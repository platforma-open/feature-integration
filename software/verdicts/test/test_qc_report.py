"""Tests for qc_report.py — per-sample QC summary metrics."""

import csv
import json
import pathlib
import re
import subprocess
import sys

import pytest
from qc_report import FIELDNAMES

SRC = pathlib.Path(__file__).parents[1] / "src" / "qc_report.py"
# The block root, for the checks that read the Tengo gather template as text.
ROOT = pathlib.Path(__file__).resolve().parents[3]


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
    # `stat[umi_col].sum()` / `.median()`. QC must report the row without crashing -- counts as the zeros
    # they are, and the median as BLANK, since no cell carries a reading to take one over and a blank and
    # a zero are opposite findings.
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n")  # header only, zero data rows
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 1000, "matched": 0}}))
    row = _run(tmp_path, tagstat, parse_report)
    assert int(row["cellsDetected"]) == 0
    assert int(row["featuresDetected"]) == 0
    assert int(row["totalUniqueUmis"]) == 0
    assert row["medianUmisPerCell"] == "", "no cell holds a reading, so there is no median to print"
    assert int(row["readsTotal"]) == 1000


def test_qc_survives_missing_refine_report(tmp_path):
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncell1\tAAAA\t1\t1\t1\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": 10, "matched": 10}}))
    row = _run(tmp_path, tagstat, parse_report, refine_report=tmp_path / "does_not_exist.json")
    assert row["panelAssignedFraction"] == ""


@pytest.mark.parametrize(
    "matched, cell_out, feature_out, expected",
    [
        (100, 100, 90, 0.9),
        (100, 100, 100, 1.0),
        # The regression case. Cell-barcode correction drops 20 of the 100 matched reads, so the antigen
        # step sees 80 and places 40. Over MATCHED reads that is 0.4. Over the step's own input it would
        # read 0.5, and every read share in the set would then sit on a denominator no other one shared.
        (100, 80, 40, 0.4),
    ],
    ids=["90pct-on-panel", "all-on-panel", "cell-loss-does-not-inflate-the-panel-share"],
)
def test_panel_assigned_fraction_is_over_matched_reads(tmp_path, matched, cell_out, feature_out, expected):
    # The FEATURE step's outputCount over MATCHED READS -- not over that step's own input. Each refine
    # level is fed from the previous level's survivors, so the step's input is post-cell-correction and
    # is a denominator no other read share here has.
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncell1\tAAAA\t1\t1\t1\n")
    parse_report = tmp_path / "parse.json"
    parse_report.write_text(json.dumps({"parseReport": {"total": matched, "matched": matched}}))
    refine_report = tmp_path / "refine.json"
    refine_report.write_text(
        json.dumps(
            _refine_report(
                [
                    {"tagName": "CELL", "inputCount": matched, "outputCount": cell_out},
                    {"tagName": "FEATURE", "inputCount": cell_out, "outputCount": feature_out},
                ]
            )
        )
    )
    row = _run(tmp_path, tagstat, parse_report, refine_report=refine_report)
    assert float(row["panelAssignedFraction"]) == pytest.approx(expected)
    # Its sibling, over the same denominator: what the antigen step could not place on the panel. The two
    # do not sum to 1 -- the cell-barcode step took its own reads in between, and that gap is the
    # information a ratio over the step's own input threw away.
    assert float(row["featureDroppedShare"]) == pytest.approx((cell_out - feature_out) / matched)


def _tagstat_lines(umi_by_cell: dict[str, int], read_by_cell: dict[str, int]) -> str:
    """One FEATURE row per cell, so group-by-CELL sums equal the given per-cell totals."""
    lines = ["CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"]
    for cell, umi in umi_by_cell.items():
        lines.append(f"{cell}\tAAAA\t{read_by_cell[cell]}\t{read_by_cell[cell]}\t{umi}\n")
    return "".join(lines)


def test_aggregate_barcode_fraction_flags_a_clear_outlier(tmp_path):
    # 20 cells spread 600..790 antigen UMIs, one at 5000. q1=650, q3=750, threshold=1050 -- above the
    # 1000-UMI floor -- so only the 5000 barcode is flagged. Its reads are 10000 of a 100000 total.
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
    # q1=12, q3=20, threshold=44 -- under the 1000-UMI floor, so nothing is flagged even though one
    # barcode (400) clears 44 on its own.
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
    # Same bed as the below-the-floor case, but a lowered --aggregate-min-umi-threshold lets the 44
    # threshold clear the floor, so the 400-UMI barcode is now flagged.
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


# --- the read-QC CSV's three-layer contract -----------------------------------------------------
#
# The per-sample CSV this module writes does NOT reach the verdict stage whole. qc-summary.tpl.tengo
# concatenates the per-sample files and carries through an EXPLICIT list of columns,
# `columnSpecs.QC_SUMMARY_COLUMNS`, injecting the real sampleId over the constant one. So a figure has
# to be declared in three places to arrive, and skipping the middle one fails in two different ways:
#
#   in FIELDNAMES only        -> the gather drops it; every reader downstream sees a missing value and
#                                reports "nothing computed this". SILENT.
#   in QC_SUMMARY_COLUMNS only -> `pt.col()` finds no such column and the gather dies at run time. LOUD.
#
# Read as TEXT, because one side is Tengo.


def _carried_through():
    """The column names qc-summary.tpl.tengo carries out of each per-sample CSV."""
    src = (ROOT / "workflow" / "src" / "column-specs.lib.tengo").read_text()
    block = src[src.index("QC_SUMMARY_COLUMNS := [") :]
    block = block[: block.index("]")]
    names = re.findall(r'"([A-Za-z]+)"', block)
    assert names, "QC_SUMMARY_COLUMNS could not be parsed; this check would pass vacuously"
    return names


def test_every_figure_the_verdict_stage_reads_survives_the_gather():
    # The silent failure, and the one worth a test. `emit_verdicts` reads the GATHERED file, so a column
    # written here but absent from the carry-through list reaches it as a missing value -- and a missing
    # value is a legitimate state, reported as "nothing computed this". Nothing crashes, nothing warns,
    # and the figure is simply never seen again.
    read_by_verdicts = set(re.findall(r'_number\(qc, "([A-Za-z]+)"\)', (SRC.parent / "emit_verdicts.py").read_text()))
    assert read_by_verdicts, "no read of the gathered row was found; this check would pass vacuously"
    carried = set(_carried_through())
    lost = sorted(read_by_verdicts - carried)
    assert not lost, f"read by the verdict stage, dropped by the gather: {lost}"


def test_the_cell_step_counts_travel_with_the_share_they_make():
    # The share and its two counts are one reading, and a reader sees them on one row. Either all three
    # arrive or the row states a share over an unstated scale.
    carried = _carried_through()
    for col in ("cellBarcodeValidFraction", "cellBarcodeIn", "cellBarcodeOut"):
        assert col in FIELDNAMES, col
        assert col in carried, col


def test_the_cell_step_share_is_derived_from_the_counts_it_reports(tmp_path):
    # One read of the refine report, one division. A second helper dividing the same two numbers itself
    # is a way for a row to disagree with its own detail line.
    tagstat = tmp_path / "ts.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\nAAA\tAgA\t5\t5\t3\n")
    parse = tmp_path / "parse.json"
    parse.write_text(json.dumps({"parseReport": {"total": 20000, "matched": 18000}}))
    refine = tmp_path / "refine.json"
    refine.write_text(json.dumps(_refine_report([{"tagName": "CELL", "inputCount": 18000, "outputCount": 17500}])))

    row = _run(tmp_path, tagstat, parse, refine_report=refine)
    assert int(row["cellBarcodeIn"]) == 18000
    assert int(row["cellBarcodeOut"]) == 17500
    assert float(row["cellBarcodeValidFraction"]) == pytest.approx(17500 / 18000)


def test_the_cell_step_counts_are_blank_without_a_step_to_read(tmp_path):
    # No CELL step in the report: the share has always come back blank, and the counts must too rather
    # than reading as zero reads entering correction.
    tagstat = tmp_path / "ts.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\nAAA\tAgA\t5\t5\t3\n")
    parse = tmp_path / "parse.json"
    parse.write_text(json.dumps({"parseReport": {"total": 20000, "matched": 18000}}))
    refine = tmp_path / "refine.json"
    refine.write_text(json.dumps(_refine_report([{"tagName": "UMI", "inputCount": 10, "outputCount": 9}])))

    row = _run(tmp_path, tagstat, parse, refine_report=refine)
    assert row["cellBarcodeValidFraction"] == ""
    assert row["cellBarcodeIn"] == ""
    assert row["cellBarcodeOut"] == ""
