import math
import random

import polars as pl
import pytest
from scipy.stats import beta
from verdict import (
    BOUND_CUTOFF,
    DEFAULT_FLOOR,
    DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE,
    DEFAULT_PANEL_MIN_MEMBERS,
    Admissibility,
    ReferenceChoice,
    State,
    UnreliableReason,
    apply_floor,
    combine_tags_to_identities,
    densify,
    gate_cells,
    read_states,
    reference_by_cell,
    served_source,
    silent_tally,
    specificity_score,
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


def test_nothing_here_picks_a_rung():
    # `resolve_default_source` used to, and its removal is the point rather than
    # a cleanup. `what-plays-the-baseline` requires the scientist to select among
    # the rungs and requires that nothing selects for them.
    #
    # A tripwire, not a permanent ban, and the same shape as the empty-droplets
    # one below: if a default is ever wanted again it should be a deliberate act
    # that deletes this test, not a helper that reappears in the layer furthest
    # from the reader. The workflow omits --reference-source when the model's
    # value is empty, so anything here that could pick a rung becomes the live
    # rule the moment the model stops picking one.
    import verdict

    assert not hasattr(verdict, "resolve_default_source")


def test_a_choice_that_cannot_serve_drops_to_none_and_never_to_another_rung():
    # `served_source` survived the removal above and is a different thing: it
    # never picks a rung, it only reports that the one asked for cannot serve.
    assert served_source(ReferenceChoice.DECLARED, set(), 40, 25) is ReferenceChoice.NONE
    assert served_source(ReferenceChoice.PANEL, {"CTRL"}, 3, 25) is ReferenceChoice.NONE
    assert served_source(ReferenceChoice.DECLARED, {"CTRL"}, 3, 25) is ReferenceChoice.DECLARED


def test_empty_droplets_is_not_offered():
    # A tripwire, not a permanent ban: the day this block genuinely receives
    # gene expression and an empty-droplet population, EMPTY_DROPLETS gets
    # implemented and this test is deleted, not fixed.
    assert not hasattr(ReferenceChoice, "EMPTY_DROPLETS")


def test_several_reference_tags_combine_by_the_highest():
    counts = _counts([("S1", "c1", "CTRL1", 3), ("S1", "c1", "CTRL2", 11)])
    ref, _ = reference_by_cell(counts, {"CTRL1", "CTRL2"}, ReferenceChoice.DECLARED)
    assert ref[("S1", "c1")] == 11  # not 3, not arbitrary


def test_cell_missing_the_reference_tag_reads_zero():
    counts = _counts([("S1", "c1", "CTRL", 5), ("S1", "c2", "AAAA", 9)])
    ref, _ = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.DECLARED)
    assert ref[("S1", "c2")] == 0


def test_source_none_yields_no_comparator():
    counts = _counts([("S1", "c1", "AAAA", 9)])
    ref, choice = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.NONE)
    assert choice is ReferenceChoice.NONE and ref == {}


def test_shipped_defaults_are_pinned():
    # These are user-facing numbers that appear in a dropdown and change what
    # the block produces, so an edit to any of them must be a deliberate,
    # visible act — not a silent one that only this test would otherwise
    # catch. The high-reference line is not calibrated against real data.
    #
    # The panel minimum is different in kind: it GATES the rung rather than
    # tuning it, and it comes from one preprint whose own panels held fifty and
    # a hundred members. It was 8, which no source supports. At 25 the rung is
    # out of reach of any antibody panel, since those kits cap at fifteen tags,
    # and such a panel falls to the tag-distribution rung instead.
    assert DEFAULT_PANEL_MIN_MEMBERS == 25
    assert DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE == 100


def test_panel_source_serves_exactly_at_the_minimum():
    # The minimum is a floor, not a gap: a panel of exactly min_members is
    # large enough. Nothing else in the suite distinguishes < from <=.
    counts = _counts([("S1", "c1", "AAAA", 9), ("S1", "c1", "CCCC", 1)])
    _, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=5, min_members=5)
    assert choice is ReferenceChoice.PANEL


def test_panel_source_refuses_one_below_the_minimum():
    counts = _counts([("S1", "c1", "AAAA", 9)])
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=4, min_members=5)
    assert choice is ReferenceChoice.NONE and ref == {}


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


def test_the_observation_line_is_independent_of_the_gate_threshold():
    # The two lines are separate parameters and must be given separate values
    # here: with them equal, no assertion can tell whether the exposure count
    # follows the observation line or the gate. It must follow the observation
    # line, because it measures how many cells sat in high background — true
    # whether or not the gate removed any of them.
    ref = {("S1", "a"): 500, ("S1", "b"): 50, ("S1", "c"): 2000}

    aside_off, high_off = gate_cells(ref, threshold=None, observation_line=100)
    assert aside_off == set() and high_off == 2

    # Gate stricter than the observation line: fewer set aside, same exposure.
    aside_hi, high_hi = gate_cells(ref, threshold=1000, observation_line=100)
    assert aside_hi == {("S1", "c")} and high_hi == 2

    # Gate looser than the observation line: more set aside, same exposure.
    aside_lo, high_lo = gate_cells(ref, threshold=10, observation_line=100)
    assert aside_lo == {("S1", "a"), ("S1", "b"), ("S1", "c")} and high_lo == 2


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


def test_the_panel_median_truncates_rather_than_rounds():
    # A median of 1.5 is the value that separates the two: truncation gives 1,
    # and polars' round-half-to-even gives 2. At a median of 2.5 both give 2,
    # so a fixture there cannot tell them apart — and the difference matters,
    # because a comparator of 1 and one of 2 give different scores. Truncation is
    # the behaviour; this pins it.
    counts = _counts(
        [
            ("S1", "c1", "AAAA", 1),
            ("S1", "c1", "CCCC", 1),
            ("S1", "c1", "GGGG", 2),
            ("S1", "c1", "TTTT", 2),
        ]
    )
    ref, choice = reference_by_cell(counts, set(), ReferenceChoice.PANEL, panel_size=8, min_members=5)
    assert choice is ReferenceChoice.PANEL
    assert ref[("S1", "c1")] == 1


def test_an_explicit_empty_cell_list_means_no_cells():
    # Not "derive them from the counts frame". An empty list is a statement.
    counts = _counts([("S1", "c1", "CTRL", 7)])
    ref, choice = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.DECLARED, cells=[])
    assert choice is ReferenceChoice.DECLARED
    assert ref == {}


def test_cells_outside_the_given_list_are_excluded():
    # The cell list is the analysis. A cell with a real reference reading that
    # is not in it has a comparator nobody will consult, and returning it would
    # invite a reader to treat the result as the cell universe.
    counts = _counts([("S1", "c1", "CTRL", 7), ("S1", "c2", "CTRL", 9)])
    ref, _ = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.DECLARED, cells=[("S1", "c1")])
    assert ref == {("S1", "c1"): 7}


def test_a_named_cell_with_no_reference_reading_is_zero_not_missing():
    # Both directions in one assertion: c2 is added at 0, c3 is excluded.
    counts = _counts([("S1", "c1", "CTRL", 7), ("S1", "c3", "CTRL", 4)])
    ref, _ = reference_by_cell(counts, {"CTRL"}, ReferenceChoice.DECLARED, cells=[("S1", "c1"), ("S1", "c2")])
    assert ref == {("S1", "c1"): 7, ("S1", "c2"): 0}


def test_the_panel_source_also_respects_the_given_cell_list():
    # Two branches now share the cell-list rule; only one is covered above.
    counts = _counts([("S1", "c1", "AAAA", 9), ("S1", "c2", "AAAA", 3)])
    ref, choice = reference_by_cell(
        counts, set(), ReferenceChoice.PANEL, cells=[("S1", "c1")], panel_size=8, min_members=5
    )
    assert choice is ReferenceChoice.PANEL
    assert ref == {("S1", "c1"): 9}


def _ident(rows):
    return pl.DataFrame(
        rows,
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "identity": pl.String, "umiCount": pl.Int64},
    )


def _cells(pairs):
    return pl.DataFrame(pairs, orient="row", schema={"sampleId": pl.String, "cellId": pl.String})


def test_state_has_exactly_four_members():
    assert {s.value for s in State} == {"bound", "not bound", "never asked", "unreliable"}


def test_densify_gives_a_silent_cell_a_real_zero():
    counts = _ident([("S1", "c1", "A", 7)])
    cells = _cells([("S1", "c1")])
    out = densify(counts, cells, offered_by_sample={"S1": {"A", "B"}}).sort("identity")
    assert out["identity"].to_list() == ["A", "B"]
    assert out["umiCount"].to_list() == [7, 0]  # B was asked and silent


def test_densify_does_not_invent_unoffered_identities():
    counts = _ident([("S1", "c1", "A", 7)])
    cells = _cells([("S1", "c1")])
    out = densify(counts, cells, offered_by_sample={"S1": {"A"}})
    assert out["identity"].to_list() == ["A"]


def test_identity_reading_is_the_highest_not_the_sum():
    df = _counts([("S1", "c1", "AAAA", 10), ("S1", "c1", "CCCC", 7)])
    out = combine_tags_to_identities(df, {("AAAA", "S1"): "A", ("CCCC", "S1"): "A"})
    assert out["umiCount"].to_list() == [10]


def test_combine_keeps_sample_id():
    df = _counts([("S1", "c1", "AAAA", 5), ("S2", "c1", "AAAA", 9)])
    out = combine_tags_to_identities(df, {("AAAA", "S1"): "A", ("AAAA", "S2"): "A"})
    assert out.height == 2 and set(out["sampleId"].to_list()) == {"S1", "S2"}


def test_specificity_score_matches_the_published_formula():
    assert math.isclose(specificity_score(10, 2), (1.0 - beta.cdf(0.925, 11, 5)) * 100.0, rel_tol=1e-12)


def test_cutoff_is_seventy_five():
    assert BOUND_CUTOFF == 75.0


def test_high_count_against_a_quiet_reference_is_bound():
    out = read_states(_ident([("S1", "c1", "A", 200)]), Admissibility({("S1", "c1"): 0}, set()), 75.0)
    assert out["state"].to_list() == [State.BOUND.value]


def test_zero_reads_not_bound_never_unreliable():
    out = read_states(_ident([("S1", "c1", "A", 0)]), Admissibility({("S1", "c1"): 5}, set()), 75.0)
    assert out["state"].to_list() == [State.NOT_BOUND.value]


def test_a_very_low_comparator_is_scored_rather_than_rerouted():
    # `count-becomes-a-state` deleted the thin-reference branch rather than filling
    # it in: no published line separates thin from usable, so the comparison runs
    # and the reference reading is emitted for the reader to judge instead. A
    # comparator of 1 is a real comparison, and the score decides it like any other.
    out = read_states(_ident([("S1", "c1", "A", 50)]), Admissibility({("S1", "c1"): 1}, set()), 75.0)
    assert out["state"].to_list() == [State.NOT_BOUND.value]  # scores 58.4, under the cutoff
    assert out["unreliableReason"].to_list() == [None]
    assert out["referenceCount"].to_list() == [1], "the reader is given what the verdict rested on"


def test_gated_cell_is_unreliable_and_stays_in_the_frame():
    out = read_states(_ident([("S1", "c1", "A", 500)]), Admissibility({("S1", "c1"): 900}, {("S1", "c1")}), 75.0)
    assert out.height == 1
    assert out["state"].to_list() == [State.UNRELIABLE.value]


def test_a_gated_cell_reports_the_gate_even_when_its_reference_is_very_low():
    # The gate set this cell aside AND its comparator reads 1. A very low
    # comparator is no longer a reason on its own, so only the gate can be
    # reported -- but the reason is an exported column that a later step reads to
    # tell a panel problem from a re-run problem, so this pins that the gate is
    # what it says. A cell the gate set aside was not measured at all.
    out = read_states(_ident([("S1", "c1", "A", 500)]), Admissibility({("S1", "c1"): 1}, {("S1", "c1")}), 75.0)
    assert out["state"].to_list() == [State.UNRELIABLE.value]
    reason = out["unreliableReason"].to_list()[0]
    assert reason == UnreliableReason.GATED


def test_densify_handles_a_sample_stained_with_nothing():
    # A non-empty offered map whose every value is empty contributes no block.
    # Guarding on the map rather than the assembled blocks raised here.
    out = densify(_ident([]), _cells([("S1", "c1")]), offered_by_sample={"S1": set()})
    assert out.height == 0
    assert out.schema["identity"] == pl.String
    assert out.schema["umiCount"] == pl.Int64


def test_never_asked_is_not_produced_here():
    out = read_states(_ident([("S1", "c1", "A", 0)]), Admissibility({("S1", "c1"): 5}, set()), 75.0)
    assert State.NEVER_ASKED.value not in out["state"].to_list()


def test_no_score_column_leaves_the_reading():
    out = read_states(_ident([("S1", "c1", "A", 50)]), Admissibility({("S1", "c1"): 5}, set()), 75.0)
    assert "score" not in out.columns
    assert {"umiCount", "referenceCount"} <= set(out.columns)


def test_specificity_score_stays_within_zero_and_hundred_at_sample_points():
    for a, r in [(0, 0), (5, 5), (1000, 3)]:
        assert 0.0 <= specificity_score(a, r) <= 100.0


def test_a_score_exactly_at_the_cutoff_is_bound():
    # The named value satisfies the condition it names, as everywhere else here.
    # Integer counts have no rational preimage of a fixed cutoff like 75.0 under
    # the beta CDF, so the exact boundary is built the other way round: compute
    # a reading's own score, then feed that exact value back in as the cutoff.
    # The comparison then lands on the line with no floating-point drift, and
    # ">=" must call it bound.
    exact = specificity_score(10, 2)
    out = read_states(_ident([("S1", "c1", "A", 10)]), Admissibility({("S1", "c1"): 2}, set()), cutoff=exact)
    assert out["state"].to_list() == [State.BOUND.value]


def test_no_line_separates_a_thin_comparator_from_a_usable_one():
    # There is no boundary left to be off-by-one about. A comparator of 1 and one
    # of 2 differ only in the score they produce, and a large enough antigen count
    # binds against either.
    for ref in (1, 2):
        low = read_states(_ident([("S1", "c1", "A", 0)]), Admissibility({("S1", "c1"): ref}, set()), 75.0)
        high = read_states(_ident([("S1", "c2", "A", 500)]), Admissibility({("S1", "c2"): ref}, set()), 75.0)
        assert low["unreliableReason"].to_list() == [None], f"comparator {ref} is a comparison"
        assert low["state"].to_list() == [State.NOT_BOUND.value]
        assert high["state"].to_list() == [State.BOUND.value], f"500 binds against a comparator of {ref}"


def test_no_comparator_is_unreliable_but_a_comparator_reading_zero_is_scored():
    # The two must not collapse. served=NONE (modelled here as an empty
    # reference dict, per reference_by_cell's contract) means no comparison
    # existed; a comparator present and reading 0 is a real comparison and
    # scores normally -- a positive antigen count against a zero reference is
    # This also subsumes the plain no-comparator-is-unreliable check: nothing
    # else in the suite needs a weaker, reason-blind version of this.
    no_comparator = read_states(_ident([("S1", "c1", "A", 200)]), Admissibility({}, set()), 75.0)
    zero_comparator = read_states(_ident([("S1", "c1", "A", 200)]), Admissibility({("S1", "c1"): 0}, set()), 75.0)
    assert no_comparator["state"].to_list() == [State.UNRELIABLE.value]
    assert no_comparator["unreliableReason"].to_list() == [UnreliableReason.NO_COMPARATOR]
    assert zero_comparator["state"].to_list() == [State.BOUND.value]
    assert zero_comparator["unreliableReason"].to_list() == [None]


def test_silent_admissible_cell_can_never_score_bound():
    # The fact the analytic path rests on: specificity_score(0, r) is ~0.0422
    # at r = 0 and smaller for every larger r. A silent admissible cell is
    # therefore always *not bound* for any cutoff above that bound, which is
    # what lets silent_tally skip materializing its row.
    assert math.isclose(specificity_score(0, 0), 0.0422, abs_tol=5e-4)
    scores = [specificity_score(0, r) for r in range(0, 50)]
    assert scores == sorted(scores, reverse=True)
    assert all(s < 0.05 for s in scores)


def test_duplicated_cells_rows_give_the_deduped_answer():
    # A row-count bug this project shipped once already: keys built from
    # `cells` without dedup counted the duplicated c2 row as if it were a
    # second cell. asked must count distinct cells (2), not rows (3), and
    # silentNotBound must follow from the deduped count.
    cells = _cells([("S1", "c1"), ("S1", "c2"), ("S1", "c2")])
    admissibility = Admissibility({("S1", "c1"): 5, ("S1", "c2"): 5}, set())
    observed = read_states(_ident([("S1", "c1", "A", 50)]), admissibility, 75.0)
    tally = silent_tally(observed, cells, {"S1": {"A"}}, admissibility)
    row = tally.row(0, named=True)
    assert row["asked"] == 2  # not 3: the duplicated c2 row counts once
    assert row["silentNotBound"] == 1


def test_duplicated_observed_rows_are_rejected_not_silently_wrong():
    # Recorded rather than latent: without the assertion in silent_tally, this
    # combination silently returned silentUnreliable == -1 (a duplicated
    # observed row for an inadmissible cell is counted twice against a total
    # that counts the cell once). `observed` must be unique on
    # (cell, identity); this input violates that, so the function must now
    # refuse it loudly instead of emitting a negative count.
    cells = _cells([("S1", "c1")])
    admissibility = Admissibility({}, set())  # no comparator for c1: inadmissible
    observed = read_states(_ident([("S1", "c1", "A", 50), ("S1", "c1", "A", 50)]), admissibility, 75.0)
    # ValueError rather than AssertionError, and the type is the point: an `assert` is stripped
    # under -O, and this guard stripped does not crash -- it returns a wrong answer. Pinning the
    # type here is what keeps it from quietly becoming strippable again.
    with pytest.raises(ValueError):
        silent_tally(observed, cells, {"S1": {"A"}}, admissibility)


def _build_silent_tally_population(seed, force_empty_sample=None):
    # Shared by every check below: build a small, varied population by
    # construction -- several samples, cells, identities, some cells gated,
    # some with a very low reference, some with a normal one.
    rng = random.Random(seed)
    samples = ["S1", "S2", "S3"]
    identities = ["A", "B", "C"]
    gated: set[tuple[str, str]] = set()
    reference: dict[tuple[str, str], int] = {}
    cell_rows = []
    tag_rows = []
    offered_by_sample: dict[str, set[str]] = {}

    for sample in samples:
        if sample == force_empty_sample:
            offered_by_sample[sample] = set()
        else:
            offered_by_sample[sample] = set(rng.sample(identities, k=rng.randint(1, len(identities))))
        for i in range(6):
            cell = f"c{i}"
            cell_rows.append((sample, cell))
            key = (sample, cell)
            # Reference reading: sometimes missing (no comparator), sometimes
            # very low, sometimes ordinary.
            roll = rng.random()
            if roll < 0.2:
                pass  # no comparator for this cell
            elif roll < 0.4:
                reference[key] = 1  # a very low comparator, still comparable
            else:
                reference[key] = rng.randint(2, 20)
            if rng.random() < 0.15:
                gated.add(key)
            # Sparse observed readings: only some (cell, identity) pairs the
            # sample offered actually got a tag-stat row.
            for identity in offered_by_sample[sample]:
                if rng.random() < 0.5:
                    # Some readings must actually clear the cutoff. Against
                    # references of 2-20 the largest score a count of 30 can
                    # reach is about 11.8, so a population drawn only from
                    # 0-30 contains no bound cell at all -- and the oracle
                    # comparison's bound assertion below then reads 0 == 0 in
                    # every run, proving nothing about the claim it names.
                    count = rng.randint(0, 30) if rng.random() < 0.7 else rng.randint(200, 900)
                    tag_rows.append((sample, cell, identity, count))

    return samples, identities, gated, reference, cell_rows, tag_rows, offered_by_sample


def _check_silent_tally_matches_oracle(seed, cutoff=BOUND_CUTOFF, force_empty_sample=None):
    # Checks silent_tally's three cheap terms, sample-keyed (the default),
    # against the dense grid built by densify and read through read_states,
    # which never skips a row.
    samples, identities, gated, reference, cell_rows, tag_rows, offered_by_sample = _build_silent_tally_population(
        seed, force_empty_sample
    )

    cells = _cells(cell_rows)
    sparse_identities = _ident(tag_rows)
    admissibility = Admissibility(reference, gated)
    observed = read_states(sparse_identities, admissibility, cutoff)

    dense = densify(sparse_identities, cells, offered_by_sample)
    oracle = read_states(dense, admissibility, cutoff)

    tally = silent_tally(observed, cells, offered_by_sample, admissibility)

    # A tally that emits extra rows -- one for an identity a sample never
    # offered -- must fail here, not just disagree on counts.
    expected_row_count = sum(len(offered) for offered in offered_by_sample.values())
    assert tally.height == expected_row_count

    for sample in samples:
        offered = offered_by_sample[sample]
        for identity in identities:
            group_filter = (pl.col("sampleId") == sample) & (pl.col("identity") == identity)
            if identity not in offered:
                # Never asked of this sample: no row at all, not a zero row.
                assert tally.filter(group_filter).height == 0
                continue

            oracle_group = oracle.filter(group_filter)
            observed_group = observed.filter(group_filter)
            tally_row = tally.filter(group_filter).row(0, named=True)

            oracle_states = oracle_group["state"].to_list()
            observed_states = observed_group["state"].to_list()

            # A silent admissible cell can never be observed as bound, so the
            # oracle and the sparse frame must agree exactly on bound counts.
            assert oracle_states.count(State.BOUND.value) == observed_states.count(State.BOUND.value)

            expected_silent_unreliable = oracle_states.count(State.UNRELIABLE.value) - observed_states.count(
                State.UNRELIABLE.value
            )
            expected_silent_not_bound = oracle_states.count(State.NOT_BOUND.value) - observed_states.count(
                State.NOT_BOUND.value
            )

            assert tally_row["asked"] == len(oracle_states)
            assert tally_row["observed"] == len(observed_states)
            assert tally_row["silentUnreliable"] == expected_silent_unreliable
            assert tally_row["silentNotBound"] == expected_silent_not_bound


@pytest.mark.parametrize(
    "seed, force_empty_sample",
    [
        (20260817, None),
        (1, None),
        (2, None),
        (3, None),
        (4, None),
        (5, None),
        (6, None),
        # The generator above never draws an empty offered set on its own;
        # force one so the empty-block path in densify and the zero-row case
        # in silent_tally are both exercised against the oracle, not just
        # against each other.
        (7, "S2"),
    ],
)
def test_silent_tally_agrees_with_the_densify_oracle_on_small_random_inputs(seed, force_empty_sample):
    _check_silent_tally_matches_oracle(seed, force_empty_sample=force_empty_sample)


def test_silent_tally_agrees_with_the_oracle_at_a_low_valid_cutoff():
    # 0.5 is comfortably above specificity_score(0, 0) ~= 0.0422, the bound
    # named in specificity_score's and silent_tally's docstrings. This guards
    # the boundary itself rather than assuming BOUND_CUTOFF=75.0 is
    # representative of every cutoff the equivalence must hold for.
    _check_silent_tally_matches_oracle(seed=20260817, cutoff=0.5)


def _check_silent_tally_matches_oracle_grouped(seed, cutoff=BOUND_CUTOFF, force_empty_sample=None):
    # Same population and same dense oracle as the sample-keyed check above,
    # but the cells are regrouped into sets that mix samples with different
    # offered identities, and silent_tally is called with that grouping. A
    # set's cell index (0..5) becomes its group, independent of sample, so
    # every group is guaranteed to contain a member from all three samples
    # -- exactly the shape a hoisted asked/total_inadmissible would get
    # wrong, since S1, S2, S3 are built with independently random offered
    # sets and need not agree on what a given group's identity was offered.
    samples, identities, gated, reference, cell_rows, tag_rows, offered_by_sample = _build_silent_tally_population(
        seed, force_empty_sample
    )

    cells = _cells(cell_rows)
    sparse_identities = _ident(tag_rows)
    admissibility = Admissibility(reference, gated)
    observed = read_states(sparse_identities, admissibility, cutoff)
    dense = densify(sparse_identities, cells, offered_by_sample)
    oracle = read_states(dense, admissibility, cutoff)

    group_by_cell = {(sample, cell): cell for sample, cell in cell_rows}
    groups = sorted({cell for _, cell in cell_rows})

    tally = silent_tally(
        observed, cells, offered_by_sample, admissibility, group_by_cell=group_by_cell, group_column="setId"
    )

    for group in groups:
        members = {k for k, g in group_by_cell.items() if g == group}
        offered_here = set().union(*(offered_by_sample[k[0]] for k in members))
        for identity in identities:
            group_filter = pl.col("setId") == group
            if identity not in offered_here:
                # None of this group's members' own samples offered it: no
                # row at all, not a zero row.
                assert tally.filter(group_filter & (pl.col("identity") == identity)).height == 0
                continue

            def _states_for(frame):
                # A plain Python filter, not a polars struct comparison: this
                # only needs to run over a handful of rows in a test, and it
                # sidesteps any doubt about how polars compares struct columns.
                return [
                    state
                    for sample_id, cell_id, ident, state in zip(
                        frame["sampleId"].to_list(),
                        frame["cellId"].to_list(),
                        frame["identity"].to_list(),
                        frame["state"].to_list(),
                        strict=True,
                    )
                    if (sample_id, cell_id) in members and ident == identity
                ]

            oracle_states = _states_for(oracle)
            observed_states = _states_for(observed)

            tally_row = tally.filter(group_filter & (pl.col("identity") == identity)).row(0, named=True)

            expected_silent_unreliable = oracle_states.count(State.UNRELIABLE.value) - observed_states.count(
                State.UNRELIABLE.value
            )
            expected_silent_not_bound = oracle_states.count(State.NOT_BOUND.value) - observed_states.count(
                State.NOT_BOUND.value
            )

            assert tally_row["asked"] == len(oracle_states)
            assert tally_row["observed"] == len(observed_states)
            assert tally_row["silentUnreliable"] == expected_silent_unreliable
            assert tally_row["silentNotBound"] == expected_silent_not_bound


@pytest.mark.parametrize(
    "seed, force_empty_sample",
    [
        (20260817, None),
        (1, None),
        (2, None),
        (7, "S2"),
    ],
)
def test_silent_tally_agrees_with_the_oracle_when_groups_span_differing_panels(seed, force_empty_sample):
    _check_silent_tally_matches_oracle_grouped(seed, force_empty_sample=force_empty_sample)


def test_silent_tally_group_column_is_named_by_the_caller():
    cells = _cells([("S1", "c1"), ("S2", "c1")])
    observed = _ident([])
    admissibility = Admissibility({("S1", "c1"): 5, ("S2", "c1"): 5}, set())
    tally = silent_tally(
        observed,
        cells,
        {"S1": {"A"}, "S2": {"A"}},
        admissibility,
        group_by_cell={("S1", "c1"): "G1", ("S2", "c1"): "G1"},
        group_column="setId",
    )
    assert tally.columns[0] == "setId"
    row = tally.row(0, named=True)
    assert row["setId"] == "G1" and row["asked"] == 2  # both samples' c1 land in one group


def test_a_group_spanning_two_panels_does_not_inflate_silent_unreliable():
    # THE hoist bug, pinned directly: S1 offers A, S2 does not. A group holds
    # one cell from each, and S2's cell is gated. A hoisted total_inadmissible
    # would count S2's gated cell against identity A too, even though S2
    # never offered A -- inflating silentUnreliable for an identity that
    # cell was never asked about.
    cells = _cells([("S1", "c1"), ("S2", "c2")])
    observed = _ident([])  # both cells silent
    admissibility = Admissibility({("S1", "c1"): 5}, {("S2", "c2")})
    tally = silent_tally(
        observed,
        cells,
        offered_by_sample={"S1": {"A"}, "S2": {"B"}},
        admissibility=admissibility,
        group_by_cell={("S1", "c1"): "G1", ("S2", "c2"): "G1"},
        group_column="setId",
    )
    row_a = tally.filter(pl.col("identity") == "A").row(0, named=True)
    assert row_a["asked"] == 1  # only S1's cell, not S2's
    assert row_a["silentUnreliable"] == 0  # S2's gated cell must not count against A
    assert row_a["silentNotBound"] == 1  # S1's silent, admissible cell votes not bound


def test_same_barcode_combines_into_different_identities_by_sample():
    # The cell's own sample decides which antigen its barcode counted toward. Keyed by tag alone this
    # put both cells under whichever identity the dataset-wide map happened to hold.
    df = _counts([("S1", "c1", "AAAA", 10), ("S2", "c2", "AAAA", 20)])
    out = combine_tags_to_identities(df, {("AAAA", "S1"): "A", ("AAAA", "S2"): "B"}).sort("sampleId")
    assert out["identity"].to_list() == ["A", "B"]
    assert out["umiCount"].to_list() == [10, 20]


def test_combine_is_still_the_max_within_one_sample():
    # tags-combine-by-the-highest is unchanged by the keying: two tags of one identity in one cell
    # still combine to the highest, never the sum.
    df = _counts([("S1", "c1", "AAAA", 3), ("S1", "c1", "CCCC", 7)])
    out = combine_tags_to_identities(df, {("AAAA", "S1"): "A", ("CCCC", "S1"): "A"})
    assert out["umiCount"].to_list() == [7]


def test_a_global_panel_entry_applies_to_every_sample_when_combining():
    df = _counts([("S1", "c1", "AAAA", 4), ("S2", "c2", "AAAA", 9)])
    out = combine_tags_to_identities(df, {("AAAA", "*"): "A"}).sort("sampleId")
    assert out["identity"].to_list() == ["A", "A"]
    assert out["umiCount"].to_list() == [4, 9]


def test_an_explicit_per_sample_declaration_beats_the_global_one():
    # A panel mixing "*" with named rows is refused by the reader, but the fill order is pinned here
    # so a caller building a frame directly cannot silently get the global answer for a named sample.
    df = _counts([("S1", "c1", "AAAA", 5)])
    out = combine_tags_to_identities(df, {("AAAA", "*"): "GLOBAL", ("AAAA", "S1"): "MINE"})
    assert out["identity"].to_list() == ["MINE"]


def test_the_comparator_is_computed_on_raw_counts_not_floored_ones(tmp_path):
    """The minimum count acts on the numerator only; every rung reads its own source raw.

    The bug this pins was at the call site, not in this module: production
    handed `reference_by_cell` the FLOORED frame. Two consequences, and the
    second is the sharper one.

    A cell whose panel readings straddle the minimum medians differently before
    and after: [1, 1, 2, 9, 9] medians to 2, and the same readings floored at 4
    are [0, 0, 0, 9, 9], which medians to 0. A comparator of 0 rather than 2
    moves every one of that cell's verdicts toward *bound*.

    And the median was internally inconsistent wherever a reference tag was
    also present, because the minimum exempts reference tags and floors every
    antigen tag — so a single median ran over a mixture of raw and floored
    values. Nobody chose that.
    """
    raw = _counts(
        [
            ("S1", "c1", "AAAA", 1),
            ("S1", "c1", "CCCC", 1),
            ("S1", "c1", "GGGG", 2),
            ("S1", "c1", "TTTT", 9),
            ("S1", "c1", "ACAC", 9),
        ]
    )
    floored = apply_floor(raw, 4, set()).counts

    on_raw, _ = reference_by_cell(raw, set(), ReferenceChoice.PANEL, panel_size=5, min_members=5)
    on_floored, _ = reference_by_cell(floored, set(), ReferenceChoice.PANEL, panel_size=5, min_members=5)

    assert on_raw[("S1", "c1")] == 2
    # Held so the test fails loudly if the minimum ever stops biting here — the
    # two frames must actually differ, or this proves nothing.
    assert on_floored[("S1", "c1")] == 0
