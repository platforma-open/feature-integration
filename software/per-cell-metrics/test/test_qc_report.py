"""Tests for qc_report.py — per-sample QC summary metrics."""

import csv
import json
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).parents[1] / "src" / "qc_report.py"


def _refine_report(steps):
    """A minimal refine-tags JSON report (mitool schema) carrying the given per-tag steps."""
    return {"inputRecords": 0, "outputRecords": 0, "steps": steps, "filterReport": None}


def _run(tmp_path, tagstat, parse_report, refine_report=None, extra=()):
    args = [
        sys.executable,
        str(SRC),
        str(tagstat),
        "--parse-report",
        str(parse_report),
        "--sample-id",
        "s1",
        "--cell-col",
        "CELL",
        "--feature-col",
        "FEATURE",
        "--umi-col",
        "unique_UMI",
        "--output",
        str(tmp_path / "result_qc.csv"),
    ]
    if refine_report is not None:
        args += ["--refine-report", str(refine_report)]
    args += list(extra)
    subprocess.run(args, check=True, cwd=tmp_path)
    with open(tmp_path / "result_qc.csv", newline="") as f:
        return next(csv.DictReader(f))


def test_qc_metrics_from_parse_report_and_tagstat(tmp_path):
    tagstat = tmp_path / "tagstat.tsv"
    # two cells. Cell1 has 2 features (3+1 UMIs), cell2 has 1 feature (4 UMIs)
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cell1\tAAAA\t7\t7\t3\n"
        "cell1\tCCCC\t1\t1\t1\n"
        "cell2\tAAAA\t9\t9\t4\n"
    )
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": 900}}))

    row = _run(tmp_path, tagstat, parse_report)
    assert int(row["readsTotal"]) == 1000
    assert int(row["readsMatched"]) == 900
    assert abs(float(row["matchedFraction"]) - 0.9) < 1e-9
    assert int(row["cellsDetected"]) == 2
    assert int(row["featuresDetected"]) == 2  # AAAA, CCCC
    assert int(row["totalUniqueUmis"]) == 8  # 3+1+4
    assert abs(float(row["medianUmisPerCell"]) - 4.0) < 1e-9  # median(4, 4)
    assert row["panelAssignedFraction"] == ""  # no refine report given


def test_qc_survives_header_only_tagstat(tmp_path):
    # Regression: a sample whose reads are all off-panel (or a wrong read geometry) yields a header-only
    # tag-stat TSV. polars then infers every column as String, and the old code crashed on
    # `stat[umi_col].sum()` / `.median()`. QC must instead report zeros without crashing -- the sibling
    # per_cell_metrics._load handles the same file (test_cli_empty_join_writes_header_only_not_crash).
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n")  # header only, zero data rows
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": 0}}))
    row = _run(tmp_path, tagstat, parse_report)
    assert int(row["cellsDetected"]) == 0
    assert int(row["featuresDetected"]) == 0
    assert int(row["totalUniqueUmis"]) == 0
    assert float(row["medianUmisPerCell"]) == 0.0
    assert int(row["readsTotal"]) == 1000


def test_qc_survives_missing_refine_report(tmp_path):
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncell1\tAAAA\t1\t1\t1\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 10, "matched": 10}}))
    row = _run(tmp_path, tagstat, parse_report, refine_report=tmp_path / "does_not_exist.json")
    assert row["panelAssignedFraction"] == ""


@pytest.mark.parametrize(
    "input_count, output_count, expected",
    [(100, 90, 0.9), (100, 100, 1.0), (200, 50, 0.25)],
    ids=["90pct-kept", "all-kept", "quarter-kept"],
)
def test_panel_assigned_fraction_from_feature_step(tmp_path, input_count, output_count, expected):
    # The FEATURE refine step's outputCount/inputCount is the fraction of reads kept after correcting
    # the feature barcode against the panel whitelist -- i.e. assigned to a panel feature.
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncell1\tAAAA\t1\t1\t1\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 100, "matched": 100}}))
    refine_report = tmp_path / "refine.json"
    refine_report.write_text(
        json.dumps(
            _refine_report(
                [
                    {"tagName": "CELL", "inputCount": input_count, "outputCount": input_count},
                    {"tagName": "FEATURE", "inputCount": input_count, "outputCount": output_count},
                ]
            )
        )
    )
    row = _run(tmp_path, tagstat, parse_report, refine_report=refine_report)
    assert float(row["panelAssignedFraction"]) == pytest.approx(expected)


def _tagstat_lines(umi_by_cell: dict[str, int], read_by_cell: dict[str, int]) -> str:
    """One FEATURE row per cell, so group-by-CELL sums equal the given per-cell totals."""
    lines = ["CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"]
    for cell, umi in umi_by_cell.items():
        lines.append(f"{cell}\tAAAA\t{read_by_cell[cell]}\t{read_by_cell[cell]}\t{umi}\n")
    return "".join(lines)


def test_aggregate_barcode_fraction_flags_a_clear_outlier(tmp_path):
    # 20 cells spread 600..790 antigen UMIs, one at 5000. q1=650, q3=750, threshold=1050 --
    # above the 1000-UMI floor -- so only the 5000 barcode is flagged. Its reads are 10000 of
    # a 100000 total, so the fraction is 0.1.
    normal = {f"c{i}": 600 + i * 10 for i in range(20)}
    umi = {**normal, "agg": 5000}
    reads = {c: v * 2 for c, v in umi.items()}
    reads["agg"] = 10_000
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(_tagstat_lines(umi, reads))
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 100_000, "matched": sum(reads.values())}}))

    row = _run(tmp_path, tagstat, parse_report)
    assert float(row["aggregateBarcodeFraction"]) == pytest.approx(0.1)
    assert int(row["aggregateBarcodesFlagged"]) == 1
    assert float(row["aggregateBarcodeThreshold"]) == pytest.approx(1050.0)


def test_aggregate_barcode_fraction_below_the_floor_is_zero(tmp_path):
    # q1=12, q3=20, threshold=44 -- under the 1000-UMI floor, so nothing is flagged even
    # though one barcode (400) clears 44 on its own.
    umi = {"c0": 10, "c1": 12, "c2": 15, "c3": 20, "c4": 400}
    reads = {c: v for c, v in umi.items()}
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(_tagstat_lines(umi, reads))
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": sum(reads.values())}}))

    row = _run(tmp_path, tagstat, parse_report)
    assert float(row["aggregateBarcodeFraction"]) == 0.0
    assert int(row["aggregateBarcodesFlagged"]) == 0
    assert float(row["aggregateBarcodeThreshold"]) == pytest.approx(44.0)


def test_aggregate_barcode_knobs_are_cli_flags(tmp_path):
    # Same bed as the below-the-floor case, but a lowered --aggregate-min-umi-threshold lets
    # the 44 threshold clear the floor, so the 400-UMI barcode is now flagged.
    umi = {"c0": 10, "c1": 12, "c2": 15, "c3": 20, "c4": 400}
    reads = {c: v for c, v in umi.items()}
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(_tagstat_lines(umi, reads))
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": sum(reads.values())}}))

    row = _run(tmp_path, tagstat, parse_report, extra=["--aggregate-min-umi-threshold", "10"])
    assert float(row["aggregateBarcodeThreshold"]) == pytest.approx(44.0)
    assert int(row["aggregateBarcodesFlagged"]) == 1


def test_aggregate_barcode_fraction_survives_header_only_tagstat(tmp_path):
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": 0}}))
    row = _run(tmp_path, tagstat, parse_report)
    assert float(row["aggregateBarcodeFraction"]) == 0.0
    assert int(row["aggregateBarcodesFlagged"]) == 0
    assert row["aggregateBarcodeThreshold"] == ""


def test_panel_assigned_fraction_blank_without_feature_step(tmp_path):
    # A refine report with no FEATURE step (e.g. CELL/UMI only) leaves the fraction blank rather than
    # reporting a wrong number.
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncell1\tAAAA\t1\t1\t1\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 10, "matched": 10}}))
    refine_report = tmp_path / "refine.json"
    refine_report.write_text(json.dumps(_refine_report([{"tagName": "CELL", "inputCount": 10, "outputCount": 10}])))
    row = _run(tmp_path, tagstat, parse_report, refine_report=refine_report)
    assert row["panelAssignedFraction"] == ""
