"""Behavioral tests for parse_gate.py (Feature Integration software).

The gate reads mitool's parse report and decides whether the per-sample mitool chain should continue
(>=1 read matched) or be skipped (0 matched → mitool wrote no parsed.mic), and synthesizes the empty
fallbacks the no-match branch feeds downstream. Stdlib only; run via the CLI like the other slow tests.
"""

import json
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).parents[1] / "src" / "parse_gate.py"


def _run(report_obj, tmp_path):
    report = tmp_path / "parse_report.json"
    report.write_text(json.dumps(report_obj))
    decision = tmp_path / "decision.json"
    tagstat = tmp_path / "tagstat_empty.tsv"
    refine = tmp_path / "refine_report_empty.json"
    r = subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(report),
            str(decision),
            "--empty-tagstat",
            str(tagstat),
            "--empty-refine-report",
            str(refine),
            "--cell-tag",
            "CELL",
            "--feature-tag",
            "FEATURE",
            "--umi-tag",
            "UMI",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(decision.read_text()), tagstat.read_text(), refine.read_text(), r.stderr


@pytest.mark.slow
def test_gate_no_match_stops_and_warns(tmp_path):
    # The real failing case: total>0 but matched==0 (wrong geometry). Gate must stop and warn.
    decision, tagstat, refine, stderr = _run({"parseReport": {"total": 74579, "matched": 0}}, tmp_path)
    assert decision == {"total": 74579, "matched": 0, "shouldContinue": False}
    # Header-only tag-stat: exactly mitool's `tag-stat -t CELL -t FEATURE -u UMI` columns, no data rows.
    assert tagstat == "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
    assert refine == "{}"
    assert "matched 0 of 74579 reads" in stderr


@pytest.mark.slow
def test_gate_match_continues(tmp_path):
    decision, tagstat, refine, stderr = _run({"parseReport": {"total": 100, "matched": 42}}, tmp_path)
    assert decision == {"total": 100, "matched": 42, "shouldContinue": True}
    # Fallbacks are written unconditionally (ignored on the matched>0 path) and are well-formed.
    assert tagstat == "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
    assert refine == "{}"
    assert stderr == ""


@pytest.mark.slow
def test_gate_tolerates_unwrapped_report(tmp_path):
    # Defensive: an unwrapped {total, matched} (no "parseReport" envelope) is still read correctly.
    decision, _tagstat, _refine, _stderr = _run({"total": 10, "matched": 3}, tmp_path)
    assert decision == {"total": 10, "matched": 3, "shouldContinue": True}
