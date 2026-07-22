"""Tests for emit_csv_meta.py — CSV headers + per-column distinct values for the D4 dropdowns."""

import json
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).parents[1] / "src" / "emit_csv_meta.py"


def _run(tmp_path, text):
    csv = tmp_path / "tags.csv"
    csv.write_text(text)
    out = tmp_path / "meta.json"
    subprocess.run([sys.executable, str(SRC), str(csv), str(out)], check=True)
    return json.loads(out.read_text())


def test_columns_in_header_order(tmp_path):
    meta = _run(tmp_path, "barcode,antigen,pool\nAAAA,AgX,p1\nCCCC,AgY,p1\n")
    assert meta["columns"] == ["barcode", "antigen", "pool"]


def test_values_by_column_deduped_and_sorted(tmp_path):
    # Each column's distinct values, sorted — the control dropdown reads valuesByColumn[<feature col>].
    meta = _run(tmp_path, "barcode,antigen,pool\nAAAA,AgY,p1\nCCCC,AgX,p1\nGGGG,AgX,p2\n")
    assert meta["valuesByColumn"]["antigen"] == ["AgX", "AgY"]  # deduped (AgX twice), sorted
    assert meta["valuesByColumn"]["pool"] == ["p1", "p2"]
    assert meta["valuesByColumn"]["barcode"] == ["AAAA", "CCCC", "GGGG"]


def test_row_count_counts_data_rows(tmp_path):
    # rowCount is the number of data rows (header excluded) — the model compares it against a column's
    # distinct-value count to detect a barcode mapped on more than one row.
    meta = _run(tmp_path, "barcode,antigen\nAAAA,AgX\nCCCC,AgY\nGGGG,AgZ\n")
    assert meta["rowCount"] == 3
    # No duplicate barcodes: distinct barcode count equals rowCount.
    assert len(meta["valuesByColumn"]["barcode"]) == meta["rowCount"]


def test_row_count_ignores_trailing_blank_rows(tmp_path):
    meta = _run(tmp_path, "barcode,antigen\nAAAA,AgX\n\n,\n")
    assert meta["rowCount"] == 1


def test_row_count_exceeds_distinct_when_barcode_duplicated(tmp_path):
    # Same barcode on two rows (sample-specific mapping): distinct barcode count < rowCount.
    meta = _run(tmp_path, "barcode,antigen\nAAAA,AgX\nAAAA,AgY\n")
    assert meta["rowCount"] == 2
    assert len(meta["valuesByColumn"]["barcode"]) < meta["rowCount"]


def test_blank_cells_ignored(tmp_path):
    meta = _run(tmp_path, "barcode,antigen\nAAAA,AgX\nCCCC,\n")
    assert meta["valuesByColumn"]["antigen"] == ["AgX"]  # the empty cell is not a value


def test_header_only_csv_emits_empty_value_lists(tmp_path):
    meta = _run(tmp_path, "barcode,antigen\n")
    assert meta["columns"] == ["barcode", "antigen"]
    assert meta["valuesByColumn"] == {"barcode": [], "antigen": []}


def test_empty_or_headerless_csv_errors(tmp_path):
    csv = tmp_path / "empty.csv"
    csv.write_text("")
    out = tmp_path / "meta.json"
    r = subprocess.run([sys.executable, str(SRC), str(csv), str(out)])
    assert r.returncode != 0
