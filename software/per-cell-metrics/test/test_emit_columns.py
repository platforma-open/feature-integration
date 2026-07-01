"""Tests for emit_columns.py — CSV header list for the column-mapping dropdowns."""

import json
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).parents[1] / "src" / "emit_columns.py"


def test_emits_header_names_in_order(tmp_path):
    csv = tmp_path / "tags.csv"
    csv.write_text("barcode,antigen,pool\nAAAA,AgX,p1\nCCCC,AgY,p1\n")
    out = tmp_path / "cols.json"
    subprocess.run([sys.executable, str(SRC), str(csv), str(out)], check=True)
    assert json.loads(out.read_text()) == ["barcode", "antigen", "pool"]


def test_empty_or_headerless_csv_errors(tmp_path):
    csv = tmp_path / "empty.csv"
    csv.write_text("")
    out = tmp_path / "cols.json"
    r = subprocess.run([sys.executable, str(SRC), str(csv), str(out)])
    assert r.returncode != 0
