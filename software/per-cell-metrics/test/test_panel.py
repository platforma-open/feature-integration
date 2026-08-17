import polars as pl
import pytest
from panel import consistent_properties, read_panel

ROLES = {"barcode": "Sequence", "feature": "Name", "sample": "Samples"}


def _csv(tmp_path, rows, header):
    p = tmp_path / "panel.csv"
    p.write_text("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n")
    return str(p)


def test_read_panel_one_row_per_tag_sample(tmp_path):
    path = _csv(
        tmp_path,
        [
            ["S1", "AgA", "AAAA", "Off-Target"],
            ["S2", "AgB", "AAAA", "Off-Target"],
            ["S1", "AgC", "CCCC", "Target"],
        ],
        ["Samples", "Name", "Sequence", "Type"],
    )
    panel, dropped = read_panel(path, ROLES)
    assert panel.height == 3
    assert set(panel.columns) >= {"tag", "sample", "Name", "Type"}
    assert dropped == []


def test_read_panel_without_sample_column_uses_star(tmp_path):
    path = _csv(tmp_path, [["AgA", "AAAA"], ["AgB", "CCCC"]], ["Name", "Sequence"])
    panel, _ = read_panel(path, {"barcode": "Sequence", "feature": "Name", "sample": ""})
    assert panel["sample"].unique().to_list() == ["*"]


def test_consistent_properties_keeps_agreeing_values():
    panel = pl.DataFrame(
        {
            "tag": ["AAAA", "AAAA"],
            "sample": ["S1", "S2"],
            "Name": ["AgA", "AgA"],
            "Channel": ["PE", "PE"],
        }
    )
    props, bad = consistent_properties(panel, ["Name", "Channel"])
    assert props["AAAA"] == {"Name": "AgA", "Channel": "PE"}
    assert bad == []


def test_consistent_properties_drops_disagreeing_and_reports_it():
    # Same barcode, different names across two samples' panels — the real shape
    # this rule exists for. Names are synthetic: this repository is public.
    panel = pl.DataFrame(
        {
            "tag": ["AAAA", "AAAA"],
            "sample": ["S1", "S2"],
            "Name": ["AgA", "AgB"],
            "Channel": ["APC", "APC"],
        }
    )
    props, bad = consistent_properties(panel, ["Name", "Channel"])
    assert props["AAAA"] == {"Channel": "APC"}
    assert bad == [("AAAA", "Name", ["AgA", "AgB"])]


def test_consistent_properties_ignores_blanks():
    panel = pl.DataFrame({"tag": ["AAAA", "AAAA"], "sample": ["S1", "S2"], "Name": ["AgA", ""]})
    props, bad = consistent_properties(panel, ["Name"])
    assert props["AAAA"] == {"Name": "AgA"}
    assert bad == []


def test_duplicate_tag_sample_pair_is_fatal(tmp_path):
    path = _csv(
        tmp_path,
        [["S1", "AgA", "AAAA", "Target"], ["S1", "AgB", "AAAA", "Target"]],
        ["Samples", "Name", "Sequence", "Type"],
    )
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "AAAA" in str(e.value)


def test_blank_barcode_row_is_reported_not_dropped(tmp_path):
    path = _csv(
        tmp_path,
        [
            ["S1", "AgA", "AAAA", "Target"],
            ["S1", "AgB", "", "Target"],
        ],
        ["Samples", "Name", "Sequence", "Type"],
    )
    panel, dropped = read_panel(path, ROLES)
    assert panel.height == 1
    assert dropped == [3]  # CSV record ordinal, header counted (not the
    # physical line, which differs if a quoted field contains a newline)


def test_blank_sample_cell_is_fatal(tmp_path):
    path = _csv(
        tmp_path,
        [
            ["S1", "AgA", "AAAA", "Target"],
            ["", "AgB", "CCCC", "Target"],
        ],
        ["Samples", "Name", "Sequence", "Type"],
    )
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "3" in str(e.value)


def test_trailing_blank_line_is_not_a_blank_sample_cell(tmp_path):
    # polars materializes a trailing newline as a real all-null row. A stray
    # newline at EOF is the commonest shape a panel file arrives in; it must
    # not read as an ambiguous sample cell.
    p = tmp_path / "panel.csv"
    p.write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\n\n")
    panel, dropped = read_panel(str(p), ROLES)
    assert panel.height == 1
    assert dropped == [3]


def test_reserved_column_name_is_fatal(tmp_path):
    path = _csv(tmp_path, [["S1", "AgA", "AAAA", "x"]], ["Samples", "Name", "Sequence", "tag"])
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "tag" in str(e.value)
