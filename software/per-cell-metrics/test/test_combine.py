import random

import polars as pl
import pytest
from combine import (
    DEFAULT_MIN_VOTERS,
    SetUnreliableReason,
    attach_competitor_notes,
    combine_cells,
    self_disagreement,
    set_counts,
)
from verdict import Admissibility, State, combine_tags_to_identities, gate_cells, read_states

B, N, U, NA = (State.BOUND.value, State.NOT_BOUND.value, State.UNRELIABLE.value, State.NEVER_ASKED.value)


# No setId column: `combine_cells` derives which set a row belongs to from
# `cells_by_set` alone, matching `read_states`' actual output shape.
_STATES_SCHEMA = {
    "sampleId": pl.String,
    "cellId": pl.String,
    "identity": pl.String,
    "state": pl.String,
}


def _states(rows):
    return pl.DataFrame(rows, orient="row", schema=_STATES_SCHEMA)


def _row(out, identity):
    return out.filter(pl.col("identity") == identity).row(0, named=True)


# A permissive admissibility used by every test whose cells all have an
# explicit row in `states` -- no cell is silent, so asked == observed for
# every identity and the silent terms are 0 regardless of what this holds.
_NEUTRAL = Admissibility({}, 0, set())


def test_majority_wins():
    df = _states([("S1", "c1", "A", B), ("S1", "c2", "A", B), ("S1", "c3", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2"), ("S1", "c3")]}
    out = combine_cells(df, universe={"A"}, offered={"S1": {"A"}}, cells_by_set=cells_by_set, admissibility=_NEUTRAL)
    r = _row(out, "A")
    assert r["state"] == B and r["cellsAnswered"] == 3 and r["agreement"] == 2 / 3


def test_vote_is_per_identity_so_a_set_can_bind_several():
    df = _states([("S1", "c1", i, B) for i in ("A", "C")])
    cells_by_set = {"s1": [("S1", "c1")]}
    out = combine_cells(
        df, universe={"A", "C"}, offered={"S1": {"A", "C"}}, cells_by_set=cells_by_set, admissibility=_NEUTRAL
    ).sort("identity")
    assert out["state"].to_list() == [B, B]


def test_a_tie_cannot_be_settled():
    df = _states([("S1", "c1", "A", B), ("S1", "c2", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    out = combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL)
    r = _row(out, "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.TIE.value


def test_a_three_way_split_that_ties_at_the_top_is_also_unreliable():
    # Not just the minimal 1-vs-1 tie: three cells settle bound, three settle
    # not bound. The tie check must compare the leading counts, not special-
    # case a count of one.
    df = _states([("S1", f"b{i}", "A", B) for i in range(3)] + [("S1", f"n{i}", "A", N) for i in range(3)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", f"n{i}") for i in range(3)]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == U and r["cellsAnswered"] == 6
    assert r["unreliableReason"] == SetUnreliableReason.TIE.value


def test_never_asked_comes_only_from_not_being_offered():
    # Z is in the universe and NOT offered -> never asked.
    df = _states([("S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    out = combine_cells(
        df, universe={"A", "Z"}, offered={"S1": {"A"}}, cells_by_set=cells_by_set, admissibility=_NEUTRAL
    )
    r = _row(out, "Z")
    assert r["state"] == NA
    assert r["cellsCouldAnswer"] == 0
    assert r["unreliableReason"] == SetUnreliableReason.NEVER_OFFERED.value


def test_an_offered_identity_nobody_bound_is_not_bound_not_never_asked():
    # Explicit rows, every one not-bound: offered, everybody read zero, so
    # the verdict is not bound, never never-asked.
    df = _states([("S1", "c1", "A", N), ("S1", "c2", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == N and r["state"] != NA


def test_silent_cells_vote_an_antigen_every_cell_failed_still_reads_not_bound():
    # The defect this reduction exists to avoid: five cells asked about A,
    # none has a row in `states` at all (tag-stat never observed a reading
    # for any of them), and all five are admissible. Silent admissible cells
    # resolve not bound, so the set must read not bound with all five voting
    # -- never unreliable (which is what happens if silent cells are simply
    # excluded from the tally) and never never-asked.
    df = _states([])
    members = [("S1", f"c{i}") for i in range(5)]
    cells_by_set = {"s1": members}
    admissibility = Admissibility({k: 5 for k in members}, 2, set())
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility), "A")
    assert r["state"] == N
    assert r["cellsAnswered"] == 5
    assert r["cellsCouldAnswer"] == 5
    assert r["agreement"] == 1.0


def test_unsettled_cells_do_not_vote_but_do_count_as_could_answer():
    df = _states([("S1", "c1", "A", B), ("S1", "c2", "A", U), ("S1", "c3", "A", U)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2"), ("S1", "c3")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["cellsAnswered"] == 1 and r["cellsCouldAnswer"] == 3


def test_a_verdict_may_rest_on_one_cell_and_says_so():
    assert DEFAULT_MIN_VOTERS == 1
    df = _states([("S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["cellsAnswered"] == 1


def test_below_min_voters_is_unreliable_when_raised():
    df = _states([("S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_voters=2), "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.TOO_FEW_VOTERS.value


def test_exactly_min_voters_settles():
    # The named value satisfies the condition it names, as elsewhere in this
    # project: two settled votes with min_voters=2 must settle, not fail.
    df = _states([("S1", "c1", "A", B), ("S1", "c2", "A", B)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_voters=2), "A")
    assert r["state"] == B and r["cellsAnswered"] == 2


def test_narrow_majority_stands_and_reports_how_narrow():
    df = _states([("S1", f"c{i}", "A", B) for i in range(6)] + [("S1", f"d{i}", "A", N) for i in range(5)])
    cells_by_set = {"s1": [("S1", f"c{i}") for i in range(6)] + [("S1", f"d{i}") for i in range(5)]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["agreement"] == 6 / 11


def test_exactly_min_agreement_settles_when_raised():
    # 3 bound, 1 not bound -> agreement 0.75. Raising min_agreement to
    # exactly 0.75 must still settle: the boundary belongs to the pass side.
    df = _states([("S1", f"b{i}", "A", B) for i in range(3)] + [("S1", "n0", "A", N)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", "n0")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_agreement=0.75), "A")
    assert r["state"] == B and r["agreement"] == 0.75


def test_just_below_min_agreement_is_below_agreement_floor_not_tie():
    # A real majority exists here (3 of 4) -- it is refused only because the
    # operator raised min_agreement above it. That is a different reason
    # than a tie, which has no majority to refuse.
    df = _states([("S1", f"b{i}", "A", B) for i in range(3)] + [("S1", "n0", "A", N)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", "n0")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_agreement=0.76), "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.BELOW_AGREEMENT_FLOOR.value


def test_a_genuine_tie_still_reads_tie_even_when_min_agreement_would_also_fail_it():
    # A fixture-coincidence trap: a tie's agreement is exactly 0.5, so any
    # min_agreement above 0.5 would ALSO fail it, and a fixture where both
    # conditions hold cannot tell which branch produced the answer. Raise
    # min_agreement to 0.6 on the same 1-vs-1 tie from test_a_tie_cannot_be_settled
    # and confirm the reason is still TIE, not BELOW_AGREEMENT_FLOOR -- the
    # tie check must run and win regardless of where the floor sits.
    df = _states([("S1", "c1", "A", B), ("S1", "c2", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_agreement=0.6), "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.TIE.value


def test_set_with_every_cell_set_aside_is_unreliable_through_the_real_pipeline():
    # Driven through read_states, not fed a synthetic UNRELIABLE row: a gate
    # excludes both of this set's cells, read_states produces the real
    # UNRELIABLE rows from that, and combine_cells must still resolve the
    # set to unreliable, reason all-cells-gated -- derived from the cells'
    # own UnreliableReason.GATED, not hard-coded.
    counts = pl.DataFrame(
        [("S1", "c1", "TAG", 500), ("S1", "c2", "TAG", 500)],
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64},
    )
    identities = combine_tags_to_identities(counts, {"TAG": "A"})
    reference = {("S1", "c1"): 900, ("S1", "c2"): 900}
    gated, _ = gate_cells(reference, threshold=800)
    admissibility = Admissibility(reference, 2, gated)
    per_cell = read_states(identities, admissibility, cutoff=75.0)

    # No setId to attach: which set these rows belong to comes from
    # cells_by_set below, not from a column on states.
    states = per_cell.select("sampleId", "cellId", "identity", "state")
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    r = _row(combine_cells(states, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility), "A")
    assert r["state"] == U and r["cellsCouldAnswer"] == 2 and r["cellsAnswered"] == 0
    assert r["unreliableReason"] == SetUnreliableReason.ALL_CELLS_GATED.value


def test_all_cells_gated_is_not_reported_when_the_reason_mix_is_not_unanimous():
    # One cell gated, one with no comparator at all: the set-wide reason is
    # not "all cells gated" (it is not true) but the comparator failure that
    # is present, per _dominant_reason's documented priority.
    df = _states([])
    members = [("S1", "c1"), ("S1", "c2")]
    cells_by_set = {"s1": members}
    admissibility = Admissibility({("S1", "c1"): 900}, 2, {("S1", "c1")})  # c2 has no comparator entry
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility), "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.NO_COMPARATOR.value


def test_cellscouldanswer_is_not_a_row_count():
    # THE defect this reduction exists to fix. 40 cells; only 3 have a row in
    # `states`, the other 37 are silent and admissible. cellsCouldAnswer must
    # reflect all 40 cells asked (their sample offered A), never the 3 rows.
    explicit = [("S1", "c0", "A", B), ("S1", "c1", "A", B), ("S1", "c2", "A", N)]
    df = _states(explicit)
    members = [("S1", "c0"), ("S1", "c1"), ("S1", "c2")] + [("S1", f"s{i}") for i in range(37)]
    cells_by_set = {"s1": members}
    admissibility = Admissibility({k: 5 for k in members}, 2, set())
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility), "A")
    assert r["cellsCouldAnswer"] == 40  # not 3
    assert r["cellsAnswered"] == 40  # 2 explicit bound + 1 explicit not-bound + 37 silent not-bound
    assert r["state"] == N  # 38 not-bound votes beat 2 bound


def test_a_set_spanning_two_panels_counts_only_the_asked_cells_and_does_not_inflate_silent_unreliable():
    # S1 offers A, S2 offers B (not A). The set holds cells from both. For
    # identity A: cellsCouldAnswer must count only S1's cells, and S2's gated
    # cell -- which never offered A -- must not inflate silentUnreliable at A.
    df = _states([])
    members = [("S1", "c1"), ("S1", "c2"), ("S2", "c3"), ("S2", "c4")]
    cells_by_set = {"s1": members}
    # S1's cells are admissible; S2's c3 is gated, c4 has a normal reference.
    reference = {("S1", "c1"): 5, ("S1", "c2"): 5, ("S2", "c3"): 900, ("S2", "c4"): 5}
    admissibility = Admissibility(reference, 2, {("S2", "c3")})
    out = combine_cells(df, {"A", "B"}, {"S1": {"A"}, "S2": {"B"}}, cells_by_set, admissibility)

    row_a = _row(out, "A")
    assert row_a["cellsCouldAnswer"] == 2  # only S1's two cells, not all four
    assert row_a["state"] == N  # both S1 cells silent and admissible -> not bound
    assert row_a["cellsAnswered"] == 2

    row_b = _row(out, "B")
    assert row_b["cellsCouldAnswer"] == 2  # only S2's two cells
    # S2's gated cell counts against B (which S2 offers), and its silent
    # not-bound cell (c4) settles: one voter, one vote, not bound.
    assert row_b["cellsAnswered"] == 1
    assert row_b["state"] == N


def test_a_row_for_a_cell_no_set_lists_is_ignored():
    # A stray row for a cell absent from every set's membership must not
    # vote: cellsAnswered must never exceed cellsCouldAnswer. Before the fix,
    # a stray row like this counted toward the set it happened to name in a
    # setId column; there is no such column now, only cells_by_set, and this
    # cell is not in it.
    df = _states([("S1", "c1", "A", B), ("S1", "stray", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["cellsCouldAnswer"] == 1
    assert r["cellsAnswered"] == 1
    assert r["cellsAnswered"] <= r["cellsCouldAnswer"]


def test_a_cell_in_two_sets_fails_naming_cells_by_set():
    # A cell listed under two different set ids is a malformed cells_by_set,
    # not a silent_tally precondition violation: the failure must name the
    # thing that is actually wrong.
    cells_by_set = {"s1": [("S1", "c1")], "s2": [("S1", "c1")]}
    df = _states([])
    # ValueError rather than AssertionError, and the type is the point: an `assert` is stripped
    # under -O, and this guard stripped does not crash -- it returns a wrong answer. Pinning the
    # type here is what keeps it from quietly becoming strippable again.
    with pytest.raises(ValueError, match="cells_by_set"):
        combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL)


def test_dominant_reason_raises_rather_than_falling_through_to_thin_comparator():
    # A malformed but constructible input: `states` claims this cell is
    # UNRELIABLE, while `admissibility` says it is perfectly fine -- a real
    # comparator, not gated, not thin. That contradiction is exactly what
    # used to let an admissible key reach _dominant_reason and fall through
    # to THIN_COMPARATOR; it must now raise instead of reporting a
    # comparator problem for a cell whose comparator is fine.
    df = _states([("S1", "c1", "A", U)])
    cells_by_set = {"s1": [("S1", "c1")]}
    admissibility = Admissibility({("S1", "c1"): 10}, 2, set())
    # ValueError rather than AssertionError, and the type is the point: an `assert` is stripped
    # under -O, and this guard stripped does not crash -- it returns a wrong answer. Pinning the
    # type here is what keeps it from quietly becoming strippable again.
    with pytest.raises(ValueError):
        combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility)


def _verdicts(rows):
    return pl.DataFrame(rows, orient="row", schema={"setId": pl.String, "identity": pl.String, "state": pl.String})


def _competitor_row(out, identity):
    return out.filter(pl.col("identity") == identity).row(0, named=True)


def test_negative_beside_a_bound_competitor_names_it():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", N)]), [{"A", "C"}])
    r = _competitor_row(out, "C")
    assert r["competedWith"] == "A" and r["state"] == N


def test_a_statement_can_test_the_note():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", N)]), [{"A", "C"}])
    assert _competitor_row(out, "C")["wasCompeted"] == "true"
    assert _competitor_row(out, "A")["wasCompeted"] == "false"


def test_no_note_where_no_competitor_was_bound():
    out = attach_competitor_notes(_verdicts([("s1", "A", N), ("s1", "C", N)]), [{"A", "C"}])
    assert _competitor_row(out, "C")["competedWith"] is None


def test_no_note_on_a_bound_identity():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", B)]), [{"A", "C"}])
    assert out["competedWith"].to_list() == [None, None]


def test_no_note_without_a_declared_group():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", N)]), [])
    assert out["competedWith"].to_list() == [None, None]


def test_notes_do_not_leak_across_sets():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s2", "C", N)]), [{"A", "C"}])
    assert _competitor_row(out.filter(pl.col("setId") == "s2"), "C")["competedWith"] is None


def test_several_bound_competitors_are_all_named():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "B", B), ("s1", "C", N)]), [{"A", "B", "C"}])
    assert _competitor_row(out, "C")["competedWith"] == "A, B"


def test_was_competed_is_the_string_false_never_null_with_no_declared_groups():
    # wasCompeted is the predicate a downstream statement filters on. With no
    # contending groups at all, every row's flag must still be the literal
    # string "false" -- a null here would make "wasCompeted == false" fail to
    # match the exact rows the flag exists to describe.
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", N)]), [])
    assert out["wasCompeted"].to_list() == ["false", "false"]
    assert out["wasCompeted"].dtype == pl.String


def test_was_competed_is_the_string_false_never_null_with_declared_groups_present():
    # Same requirement, but with a declared group in play and a row that
    # simply has no bound rival: the flag column must not switch to null just
    # because contention was possible elsewhere in the frame.
    out = attach_competitor_notes(_verdicts([("s1", "A", N), ("s1", "C", N)]), [{"A", "C"}])
    assert out["wasCompeted"].to_list() == ["false", "false"]


def test_no_note_on_an_unreliable_reading():
    # An UNRELIABLE identity made no settled comparison, so it has no
    # negative for a competitor to sit beside -- naming one would assert a
    # comparison this run never made.
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", U)]), [{"A", "C"}])
    r = _competitor_row(out, "C")
    assert r["competedWith"] is None
    assert r["wasCompeted"] == "false"


def test_no_note_on_a_never_asked_reading():
    out = attach_competitor_notes(_verdicts([("s1", "A", B), ("s1", "C", NA)]), [{"A", "C"}])
    r = _competitor_row(out, "C")
    assert r["competedWith"] is None
    assert r["wasCompeted"] == "false"


def test_overlapping_declared_groups_union_their_bound_competitors():
    # C sits in two declared groups, {A, C} and {C, D}, with A and D each
    # bound in only one of them. The note names both: the union of bound
    # competitors across every group that contains the identity, not just
    # the first matching group.
    out = attach_competitor_notes(
        _verdicts([("s1", "A", B), ("s1", "D", B), ("s1", "C", N)]),
        [{"A", "C"}, {"C", "D"}],
    )
    assert _competitor_row(out, "C")["competedWith"] == "A, D"


def test_competitor_names_are_joined_in_sorted_order():
    # Three bound rivals whose declared-group and bound-set iteration order
    # is not alphabetical; only a sorted join reliably reads "Bee, Mango,
    # Zebra" run after run. A byte-stable column depends on this.
    out = attach_competitor_notes(
        _verdicts([("s1", "Zebra", B), ("s1", "Mango", B), ("s1", "Bee", B), ("s1", "C", N)]),
        [{"Zebra", "Mango", "Bee", "C"}],
    )
    assert _competitor_row(out, "C")["competedWith"] == "Bee, Mango, Zebra"


def _v(rows):
    return pl.DataFrame(rows, orient="row", schema={"setId": pl.String, "identity": pl.String, "state": pl.String})


def test_denominator_is_offered_and_settled():
    v = _v([("s1", f"i{i}", B) for i in range(8)] + [("s1", "i8", U), ("s1", "i9", NA)])
    r = set_counts(v).row(0, named=True)
    assert r["boundCount"] == 8
    assert r["settledCount"] == 8  # i8 unsettled, i9 never asked
    assert r["offeredCount"] == 9  # never-asked is not offered
    assert r["unsettledCount"] == 1


def test_not_bound_is_settled_and_in_the_denominator():
    v = _v([("s1", "a", B), ("s1", "b", N)])
    r = set_counts(v).row(0, named=True)
    assert r["boundCount"] == 1 and r["settledCount"] == 2 and r["unsettledCount"] == 0


def test_never_asked_is_outside_the_denominator():
    v = _v([("s1", "a", B), ("s1", "b", NA)])
    r = set_counts(v).row(0, named=True)
    assert r["offeredCount"] == 1 and r["settledCount"] == 1


def test_counts_are_in_identities_not_tags():
    # One identity carried on two tags is one row here, so it counts once.
    v = _v([("s1", "family", B)])
    assert set_counts(v).row(0, named=True)["boundCount"] == 1


def test_each_set_counted_separately():
    v = _v([("s1", "a", B), ("s2", "a", N)])
    out = set_counts(v).sort("setId")
    assert out["boundCount"].to_list() == [1, 0]


def test_offered_equals_settled_plus_unsettled_with_all_four_states_present():
    # A fixture carrying BOUND, NOT_BOUND, UNRELIABLE, and NEVER_ASKED at
    # once, so the arithmetic relationship is pinned rather than incidentally
    # true because some state never appeared. A predicate that counts the
    # wrong states (say offeredCount including NEVER_ASKED, or settledCount
    # including UNRELIABLE) passes every test above that uses only two or
    # three states; this one does not let that slip through.
    v = _v([("s1", "a", B), ("s1", "b", N), ("s1", "c", U), ("s1", "d", NA)])
    r = set_counts(v).row(0, named=True)
    assert r["offeredCount"] == r["settledCount"] + r["unsettledCount"]
    assert r["boundCount"] <= r["settledCount"]
    assert r["boundCount"] == 1
    assert r["settledCount"] == 2
    assert r["unsettledCount"] == 1
    assert r["offeredCount"] == 3


def test_a_set_asked_nothing_reports_all_zero_and_a_reader_must_guard_the_divide():
    # Every position NEVER_ASKED: offeredCount is 0, so a downstream reader
    # computing boundCount / offeredCount would divide by zero. This pins
    # what the row emits -- all zeros -- rather than leaving the shape
    # undocumented; the guard against the zero is the caller's job, since
    # this function cannot produce a rate for a set that was asked nothing.
    v = _v([("s1", "a", NA), ("s1", "b", NA)])
    r = set_counts(v).row(0, named=True)
    assert r["boundCount"] == 0
    assert r["offeredCount"] == 0
    assert r["settledCount"] == 0
    assert r["unsettledCount"] == 0


def test_a_set_entirely_unreliable_reads_as_nothing_settled_not_as_a_bind_failure():
    # All positions UNRELIABLE: boundCount=0, settledCount=0, unsettledCount=N.
    # This is the shape a fully-gated or comparator-less set produces, and it
    # is the one most likely to be misread downstream as "bound none of N" --
    # the honest reading is that nothing settled, since no comparison was
    # ever made.
    v = _v([("s1", "a", U), ("s1", "b", U), ("s1", "c", U)])
    r = set_counts(v).row(0, named=True)
    assert r["boundCount"] == 0
    assert r["settledCount"] == 0
    assert r["unsettledCount"] == 3
    assert r["offeredCount"] == 3


def test_output_row_order_is_deterministic_regardless_of_input_row_order():
    # This becomes a p-column, so it must be byte-stable: the same verdicts
    # fed in several shuffled row orders must produce one identical output,
    # including row order, not merely equal counts.
    rows = (
        [("s3", "a", B), ("s3", "b", N)]
        + [("s1", "a", B), ("s1", "b", U), ("s1", "c", NA)]
        + [("s2", "a", N), ("s2", "b", N)]
    )
    baseline = set_counts(_v(rows))

    rng = random.Random(1234)
    for _ in range(5):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        out = set_counts(_v(shuffled))
        assert out.equals(baseline)


# self_disagreement's states frame is keyed by `key` (an identity or a tag,
# according to `level`), never by `identity`: the same sparse per-cell shape
# `combine_cells` reads, minus a setId column -- set membership comes only
# from `cells_by_set`, matching that function's own rule.
_KEY_STATES_SCHEMA = {"sampleId": pl.String, "cellId": pl.String, "key": pl.String, "state": pl.String}


def _key_states(rows):
    return pl.DataFrame(rows, orient="row", schema=_KEY_STATES_SCHEMA)


def _row_for_key(out, key):
    return out.filter(pl.col("key") == key).row(0, named=True)


def test_a_set_whose_cells_agree_does_not_disagree():
    states = _key_states([("S1", "c1", "A", B), ("S1", "c2", "A", B)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, level="identity")
    r = _row_for_key(out, "A")
    assert r["setsEvaluated"] == 1
    assert r["disagreementRate"] == 0.0


def test_a_set_whose_cells_differ_disagrees():
    states = _key_states([("S1", "c1", "A", B), ("S1", "c2", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, level="identity")
    assert _row_for_key(out, "A")["disagreementRate"] == 1.0


def test_singletons_do_not_contribute():
    states = _key_states([("S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, level="identity")
    assert _row_for_key(out, "A")["setsEvaluated"] == 0


def test_unsettled_cells_are_not_evaluable():
    # c2's row is UNRELIABLE, not silent: it has an explicit row and so is
    # not asked through `silent_tally`, but UNRELIABLE never counts as a
    # settled vote either. One evaluable cell remains -- a singleton -- so
    # the position does not contribute.
    states = _key_states([("S1", "c1", "A", B), ("S1", "c2", "A", U)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, level="identity")
    assert _row_for_key(out, "A")["setsEvaluated"] == 0


def test_both_levels_are_carried_even_when_they_coincide():
    states = _key_states([("S1", "c1", "AAAA", B), ("S1", "c2", "AAAA", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    universe, offered = {"AAAA"}, {"S1": {"AAAA"}}
    ident = self_disagreement(states, universe, offered, cells_by_set, _NEUTRAL, level="identity")
    tag = self_disagreement(states, universe, offered, cells_by_set, _NEUTRAL, level="tag")
    assert ident.height == 1 and tag.height == 1
    assert tag.row(0, named=True)["level"] == "tag"
    assert ident.row(0, named=True)["level"] == "identity"


def test_tag_level_is_marked_diagnostic_only():
    states = _key_states([("S1", "c1", "AAAA", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    universe, offered = {"AAAA"}, {"S1": {"AAAA"}}
    tag = self_disagreement(states, universe, offered, cells_by_set, _NEUTRAL, level="tag")
    ident = self_disagreement(states, universe, offered, cells_by_set, _NEUTRAL, level="identity")
    assert tag.row(0, named=True)["diagnosticOnly"] == "true"
    assert ident.row(0, named=True)["diagnosticOnly"] == "false"


def test_rate_is_over_sets_evaluated_not_all_sets():
    # s2 is a singleton at A and is not evaluable.
    states = _key_states([("S1", "c1", "A", B), ("S1", "c2", "A", N), ("S1", "c3", "A", B)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")], "s2": [("S1", "c3")]}
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, level="identity")
    r = _row_for_key(out, "A")
    assert r["setsEvaluated"] == 1 and r["disagreementRate"] == 1.0


def test_silent_cells_flip_agreement_into_disagreement():
    # THE defect this generalisation exists to fix: a set with 2 observed
    # bound cells and 38 silent, admissible not-bound cells. Counting rows on
    # the sparse frame sees only the 2 bound rows and calls this agreement;
    # the 38 silent cells are settled not-bound votes and the set actually
    # disagrees as badly as it is possible to.
    members = [("S1", "c0"), ("S1", "c1")] + [("S1", f"s{i}") for i in range(38)]
    states = _key_states([("S1", "c0", "A", B), ("S1", "c1", "A", B)])
    cells_by_set = {"s1": members}
    admissibility = Admissibility({k: 5 for k in members}, 2, set())
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility, level="identity")
    r = _row_for_key(out, "A")
    assert r["setsEvaluated"] == 1
    assert r["disagreementRate"] == 1.0


def test_one_observed_positive_among_many_silent_negatives_is_evaluable():
    # A single explicit row is a singleton by row count alone, but 19 silent,
    # admissible cells settle not-bound alongside it: 20 evaluable cells, not
    # a discarded singleton.
    members = [("S1", "c0")] + [("S1", f"s{i}") for i in range(19)]
    states = _key_states([("S1", "c0", "A", B)])
    cells_by_set = {"s1": members}
    admissibility = Admissibility({k: 5 for k in members}, 2, set())
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility, level="identity")
    r = _row_for_key(out, "A")
    assert r["setsEvaluated"] == 1
    assert r["disagreementRate"] == 1.0


def test_all_silent_not_bound_cells_agree():
    # The mirror of the defect test: every cell of the set is silent and
    # admissible, so every one settles not-bound. All evaluable cells give
    # the same settled state, so the set agrees with itself -- this must not
    # be over-corrected into calling every silent set a disagreement.
    members = [("S1", f"s{i}") for i in range(5)]
    states = _key_states([])
    cells_by_set = {"s1": members}
    admissibility = Admissibility({k: 5 for k in members}, 2, set())
    out = self_disagreement(states, {"A"}, {"S1": {"A"}}, cells_by_set, admissibility, level="identity")
    r = _row_for_key(out, "A")
    assert r["setsEvaluated"] == 1
    assert r["disagreementRate"] == 0.0


def test_self_disagreement_output_is_deterministic_regardless_of_input_row_order():
    # This becomes a p-column, so it must be byte-stable across row orders.
    rows = [
        ("S1", "c0", "A", B),
        ("S1", "c1", "A", N),
        ("S1", "d0", "B", B),
        ("S1", "d1", "B", B),
        ("S2", "e0", "A", N),
    ]
    cells_by_set = {
        "s1": [("S1", "c0"), ("S1", "c1")],
        "s2": [("S1", "d0"), ("S1", "d1")],
        "s3": [("S2", "e0")],
    }
    universe = {"A", "B"}
    offered = {"S1": {"A", "B"}, "S2": {"A", "B"}}
    baseline = self_disagreement(_key_states(rows), universe, offered, cells_by_set, _NEUTRAL, level="identity")

    rng = random.Random(2026)
    for _ in range(5):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        out = self_disagreement(_key_states(shuffled), universe, offered, cells_by_set, _NEUTRAL, level="identity")
        assert out.equals(baseline)
