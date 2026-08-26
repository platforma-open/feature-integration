import polars as pl
import pytest
from panel import (
    consistent_properties,
    default_grouping,
    identity_universe,
    offered_identities,
    panel_read_mismatch,
    property_columns,
    read_panel,
)

ROLES = {"barcode": "Sequence", "feature": "Name", "sample": "Samples"}


def _csv(tmp_path, header=("Samples", "Name", "Sequence", "Type"), *, rows):
    p = tmp_path / "panel.csv"
    p.write_text("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n")
    return str(p)


def test_read_panel_one_row_per_tag_sample(tmp_path):
    path = _csv(
        tmp_path,
        rows=[
            ["S1", "AgA", "AAAA", "Off-Target"],
            ["S2", "AgB", "AAAA", "Off-Target"],
            ["S1", "AgC", "CCCC", "Target"],
        ],
    )
    panel, dropped = read_panel(path, ROLES)
    assert panel.height == 3
    assert set(panel.columns) == {"tag", "sample", "Name", "Type"}
    assert dropped == []


def test_read_panel_without_sample_column_uses_star(tmp_path):
    path = _csv(tmp_path, header=("Name", "Sequence"), rows=[["AgA", "AAAA"], ["AgB", "CCCC"]])
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
    # Same barcode, different names across two samples' panels -- the real shape this rule exists for.
    # Names are synthetic: this repository is public.
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
    path = _csv(tmp_path, rows=[["S1", "AgA", "AAAA", "Target"], ["S1", "AgB", "AAAA", "Target"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "AAAA/S1" in str(e.value)


def test_blank_barcode_row_is_reported_not_dropped(tmp_path):
    path = _csv(
        tmp_path,
        rows=[
            ["S1", "AgA", "AAAA", "Target"],
            ["S1", "AgB", "", "Target"],
        ],
    )
    panel, dropped = read_panel(path, ROLES)
    assert panel.height == 1
    assert dropped == [3]  # CSV record ordinal, header counted (not the
    # physical line, which differs if a quoted field contains a newline)


def test_blank_sample_cell_is_fatal(tmp_path):
    path = _csv(
        tmp_path,
        rows=[
            ["S1", "AgA", "AAAA", "Target"],
            ["", "AgB", "CCCC", "Target"],
        ],
    )
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "line(s) 3." in str(e.value)


def test_trailing_blank_line_is_not_a_blank_sample_cell(tmp_path):
    # polars materializes a trailing newline as a real all-null row. A stray newline at EOF is the
    # commonest shape a panel file arrives in, and it must not read as an ambiguous sample cell.
    p = tmp_path / "panel.csv"
    p.write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\n\n")
    panel, dropped = read_panel(str(p), ROLES)
    assert panel.height == 1
    assert dropped == [3]


@pytest.mark.parametrize("bad_name", ["tag", "sample"])
def test_reserved_column_name_is_fatal(tmp_path, bad_name):
    # A NON-role column named "tag"/"sample" would be overwritten by the one this reader produces, so it
    # is refused rather than silently shadowed.
    path = _csv(tmp_path, header=("Samples", "Name", "Sequence", bad_name), rows=[["S1", "AgA", "AAAA", "x"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert f"['{bad_name}']" in str(e.value)


def test_role_column_may_be_named_tag(tmp_path):
    # emit_panel.py defaults --tag-col to "tag". A role column cannot collide: alias() replaces the source
    # column rather than duplicating it.
    path = _csv(tmp_path, header=("sample", "feature", "tag"), rows=[["S1", "AgA", "AAAA"]])
    panel, dropped = read_panel(path, {"barcode": "tag", "feature": "feature", "sample": "sample"})
    assert panel.height == 1
    assert panel["tag"].to_list() == ["AAAA"]
    assert panel["sample"].to_list() == ["S1"]
    assert dropped == []


def test_sample_role_named_tag_is_fatal(tmp_path):
    # The barcode alias runs first and would overwrite this column, leaving "sample" a silent copy of the
    # barcode -- per-sample keying gone, and no duplicate raised because the pairs stay unique.
    path = _csv(tmp_path, header=("tag", "Name", "Sequence"), rows=[["S1", "AgA", "AAAA"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, {"barcode": "Sequence", "feature": "Name", "sample": "tag"})
    assert "['tag']" in str(e.value)


def test_two_blank_barcode_rows_are_not_a_duplicate(tmp_path):
    # Both rows have tag "", so they would collide as a duplicate (tag, sample) pair if the blank-barcode
    # filter ran after the dupe check.
    path = _csv(
        tmp_path,
        rows=[
            ["S1", "AgA", "AAAA", "Target"],
            ["S1", "AgB", "", "Target"],
            ["S1", "AgC", "", "Target"],
        ],
    )
    panel, dropped = read_panel(path, ROLES)
    assert panel.height == 1
    assert dropped == [3, 4]


def test_two_roles_on_one_column_is_fatal(tmp_path):
    # Two roles on one column silently makes "sample" a copy of "tag" -- reachable from the UI today,
    # since the Sample-column dropdown is unfiltered.
    path = _csv(tmp_path, header=("Samples", "Name", "Sequence"), rows=[["S1", "AgA", "AAAA"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, {"barcode": "Sequence", "feature": "Name", "sample": "Sequence"})
    assert "column 'Sequence'" in str(e.value)


def test_missing_barcode_column_is_fatal(tmp_path):
    path = _csv(tmp_path, rows=[["S1", "AgA", "AAAA", "Target"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, {"barcode": "NoSuchCol", "feature": "Name", "sample": "Samples"})
    assert "no barcode column 'NoSuchCol'" in str(e.value)


def test_missing_sample_column_is_fatal(tmp_path):
    path = _csv(tmp_path, rows=[["S1", "AgA", "AAAA", "Target"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, {"barcode": "Sequence", "feature": "Name", "sample": "NoSuchCol"})
    assert "no sample column 'NoSuchCol'" in str(e.value)


def test_literal_row_header_is_fatal(tmp_path):
    path = _csv(tmp_path, header=("Samples", "Name", "Sequence", "_row"), rows=[["S1", "AgA", "AAAA", "x"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "['_row']" in str(e.value)


def test_feature_role_named_tag_is_fatal(tmp_path):
    # Pins that the barcode-only "tag" exemption stays narrow: a FEATURE role named "tag" is not covered.
    path = _csv(tmp_path, header=("Samples", "Sequence", "tag"), rows=[["S1", "AAAA", "AgA"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, {"barcode": "Sequence", "feature": "tag", "sample": "Samples"})
    assert "['tag']" in str(e.value)


def test_property_columns_excludes_tag_and_sample_and_preserves_order():
    # Downstream column layout depends on source order being preserved.
    panel = pl.DataFrame({"Type": ["Target"], "tag": ["AAAA"], "Name": ["AgA"], "sample": ["S1"], "Channel": ["APC"]})
    assert property_columns(panel) == ["Type", "Name", "Channel"]


def test_barcode_is_stripped(tmp_path):
    # tag equality is the join key for every later task.
    path = _csv(tmp_path, rows=[["S1", "AgA", " AAAA ", "Target"]])
    panel, _ = read_panel(path, ROLES)
    assert panel["tag"].to_list() == ["AAAA"]


def test_universe_is_every_identity_not_a_per_set_subset():
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC", "GGGG"], "sample": ["S1", "S2", "S2"], "Name": ["a", "c", "g"]})
    g = {("AAAA", "S1"): "A", ("CCCC", "S2"): "C", ("GGGG", "S2"): "G"}
    assert identity_universe(panel, g) == {"A", "C", "G"}


def test_reference_tags_never_enter_the_universe():
    panel = pl.DataFrame({"tag": ["AAAA", "CTRL"], "sample": ["S1", "S1"], "Name": ["a", "ctrl"]})
    g = default_grouping(panel, reference_tags={"CTRL"})
    assert g == {("AAAA", "S1"): "AAAA"}
    assert identity_universe(panel, g) == {"AAAA"}


def test_offered_is_the_union_over_the_sets_samples():
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC", "GGGG"], "sample": ["S1", "S2", "S2"], "Name": ["a", "c", "g"]})
    g = {("AAAA", "S1"): "A", ("CCCC", "S2"): "C", ("GGGG", "S2"): "G"}
    assert offered_identities(panel, g, ["S1"]) == {"A"}
    assert offered_identities(panel, g, ["S2"]) == {"C", "G"}
    assert offered_identities(panel, g, ["S1", "S2"]) == {"A", "C", "G"}


def test_offered_needs_only_one_member_tag():
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC"], "sample": ["S1", "S2"], "Name": ["a1", "a2"]})
    g = {("AAAA", "S1"): "A", ("CCCC", "S2"): "A"}
    assert offered_identities(panel, g, ["S1"]) == {"A"}
    assert offered_identities(panel, g, ["S2"]) == {"A"}


def test_star_sample_offers_everything():
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC"], "sample": ["*", "*"], "Name": ["a", "c"]})
    g = {("AAAA", "*"): "A", ("CCCC", "*"): "C"}
    assert offered_identities(panel, g, ["anything"]) == {"A", "C"}


def test_an_identity_not_offered_is_still_in_the_universe():
    # The universe is what makes a never-asked ROW exist. An earlier revision used `offered` as the row
    # set, so a never-offered identity vanished instead of reading "never asked".
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC"], "sample": ["S1", "S2"], "Name": ["a", "c"]})
    g = {("AAAA", "S1"): "A", ("CCCC", "S2"): "C"}
    assert "C" in identity_universe(panel, g)
    assert "C" not in offered_identities(panel, g, ["S1"])


def test_a_sample_absent_from_the_panel_is_offered_nothing():
    # A set whose cells came from a sample the panel never mentions was offered nothing, so every identity
    # reads "never asked" for it. A big claim from a silent lookup, so it is pinned here.
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    g = {("AAAA", "S1"): "A"}
    assert offered_identities(panel, g, ["S9"]) == set()


def test_a_panel_of_only_references_has_an_empty_universe():
    panel = pl.DataFrame({"tag": ["CTRL"], "sample": ["S1"], "Name": ["ctrl"]})
    g = default_grouping(panel, reference_tags={"CTRL"})
    assert g == {}
    assert identity_universe(panel, g) == set()


def test_no_samples_offers_nothing_but_the_star():
    # An empty sample list must not accidentally mean "all samples".
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    assert offered_identities(panel, {("AAAA", "S1"): "A"}, []) == set()
    star = pl.DataFrame({"tag": ["AAAA"], "sample": ["*"], "Name": ["a"]})
    assert offered_identities(star, {("AAAA", "*"): "A"}, []) == {"A"}


def test_a_tag_outside_the_grouping_is_skipped_not_an_error():
    # The reference is the ordinary case of this: it is on the panel and
    # deliberately absent from the grouping.
    panel = pl.DataFrame({"tag": ["AAAA", "ZZZZ"], "sample": ["S1", "S1"], "Name": ["a", "z"]})
    g = {("AAAA", "S1"): "A"}
    assert identity_universe(panel, g) == {"A"}
    assert offered_identities(panel, g, ["S1"]) == {"A"}


def _counts(rows):
    # House shape for a reads/counts table: one row per (sample, cell, tag) with the UMI count.
    # panel_read_mismatch only needs sampleId and tag, but the table it is handed always carries all four.
    return pl.DataFrame(
        rows, orient="row", schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64}
    )


def test_declared_tag_never_seen_is_reported_per_sample():
    panel = pl.DataFrame({"tag": ["AAAA", "GGGG"], "sample": ["S1", "S1"], "Name": ["a", "g"]})
    seen = _counts([("S1", "c1", "AAAA", 1)])
    out = panel_read_mismatch(panel, seen)
    row = out.filter(pl.col("direction") == "declared-never-seen").row(0, named=True)
    assert row["tag"] == "GGGG" and row["sample"] == "S1"


def test_undeclared_barcode_is_reported_per_sample():
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    seen = _counts([("S1", "c1", "AAAA", 1), ("S1", "c1", "TTTT", 1)])
    out = panel_read_mismatch(panel, seen)
    row = out.filter(pl.col("direction") == "undeclared-in-panel").row(0, named=True)
    assert row["tag"] == "TTTT" and row["sample"] == "S1"


def test_a_barcode_declared_in_another_sample_does_not_pass_silently():
    # The failure this whole check exists to prevent: AAAA is declared for S3 only. It is read in S1,
    # where nothing declares it. A global check would let S3's declaration excuse it there too.
    panel = pl.DataFrame({"tag": ["CCCC", "AAAA"], "sample": ["S1", "S3"], "Name": ["c", "a"]})
    seen = _counts([("S1", "c1", "CCCC", 1), ("S1", "c1", "AAAA", 1)])
    out = panel_read_mismatch(panel, seen)
    undeclared = out.filter(pl.col("direction") == "undeclared-in-panel")
    assert ("S1", "AAAA") in list(zip(undeclared["sample"], undeclared["tag"], strict=True))


def test_a_star_panel_is_satisfied_by_reads_in_any_sample():
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["*"], "Name": ["a"]})
    seen = _counts([("S1", "c1", "AAAA", 1), ("S2", "c1", "AAAA", 1)])
    assert panel_read_mismatch(panel, seen).height == 0


def test_mismatch_never_raises():
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    seen = _counts([("S9", "c1", "ZZZZ", 1)])
    rows = {(r["sample"], r["tag"], r["direction"]) for r in panel_read_mismatch(panel, seen).to_dicts()}
    assert rows == {
        ("S1", "AAAA", "declared-never-seen"),
        ("S9", "ZZZZ", "undeclared-in-panel"),
    }


def test_a_sample_with_reads_but_no_panel_rows_reports_every_barcode():
    # The panel does not cover this sample at all. Every barcode it read is undeclared -- a large claim,
    # so it must be stated rather than inferred from an empty result.
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    seen = _counts([("S9", "c1", "CCCC", 5)])
    out = panel_read_mismatch(panel, seen)
    rows = {(r["sample"], r["tag"], r["direction"]) for r in out.to_dicts()}
    assert ("S9", "CCCC", "undeclared-in-panel") in rows


def test_a_sample_in_the_panel_with_no_reads_reports_every_tag():
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    seen = _counts([("S2", "c1", "AAAA", 5)])
    out = panel_read_mismatch(panel, seen)
    rows = {(r["sample"], r["tag"], r["direction"]) for r in out.to_dicts()}
    assert ("S1", "AAAA", "declared-never-seen") in rows


def test_full_agreement_reports_nothing():
    # The empty result must mean agreement, not a check that failed to run.
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC"], "sample": ["S1", "S1"], "Name": ["a", "c"]})
    seen = _counts([("S1", "c1", "AAAA", 5), ("S1", "c1", "CCCC", 3)])
    assert panel_read_mismatch(panel, seen).height == 0


def test_empty_inputs_do_not_raise():
    panel = pl.DataFrame(
        {"tag": [], "sample": [], "Name": []}, schema={"tag": pl.String, "sample": pl.String, "Name": pl.String}
    )
    assert panel_read_mismatch(panel, _counts([])).height == 0


def test_both_directions_can_fire_for_one_sample_at_once():
    # A sample can simultaneously declare a tag it never read and read a barcode it never declared.
    # Neither direction may mask the other.
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["S1"], "Name": ["a"]})
    seen = _counts([("S1", "c1", "CCCC", 5)])
    rows = {(r["sample"], r["tag"], r["direction"]) for r in panel_read_mismatch(panel, seen).to_dicts()}
    assert ("S1", "AAAA", "declared-never-seen") in rows
    assert ("S1", "CCCC", "undeclared-in-panel") in rows


def test_a_literal_star_in_a_sample_column_is_fatal(tmp_path):
    # "*" is what the reader writes when there is no sample column. Accepting it as a sample name lets one
    # row claim every sample, and the whole panel-versus-reads check goes blind.
    path = _csv(tmp_path, rows=[["S1", "AgA", "AAAA", "T"], ["*", "AgB", "CCCC", "T"]])
    with pytest.raises(SystemExit) as e:
        read_panel(path, ROLES)
    assert "*" in str(e.value)


def test_a_mixed_star_and_named_panel_does_not_go_global():
    # Second line of defence: the reader refuses this frame, but a caller building one directly must not
    # get an empty table for a real disagreement. The output is asserted in full: a mixed frame reports
    # every row as noise, including the star row compared as a literal sample name.
    panel = pl.DataFrame({"tag": ["AAAA", "CCCC"], "sample": ["*", "S1"], "Name": ["a", "c"]})
    seen = _counts([("S1", "c1", "AAAA", 5)])
    rows = {(r["sample"], r["tag"], r["direction"]) for r in panel_read_mismatch(panel, seen).to_dicts()}
    assert rows == {
        ("*", "AAAA", "declared-never-seen"),
        ("S1", "AAAA", "undeclared-in-panel"),
        ("S1", "CCCC", "declared-never-seen"),
    }


def test_null_keys_on_either_side_do_not_raise():
    # A null tag or a null sample cannot be placed on either side of the comparison, on either input.
    # The panel side gets the same guard as the reads side.
    panel = pl.DataFrame(
        {"tag": ["AAAA", "CCCC", None], "sample": ["S1", None, "S1"], "Name": ["a", "c", "z"]},
        schema={"tag": pl.String, "sample": pl.String, "Name": pl.String},
    )
    seen = _counts([("S1", "c1", "AAAA", 5), (None, "c2", "CCCC", 3), ("S1", "c3", None, 1)])
    out = panel_read_mismatch(panel, seen)
    assert out.height == 0
    assert None not in out["sample"].to_list()
    assert None not in out["tag"].to_list()


# --- per-sample grouping (panel-file-authority@3.0) ----------------------------------------


def test_default_grouping_is_keyed_by_tag_and_sample():
    # The identity is still the tag under the per-tag grouping, but the map is keyed by the pair so every
    # consumer reads one shape whichever grouping is in force.
    panel = pl.DataFrame({"tag": ["AAAA", "AAAA"], "sample": ["S1", "S2"], "Name": ["a", "a"]})
    g = default_grouping(panel, reference_tags=set())
    assert g == {("AAAA", "S1"): "AAAA", ("AAAA", "S2"): "AAAA"}


def test_identity_universe_is_the_union_across_samples():
    # One barcode carrying a different antigen in each sample yields TWO identities, not a conflict. This
    # is the case the panel is keyed for.
    panel = pl.DataFrame({"tag": ["AAAA", "AAAA"], "sample": ["S1", "S2"], "Name": ["a", "b"]})
    g = {("AAAA", "S1"): "A", ("AAAA", "S2"): "B"}
    assert identity_universe(panel, g) == {"A", "B"}


def test_offered_reads_the_identity_the_sample_itself_declared():
    # A set drawn from S1 was offered what S1's panel said that barcode was, and nothing S2 said.
    panel = pl.DataFrame({"tag": ["AAAA", "AAAA"], "sample": ["S1", "S2"], "Name": ["a", "b"]})
    g = {("AAAA", "S1"): "A", ("AAAA", "S2"): "B"}
    assert offered_identities(panel, g, ["S1"]) == {"A"}
    assert offered_identities(panel, g, ["S2"]) == {"B"}
    assert offered_identities(panel, g, ["S1", "S2"]) == {"A", "B"}


def test_a_global_panel_row_is_offered_to_every_sample():
    # "*" is what the reader writes where the file declares no sample dimension at all, so it applies
    # everywhere rather than naming a sample.
    panel = pl.DataFrame({"tag": ["AAAA"], "sample": ["*"], "Name": ["a"]})
    g = {("AAAA", "*"): "A"}
    assert offered_identities(panel, g, ["S1"]) == {"A"}
    assert offered_identities(panel, g, ["anything"]) == {"A"}
