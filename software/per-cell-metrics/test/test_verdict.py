import polars as pl
from verdict import (
    DEFAULT_FLOOR,
    DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE,
    DEFAULT_PANEL_MIN_MEMBERS,
    DEFAULT_REFERENCE_THIN_LINE,
    ReferenceChoice,
    apply_floor,
    gate_cells,
    reference_by_cell,
    resolve_default_source,
)


def _counts(rows):
    return pl.DataFrame(
        rows, orient="row", schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64}
    )


def test_default_floor_is_four():
    assert DEFAULT_FLOOR == 4


def test_counts_below_the_floor_become_zero():
    df = _counts([("S1", "c1", "AAAA", 3), ("S1", "c1", "CCCC", 4)])
    out, stats = apply_floor(df, floor=4, reference_tags=set())
    assert out.sort("tag")["umiCount"].to_list() == [0, 4]
    assert stats["readingsFloored"] == 1


def test_floor_is_per_cell_and_tag_not_per_cell_total():
    df = _counts([("S1", "c1", "AAAA", 3), ("S1", "c1", "CCCC", 3)])
    out, stats = apply_floor(df, floor=4, reference_tags=set())
    assert out["umiCount"].to_list() == [0, 0]
    assert stats["readingsFloored"] == 2


def test_reference_tags_are_never_floored():
    df = _counts([("S1", "c1", "CTRL", 1), ("S1", "c1", "AAAA", 1)])
    out, _ = apply_floor(df, floor=4, reference_tags={"CTRL"})
    got = dict(zip(out["tag"].to_list(), out["umiCount"].to_list(), strict=True))
    assert got["CTRL"] == 1  # the comparator is not evidence of binding
    assert got["AAAA"] == 0


def test_cells_left_with_nothing_are_counted():
    df = _counts([("S1", "c1", "AAAA", 1), ("S1", "c2", "AAAA", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 1


def test_the_floor_zeroes_readings_it_never_drops_rows():
    df = _counts([("S1", "c1", "AAAA", 9), ("S2", "c1", "AAAA", 9)])
    out, _ = apply_floor(df, floor=4, reference_tags=set())
    assert out.height == 2


def test_floor_of_zero_removes_nothing():
    df = _counts([("S1", "c1", "AAAA", 1)])
    out, stats = apply_floor(df, floor=0, reference_tags=set())
    assert out["umiCount"].to_list() == [1]
    assert stats["readingsFloored"] == 0


def test_count_exactly_at_the_floor_survives():
    df = _counts([("S1", "c1", "AAAA", 4)])
    out, _ = apply_floor(df, floor=4, reference_tags=set())
    assert out["umiCount"].to_list() == [4]


def test_a_cell_holding_only_the_reference_is_not_emptied():
    # Its non-reference readings are absent, not zeroed. "Emptied" means the
    # floor took a cell's evidence away, not that it never had any.
    df = _counts([("S1", "c1", "CTRL", 1)])
    _, stats = apply_floor(df, floor=4, reference_tags={"CTRL"})
    assert stats["cellsEmptied"] == 0


def test_a_cell_keeping_one_reading_is_not_emptied():
    df = _counts([("S1", "c1", "AAAA", 1), ("S1", "c1", "CCCC", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 0
    assert stats["readingsFloored"] == 1


def test_the_same_cell_id_in_two_samples_empties_independently():
    # (sampleId, cellId) is the key. Keying on cellId alone would let S2's
    # surviving reading rescue S1's emptied cell.
    df = _counts([("S1", "c1", "AAAA", 1), ("S2", "c1", "AAAA", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 1


def test_a_disabled_floor_is_a_no_op_even_for_a_zero_reading():
    # floor <= 0 returns early, and that early return is behavioural rather
    # than an optimisation: falling through would count a cell whose only
    # reading is already 0 as "emptied", when the floor removed nothing.
    df = _counts([("S1", "c1", "AAAA", 0)])
    out, stats = apply_floor(df, floor=0, reference_tags=set())
    assert out["umiCount"].to_list() == [0]
    assert stats == {"readingsFloored": 0, "cellsEmptied": 0}


def test_an_empty_frame_floors_to_nothing():
    df = _counts([])
    out, stats = apply_floor(df, floor=4, reference_tags=set())
    assert out.height == 0
    assert stats == {"readingsFloored": 0, "cellsEmptied": 0}


def test_a_reading_that_was_already_zero_still_counts_as_evidence_lost():
    # Pins the deliberate asymmetry between had_evidence and kept_evidence.
    # had_evidence must NOT filter on > 0: on the sparse frame this step is
    # contracted to receive, every row is an observed reading, so a row's
    # existence is what makes a cell one that had evidence. Adding "> 0" to
    # had_evidence is a no-op on real input and silently changes this count
    # once densified zeros exist — which is the reason densify runs after.
    df = _counts([("S1", "c1", "AAAA", 0)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 1


def test_default_source_is_declared_where_a_reference_tag_exists():
    assert resolve_default_source({"CTRL"}) is ReferenceChoice.DECLARED


def test_default_source_never_upgrades_itself():
    assert resolve_default_source(set()) is ReferenceChoice.NONE


def test_empty_droplets_is_not_offered():
    assert not hasattr(ReferenceChoice, "EMPTY_DROPLETS")


def test_several_reference_tags_combine_by_the_highest():
    counts = _counts([("S1", "c1", "CTRL1", 3), ("S1", "c1", "CTRL2", 11)])
    ref, _ = reference_by_cell(counts, {"CTRL1", "CTRL2"}, ReferenceChoice.DECLARED)
    assert ref[("S1", "c1")] == 11  # not 3, not arbitrary


def test_cell_missing_the_reference_tag_reads_zero():
    counts = _counts([("S1", "c1", "CTRL", 5), ("S1", "c2", "AAAA", 9)])
    ref, _ = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.DECLARED)
    assert ref[("S1", "c2")] == 0


def test_panel_source_refuses_below_the_minimum():
    counts = _counts([("S1", "c1", "AAAA", 9)])
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=2, min_members=5)
    assert choice is ReferenceChoice.NONE and ref == {}


def test_panel_source_serves_when_big_enough():
    counts = _counts([("S1", "c1", "AAAA", 9), ("S1", "c1", "CCCC", 1)])
    _, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=8, min_members=5)
    assert choice is ReferenceChoice.PANEL


def test_source_none_yields_no_comparator():
    counts = _counts([("S1", "c1", "AAAA", 9)])
    ref, choice = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.NONE)
    assert choice is ReferenceChoice.NONE and ref == {}


def test_defaults_are_named_not_magic():
    # Pins the actual shipped values, not just their sign. These are
    # user-facing numbers that appear in a dropdown and change what the block
    # produces, so an edit to any of them must be a deliberate, visible act —
    # not a silent one that only this test would otherwise catch. The values
    # themselves are not calibrated against real data.
    assert DEFAULT_PANEL_MIN_MEMBERS > 0
    assert DEFAULT_REFERENCE_THIN_LINE >= 0
    assert DEFAULT_PANEL_MIN_MEMBERS == 8
    assert DEFAULT_REFERENCE_THIN_LINE == 2
    assert DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE == 100


def test_gate_defaults_off_but_still_measures_exposure():
    ref = {("S1", "c1"): 5000, ("S1", "c2"): 1}
    aside, high = gate_cells(ref, threshold=None, observation_line=100)
    assert aside == set() and high == 1


def test_declared_gate_sets_aside_and_counts():
    ref = {("S1", "c1"): 900, ("S1", "c2"): 2}
    aside, high = gate_cells(ref, threshold=100, observation_line=100)
    assert aside == {("S1", "c1")} and high == 1


def test_panel_source_serves_exactly_at_the_minimum():
    # The minimum is a floor, not a gap: a panel of exactly min_members is
    # large enough. Nothing else in the suite distinguishes < from <=.
    counts = _counts([("S1", "c1", "AAAA", 9), ("S1", "c1", "CCCC", 1)])
    _, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=5, min_members=5)
    assert choice is ReferenceChoice.PANEL


def test_panel_source_refuses_one_below_the_minimum():
    counts = _counts([("S1", "c1", "AAAA", 9)])
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=4, min_members=5)
    assert choice is ReferenceChoice.NONE


def test_the_gate_boundary_includes_the_line_itself():
    # A reading exactly at the threshold is high: the named value satisfies the
    # condition it names, matching the floor (a count of exactly `floor` is
    # evidence) and the panel minimum (exactly `min_members` is large enough).
    # Both sides are pinned so that changing the comparison is a deliberate act.
    at_line = {("S1", "c1"): 100}
    just_below = {("S1", "c2"): 99}
    aside_at, high_at = gate_cells(at_line, threshold=100, observation_line=100)
    aside_below, high_below = gate_cells(just_below, threshold=100, observation_line=100)
    assert aside_at == {("S1", "c1")} and high_at == 1
    assert aside_below == set() and high_below == 0


def test_high_reference_counting_is_independent_of_the_gate_acting():
    # The exposure count must not quietly become "cells the gate removed".
    ref = {("S1", "c1"): 500, ("S1", "c2"): 1}
    _, high_off = gate_cells(ref, threshold=None, observation_line=100)
    _, high_on = gate_cells(ref, threshold=100, observation_line=100)
    assert high_off == high_on == 1


def test_a_source_that_cannot_be_served_falls_to_none_and_never_sideways():
    # The served choice may only move down to NONE. Substituting a different
    # comparator would silently answer a question the scientist did not ask,
    # and two runs served by different comparators are not comparable.
    counts = _counts([("S1", "c1", "AAAA", 9), ("S1", "c1", "CCCC", 3)])
    # DECLARED with nothing declared: not PANEL, even though a panel exists.
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.DECLARED, panel_size=100, min_members=5)
    assert choice is ReferenceChoice.NONE
    assert ref == {}
    # PANEL below the minimum: not DECLARED, even though a reference tag exists.
    ref2, choice2 = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.PANEL, panel_size=1, min_members=5)
    assert choice2 is ReferenceChoice.NONE
    assert ref2 == {}


def test_the_panel_comparator_is_the_median_not_the_mean():
    # A cell with one strong binder: the mean is dragged up by it, the median
    # is not. The comparator is meant to stand for the cell's background, so a
    # single high reading must not raise the bar it is measured against.
    counts = _counts(
        [
            ("S1", "c1", "AAAA", 1),
            ("S1", "c1", "CCCC", 2),
            ("S1", "c1", "GGGG", 3),
            ("S1", "c1", "TTTT", 200),
        ]
    )
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=8, min_members=5)
    assert choice is ReferenceChoice.PANEL
    assert ref[("S1", "c1")] == 2  # median of 1,2,3,200 -> 2.5 -> int 2; mean would be 51
