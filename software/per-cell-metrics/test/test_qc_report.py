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


def _run(tmp_path, tagstat, parse_report, refine_report=None):
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
    subprocess.run(args, check=True, cwd=tmp_path)
    with open(tmp_path / "result_qc.csv", newline="") as f:
        return next(csv.DictReader(f))


def test_qc_metrics_from_parse_report_and_tagstat(tmp_path):
    tagstat = tmp_path / "tagstat.tsv"
    # two cells; cell1 has 2 features (3+1 UMIs), cell2 has 1 feature (4 UMIs)
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
