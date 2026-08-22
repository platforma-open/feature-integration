"""Tests for emit_feature_properties.py -- generic per-feature property import from the tag CSV.

Two layers: subprocess tests over the CLI (wide CSV + meta JSON) and direct unit tests of the pure
``parse_properties`` collapse (dedup + distinct-value collection).
"""

import csv
import io
import json
import pathlib
import subprocess
import sys

from emit_feature_properties import parse_properties

SRC = pathlib.Path(__file__).parents[1] / "src" / "emit_feature_properties.py"


def _run(tmp_path, text, *extra):
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text(text)
    subprocess.run(
        [sys.executable, str(SRC), str(csv_path), "--output-prefix", str(tmp_path / "r"), *extra],
        check=True,
    )
    meta = json.loads((tmp_path / "r_feature_property_meta.json").read_text())
    with open(tmp_path / "r_feature_properties.csv", newline="") as fh:
        rows = list(csv.reader(fh))
    return meta, rows


def test_extra_columns_imported_keyed_by_feature(tmp_path):
    meta, rows = _run(
        tmp_path,
        "tag,feature,antigen_type,species\nAAAA,AGX,protein,human\nCCCC,BGX,peptide,cyno\nGGGG,CTRL,control,human\n",
    )
    # Only the two extra columns become properties (tag + feature are roles).
    assert meta["columns"] == ["antigen_type", "species"]
    assert meta["valuesByColumn"]["species"] == ["cyno", "human"]
    assert meta["valuesByColumn"]["antigen_type"] == ["control", "peptide", "protein"]
    # Wide CSV: feature renamed to 'feature', one row per feature, sorted, properties under their headers.
    assert rows[0] == ["feature", "antigen_type", "species"]
    assert rows[1:] == [
        ["AGX", "protein", "human"],
        ["BGX", "peptide", "cyno"],
        ["CTRL", "control", "human"],
    ]


def test_no_extra_columns_yields_empty_meta(tmp_path):
    # The current shipped fixture shape (tag,feature) has no properties: empty columns, feature-only CSV.
    meta, rows = _run(tmp_path, "tag,feature\nAAAA,AGX\nCCCC,BGX\n")
    assert meta["columns"] == []
    assert meta["valuesByColumn"] == {}
    assert rows[0] == ["feature"]
    assert rows[1:] == [["AGX"], ["BGX"]]


def test_many_barcodes_one_feature_deduped(tmp_path):
    # A feature reached by several barcodes appears once. Its (consistent) property is carried through.
    meta, rows = _run(tmp_path, "tag,feature,species\nAAAA,AGX,human\nTTTT,AGX,human\nCCCC,BGX,cyno\n")
    assert meta["columns"] == ["species"]
    assert rows[1:] == [["AGX", "human"], ["BGX", "cyno"]]


def test_sample_column_excluded(tmp_path):
    # Sample-aware mapping: the sample column is a role, not a property.
    meta, rows = _run(
        tmp_path,
        "tag,feature,sample,species\nAAAA,AGX,s1,human\nCCCC,BGX,s2,cyno\n",
        "--sample-col",
        "sample",
    )
    assert meta["columns"] == ["species"]
    assert rows[0] == ["feature", "species"]


def test_custom_role_column_names(tmp_path):
    # Roles are configurable: whichever columns the user maps are excluded. The rest pass through.
    meta, _ = _run(
        tmp_path,
        "barcode,antigen,pool\nAAAA,AgX,p1\nCCCC,AgY,p2\n",
        "--csv-barcode-col",
        "barcode",
        "--csv-feature-col",
        "antigen",
    )
    assert meta["columns"] == ["pool"]
    assert meta["valuesByColumn"]["pool"] == ["p1", "p2"]


def test_header_order_preserved(tmp_path):
    meta, rows = _run(tmp_path, "tag,feature,zeta,alpha\nAAAA,AGX,z,a\n")
    assert meta["columns"] == ["zeta", "alpha"]
    assert rows[0] == ["feature", "zeta", "alpha"]


def test_missing_role_column_errors(tmp_path):
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text("tag,feature\nAAAA,AGX\n")
    r = subprocess.run(
        [sys.executable, str(SRC), str(csv_path), "--csv-feature-col", "nope", "--output-prefix", str(tmp_path / "r")]
    )
    assert r.returncode != 0


def _control_rows(tmp_path):
    with open(tmp_path / "r_negative_control.csv", newline="") as fh:
        return list(csv.reader(fh))


def test_control_feature_marker_emitted(tmp_path):
    # --control-feature marks that feature "true" in the dedicated negative-control marker CSV, so the
    # workflow surfaces it on the feature axis for VDJ Multiomic Integration to exclude from its metrics.
    _run(tmp_path, "tag,feature\nAAAA,AGX\nGGGG,CTRL\n", "--control-feature", "CTRL")
    assert _control_rows(tmp_path) == [["feature", "value"], ["CTRL", "true"]]


def test_no_control_feature_marker_header_only(tmp_path):
    # No control designated -> marker CSV is header-only (no feature marked as the control).
    _run(tmp_path, "tag,feature\nAAAA,AGX\nGGGG,BGX\n")
    assert _control_rows(tmp_path) == [["feature", "value"]]


def _rows(text):
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    col_index = {h.strip(): i for i, h in enumerate(header)}
    return list(reader), col_index


# --- pure parse_properties unit tests ---


def test_parse_first_nonempty_wins_and_notes_conflict():
    rows, col_index = _rows("feature,species\nAGX,human\nAGX,cyno\n")
    by_feature, values = parse_properties(rows, col_index, "feature", ["species"])
    assert by_feature["AGX"]["species"] == "human"  # first wins
    assert values["species"] == {"human", "cyno"}  # both recorded as distinct


def test_parse_blank_cells_ignored():
    rows, col_index = _rows("feature,species\nAGX,\nBGX,human\n")
    by_feature, values = parse_properties(rows, col_index, "feature", ["species"])
    assert by_feature["AGX"] == {}  # blank -> no property recorded
    assert by_feature["BGX"]["species"] == "human"
    assert values["species"] == {"human"}


def test_parse_blank_feature_row_skipped():
    rows, col_index = _rows("feature,species\n,human\nAGX,cyno\n")
    by_feature, _ = parse_properties(rows, col_index, "feature", ["species"])
    assert list(by_feature) == ["AGX"]
