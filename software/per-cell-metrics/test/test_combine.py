import polars as pl
from combine import DEFAULT_MIN_VOTERS, SetUnreliableReason, combine_cells
from verdict import Admissibility, State, combine_tags_to_identities, gate_cells, read_states

B, N, U, NA = (State.BOUND.value, State.NOT_BOUND.value, State.UNRELIABLE.value, State.NEVER_ASKED.value)


_STATES_SCHEMA = {
    "setId": pl.String,
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
    df = _states([("s1", "S1", "c1", "A", B), ("s1", "S1", "c2", "A", B), ("s1", "S1", "c3", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2"), ("S1", "c3")]}
    out = combine_cells(df, universe={"A"}, offered={"S1": {"A"}}, cells_by_set=cells_by_set, admissibility=_NEUTRAL)
    r = _row(out, "A")
    assert r["state"] == B and r["cellsAnswered"] == 3 and r["agreement"] == 2 / 3


def test_vote_is_per_identity_so_a_set_can_bind_several():
    df = _states([("s1", "S1", "c1", i, B) for i in ("A", "C")])
    cells_by_set = {"s1": [("S1", "c1")]}
    out = combine_cells(
        df, universe={"A", "C"}, offered={"S1": {"A", "C"}}, cells_by_set=cells_by_set, admissibility=_NEUTRAL
    ).sort("identity")
    assert out["state"].to_list() == [B, B]


def test_a_tie_cannot_be_settled():
    df = _states([("s1", "S1", "c1", "A", B), ("s1", "S1", "c2", "A", N)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    out = combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL)
    r = _row(out, "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.TIE.value


def test_a_three_way_split_that_ties_at_the_top_is_also_unreliable():
    # Not just the minimal 1-vs-1 tie: three cells settle bound, three settle
    # not bound. The tie check must compare the leading counts, not special-
    # case a count of one.
    df = _states([("s1", "S1", f"b{i}", "A", B) for i in range(3)] + [("s1", "S1", f"n{i}", "A", N) for i in range(3)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", f"n{i}") for i in range(3)]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == U and r["cellsAnswered"] == 6
    assert r["unreliableReason"] == SetUnreliableReason.TIE.value


def test_never_asked_comes_only_from_not_being_offered():
    # Z is in the universe and NOT offered -> never asked.
    df = _states([("s1", "S1", "c1", "A", B)])
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
    df = _states([("s1", "S1", "c1", "A", N), ("s1", "S1", "c2", "A", N)])
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
    df = _states([("s1", "S1", "c1", "A", B), ("s1", "S1", "c2", "A", U), ("s1", "S1", "c3", "A", U)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2"), ("S1", "c3")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["cellsAnswered"] == 1 and r["cellsCouldAnswer"] == 3


def test_a_verdict_may_rest_on_one_cell_and_says_so():
    assert DEFAULT_MIN_VOTERS == 1
    df = _states([("s1", "S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["cellsAnswered"] == 1


def test_below_min_voters_is_unreliable_when_raised():
    df = _states([("s1", "S1", "c1", "A", B)])
    cells_by_set = {"s1": [("S1", "c1")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_voters=2), "A")
    assert r["state"] == U
    assert r["unreliableReason"] == SetUnreliableReason.TOO_FEW_VOTERS.value


def test_exactly_min_voters_settles():
    # The named value satisfies the condition it names, as elsewhere in this
    # project: two settled votes with min_voters=2 must settle, not fail.
    df = _states([("s1", "S1", "c1", "A", B), ("s1", "S1", "c2", "A", B)])
    cells_by_set = {"s1": [("S1", "c1"), ("S1", "c2")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_voters=2), "A")
    assert r["state"] == B and r["cellsAnswered"] == 2


def test_narrow_majority_stands_and_reports_how_narrow():
    df = _states([("s1", "S1", f"c{i}", "A", B) for i in range(6)] + [("s1", "S1", f"d{i}", "A", N) for i in range(5)])
    cells_by_set = {"s1": [("S1", f"c{i}") for i in range(6)] + [("S1", f"d{i}") for i in range(5)]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL), "A")
    assert r["state"] == B and r["agreement"] == 6 / 11


def test_exactly_min_agreement_settles_when_raised():
    # 3 bound, 1 not bound -> agreement 0.75. Raising min_agreement to
    # exactly 0.75 must still settle: the boundary belongs to the pass side.
    df = _states([("s1", "S1", f"b{i}", "A", B) for i in range(3)] + [("s1", "S1", "n0", "A", N)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", "n0")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_agreement=0.75), "A")
    assert r["state"] == B and r["agreement"] == 0.75


def test_just_below_min_agreement_is_unreliable():
    df = _states([("s1", "S1", f"b{i}", "A", B) for i in range(3)] + [("s1", "S1", "n0", "A", N)])
    cells_by_set = {"s1": [("S1", f"b{i}") for i in range(3)] + [("S1", "n0")]}
    r = _row(combine_cells(df, {"A"}, {"S1": {"A"}}, cells_by_set, _NEUTRAL, min_agreement=0.76), "A")
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

    states = per_cell.with_columns(pl.lit("s1").alias("setId")).select(
        "setId", "sampleId", "cellId", "identity", "state"
    )
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
    explicit = [("s1", "S1", "c0", "A", B), ("s1", "S1", "c1", "A", B), ("s1", "S1", "c2", "A", N)]
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
