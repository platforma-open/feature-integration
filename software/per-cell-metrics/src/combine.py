"""Reducing a set's cells to the set's verdict, identity by identity.

Cells of one set are replicates of one measurement, so where they differ at
an identity the difference is error and the modal answer is the best
available reading of what the receptor did. The vote is per identity: a
single winning antigen collapses a set that bound several, which the model
this reduces from -- four states per (cell, identity) -- exists to keep
distinct.

The row set is the identity universe, never the offered subset: a set's
verdict at an identity the panel never offered is NEVER_ASKED, and that
comes only from the offered map, never from a row's absence. `offered` is
keyed by sample, matching `silent_tally`'s `offered_by_sample` -- what a
panel offered is a property of the staining, not of the clonotype grouping
built on top of it -- so a set's own offered set is the union, over its
member samples, of what each sample's panel offered.

A cell asked about an identity and showing no reading in `states` is silent,
not absent from the vote: `silent_tally`, generalised to key its tally by an
arbitrary per-cell group rather than only by sample, supplies the silent
contribution here. A silent admissible cell always resolves NOT_BOUND (see
`specificity_score` in verdict.py), so silent cells vote not bound; silent
inadmissible cells vote nowhere, which is exactly what keeps a set every one
of whose cells failed to bind reading NOT_BOUND rather than UNRELIABLE or
NEVER_ASKED.
"""

from __future__ import annotations

from enum import Enum

import polars as pl
from verdict import Admissibility, State, UnreliableReason, _cell_admissibility_reason, silent_tally

# Both limits default permissively because the failure they would prevent is
# visible and the failure they would cause is not. Requiring two voting cells
# would silently discard every singleton, which many clonotypes in a run are.
DEFAULT_MIN_VOTERS = 1
DEFAULT_MIN_AGREEMENT = None

SETTLED = (State.BOUND.value, State.NOT_BOUND.value)


class SetUnreliableReason(str, Enum):
    """Why a set's verdict at one identity could not be settled, or why it
    was never asked. Cell-level admissibility (`verdict.UnreliableReason`)
    answers "why can't this cell be compared"; this answers "why can't this
    set's cells, taken together, produce a verdict" -- a different grain,
    kept in its own enum rather than folded into the cell-level one, whose
    own docstring scopes it to a single cell's comparison.

    NEVER_OFFERED is the reason recorded on a NEVER_ASKED row: not itself a
    reliability problem, but the same column carries it, so a reader always
    finds a reason there whenever the state is not BOUND or NOT_BOUND.

    NO_COMPARATOR and THIN_COMPARATOR reuse the cell-level vocabulary's
    concepts because they describe the same underlying fact, just observed
    for a whole set rather than one cell: no settled vote exists because
    every one of the set's asked cells individually failed the same
    comparator check. ALL_CELLS_GATED is reported only when every asked cell
    was gated with no other reason mixed in; a set with a mix of gated and
    comparator-failed cells is reported by whichever comparator failure is
    present, since an admissibility gate excluding only part of a set is not
    by itself why the rest failed to settle.

    TIE and BELOW_AGREEMENT_FLOOR both leave the identity UNRELIABLE, and
    look alike from the counts alone, but they call for different action and
    so are kept apart. A TIE has no majority to trust: the settled cells
    split evenly, which may be real heterogeneity in the clone, and no
    parameter moves it. A BELOW_AGREEMENT_FLOOR set has one: a majority
    formed, and it was refused only because `min_agreement` was raised above
    it -- the fix is to lower that floor or gather more cells, not to
    suspect the biology. Since `min_agreement` defaults to off, this reason
    can only appear because someone raised it.
    """

    NEVER_OFFERED = "never-offered"
    NO_COMPARATOR = "no-comparator"
    THIN_COMPARATOR = "thin-comparator"
    ALL_CELLS_GATED = "all-cells-gated"
    TIE = "tie"
    BELOW_AGREEMENT_FLOOR = "below-agreement-floor"
    TOO_FEW_VOTERS = "too-few-voters"


def _dominant_reason(asked_keys: list[tuple[str, str]], admissibility: Admissibility) -> SetUnreliableReason:
    """The one reason that explains why none of `asked_keys` settled.

    Called only when the set's tally has zero settled votes for the
    identity, which happens only when every asked cell is individually
    inadmissible: an admissible cell always settles, either directly (a
    BOUND or NOT_BOUND row) or, if silent, through `silent_tally`'s proof
    that a silent admissible cell always resolves NOT_BOUND. So every key
    here has a real, non-None cell-level reason, and this only has to pick
    among the three.

    The assertion is not a formality: it is the difference between that
    claim failing loudly and failing as a wrong answer. Without it, an
    admissible key slipping in here (the caller's vote-counting and its
    admissibility disagreeing about which cells were asked) reads a `None`
    reason, matches neither of the two checks below, and falls through to
    THIN_COMPARATOR -- reporting a comparator problem for a cell whose
    comparator is fine.
    """
    reasons = {_cell_admissibility_reason(k, admissibility) for k in asked_keys}
    # Raised rather than asserted: stripped under -O this does not crash, it falls through to
    # THIN_COMPARATOR and reports a comparator problem for a cell whose comparator is fine -- the exact
    # wrong answer the docstring above says the check is here to turn into a loud failure.
    if None in reasons:
        raise ValueError(
            f"an admissible cell reached _dominant_reason among {asked_keys!r}: this is only called "
            "when cellsAnswered is 0, which should be possible only when every asked cell is "
            "individually inadmissible -- a None reason here means the caller's vote count and "
            "admissibility disagree about which cells were actually asked"
        )
    if reasons == {UnreliableReason.GATED}:
        return SetUnreliableReason.ALL_CELLS_GATED
    if UnreliableReason.NO_COMPARATOR in reasons:
        return SetUnreliableReason.NO_COMPARATOR
    return SetUnreliableReason.THIN_COMPARATOR


def _majority(counts: dict[str, int]) -> tuple[str, int, bool]:
    """The leading state, its count, and whether more than one state is tied for it."""
    top = max(counts.values())
    leaders = sorted(state for state, n in counts.items() if n == top)
    return leaders[0], top, len(leaders) > 1


def combine_cells(
    states: pl.DataFrame,
    universe: set[str],
    offered: dict[str, set[str]],
    cells_by_set: dict[str, list[tuple[str, str]]],
    admissibility: Admissibility,
    min_voters: int = DEFAULT_MIN_VOTERS,
    min_agreement: float | None = DEFAULT_MIN_AGREEMENT,
) -> pl.DataFrame:
    """One row per (set, identity) over the whole universe.

    `states` is `read_states`' output directly -- columns sampleId, cellId,
    identity, state, plus whatever else `read_states` emits, which this
    ignores -- one row per (cell, identity) that got an explicit reading; a
    cell asked about an identity and absent here is silent for it, not
    unasked. There is deliberately no setId column in that shape: which set
    a row belongs to is decided once, below, by looking its cell up in
    `cells_by_set` -- never by trusting a column, which would be a second,
    independently-suppliable source of truth for the same fact. A row for a
    cell no set in `cells_by_set` lists is dropped, exactly as `silent_tally`
    already drops such cells from its own counts; a vote is never counted
    for a cell that was not asked.

    `offered` is keyed by sample: for a given set, the identities it was
    offered are the union, over its member samples, of what each sample's
    panel offered -- so a set spanning two samples with different panels
    reads as offered whatever either panel offered, while `cellsCouldAnswer`
    below still counts only the members whose OWN sample offered that
    specific identity.

    `cells_by_set` gives each set's full cell membership, including cells
    with no row in `states` at all -- the set's silent cells, which vote
    through `silent_tally` rather than through a row that was never written.
    It must be disjoint: a cell key may repeat within one set's own list
    with no effect, but must not appear under two different set ids, which
    is asserted below rather than left to surface later as a `silent_tally`
    precondition failure whose message points at the wrong function.
    """
    group_by_cell: dict[tuple[str, str], str] = {}
    for set_id, members in cells_by_set.items():
        for key in members:
            owner = group_by_cell.get(key)
            # Raised rather than asserted: under -O an assert vanishes, and this one vanishing does not
            # crash -- it double-counts the cell into two sets and reports tallies that are simply wrong,
            # which is the failure the docstring above says this check exists to prevent.
            if owner is not None and owner != set_id:
                raise ValueError(
                    f"cell {key!r} appears in both set {owner!r} and set {set_id!r} in cells_by_set: "
                    "a cell must belong to exactly one set"
                )
            group_by_cell[key] = set_id

    cells_frame = pl.DataFrame(list(group_by_cell), orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    tally = silent_tally(states, cells_frame, offered, admissibility, group_by_cell=group_by_cell, group_column="setId")
    silent_by_pair = {(row["setId"], row["identity"]): row for row in tally.iter_rows(named=True)}

    settled = states.filter(pl.col("state").is_in(SETTLED))
    explicit_counts: dict[tuple[str, str], dict[str, int]] = {}
    for sample_id, cell_id, identity, state in zip(
        settled["sampleId"].to_list(),
        settled["cellId"].to_list(),
        settled["identity"].to_list(),
        settled["state"].to_list(),
        strict=True,
    ):
        set_id = group_by_cell.get((sample_id, cell_id))
        if set_id is None:
            # This cell is not in any set's membership list: the same drop
            # `silent_tally` applies to a cell absent from its `cells` frame,
            # kept here so a vote can never be counted for a cell nobody
            # asked to vote.
            continue
        if identity not in offered.get(sample_id, frozenset()):
            # This cell's own sample never offered the identity, so the cell
            # was never asked about it and its reading is not a vote. The
            # denominator below counts only members whose own sample offered
            # it; counting the vote here would mix two populations, and where
            # a set sits in one sample this reading is already discarded as
            # never-asked. The multi-sample case now behaves the same way.
            continue
        bucket = explicit_counts.setdefault((set_id, identity), {})
        bucket[state] = bucket.get(state, 0) + 1

    rows = []
    for set_id in sorted(cells_by_set):
        members = cells_by_set[set_id]
        offered_for_set = set().union(*(offered.get(sample, set()) for sample, _ in members)) if members else set()

        for identity in sorted(universe):
            if identity not in offered_for_set:
                rows.append(
                    {
                        "setId": set_id,
                        "identity": identity,
                        "state": State.NEVER_ASKED.value,
                        "cellsCouldAnswer": 0,
                        "cellsAnswered": 0,
                        # No tally exists for a position never put to this clonotype. 0 is the honest
                        # count; a null would ride into the punch value as an empty field. Same reasoning
                        # for cellsNotBound: nothing was ever asked, so nothing was ever answered either way.
                        "cellsBound": 0,
                        "cellsNotBound": 0,
                        "agreement": None,
                        "unreliableReason": SetUnreliableReason.NEVER_OFFERED.value,
                    }
                )
                continue

            # Guaranteed present: `identity` is in `offered_for_set` only
            # because at least one member's own sample offers it, which is
            # exactly the condition under which silent_tally emits a row for
            # (set_id, identity).
            silent_row = silent_by_pair[(set_id, identity)]
            could = silent_row["asked"]

            counts = dict(explicit_counts.get((set_id, identity), {}))
            counts[State.NOT_BOUND.value] = counts.get(State.NOT_BOUND.value, 0) + silent_row["silentNotBound"]
            answered = sum(counts.values())

            if answered == 0:
                asked_keys = [key for key in members if identity in offered.get(key[0], set())]
                reason = _dominant_reason(asked_keys, admissibility)
                rows.append(
                    {
                        "setId": set_id,
                        "identity": identity,
                        "state": State.UNRELIABLE.value,
                        "cellsCouldAnswer": could,
                        "cellsAnswered": 0,
                        "cellsBound": 0,
                        "cellsNotBound": 0,
                        "agreement": None,
                        "unreliableReason": reason.value,
                    }
                )
                continue

            if answered < min_voters:
                rows.append(
                    {
                        "setId": set_id,
                        "identity": identity,
                        "state": State.UNRELIABLE.value,
                        "cellsCouldAnswer": could,
                        "cellsAnswered": answered,
                        "cellsBound": counts.get(State.BOUND.value, 0),
                        "cellsNotBound": counts.get(State.NOT_BOUND.value, 0),
                        "agreement": None,
                        "unreliableReason": SetUnreliableReason.TOO_FEW_VOTERS.value,
                    }
                )
                continue

            top_state, top_count, tied = _majority(counts)
            agreement = top_count / answered

            # A tie has no majority to trust: the settled cells split evenly,
            # and nothing in the reading says which side to believe. A narrow
            # majority below the agreement floor has one -- it was refused
            # only because the operator raised that floor. The two states
            # this identity could still not settle in call for different
            # action, so they get different reasons.
            if tied:
                rows.append(
                    {
                        "setId": set_id,
                        "identity": identity,
                        "state": State.UNRELIABLE.value,
                        "cellsCouldAnswer": could,
                        "cellsAnswered": answered,
                        "cellsBound": counts.get(State.BOUND.value, 0),
                        "cellsNotBound": counts.get(State.NOT_BOUND.value, 0),
                        "agreement": agreement,
                        "unreliableReason": SetUnreliableReason.TIE.value,
                    }
                )
                continue

            if min_agreement is not None and agreement < min_agreement:
                rows.append(
                    {
                        "setId": set_id,
                        "identity": identity,
                        "state": State.UNRELIABLE.value,
                        "cellsCouldAnswer": could,
                        "cellsAnswered": answered,
                        "cellsBound": counts.get(State.BOUND.value, 0),
                        "cellsNotBound": counts.get(State.NOT_BOUND.value, 0),
                        "agreement": agreement,
                        "unreliableReason": SetUnreliableReason.BELOW_AGREEMENT_FLOOR.value,
                    }
                )
                continue

            rows.append(
                {
                    "setId": set_id,
                    "identity": identity,
                    "state": top_state,
                    "cellsCouldAnswer": could,
                    "cellsAnswered": answered,
                    # Read from the tally, never from `agreement`. `agreement` is top_count/answered --
                    # the MAJORITY's share -- and the majority is not always bound, so deriving a bound
                    # count from it reports the wrong state's cells wherever the verdict is not bound.
                    "cellsBound": counts.get(State.BOUND.value, 0),
                    # Same reasoning as cellsBound above: SETTLED holds only BOUND and NOT_BOUND, so this
                    # could be derived as cellsAnswered - cellsBound, but reading it from the tally directly
                    # does not lean on which state happened to be top_state, and stays correct unchanged if
                    # that ever stops being true.
                    "cellsNotBound": counts.get(State.NOT_BOUND.value, 0),
                    "agreement": agreement,
                    "unreliableReason": None,
                }
            )

    return pl.DataFrame(
        rows,
        schema={
            "setId": pl.String,
            "identity": pl.String,
            "state": pl.String,
            "cellsCouldAnswer": pl.Int64,
            "cellsAnswered": pl.Int64,
            "cellsBound": pl.Int64,
            "cellsNotBound": pl.Int64,
            "agreement": pl.Float64,
            "unreliableReason": pl.String,
        },
    ).sort(["setId", "identity"])


def attach_competitor_notes(verdicts: pl.DataFrame, contending: list[set[str]]) -> pl.DataFrame:
    """Name the bound competitor beside a not-bound reading; change nothing else.

    A negative beside a bound competitor and one beside nothing are different
    evidence, and the counts cannot tell them apart. The verdict reports what
    could have caused the reading and leaves the call to the reader; the state
    stays at *not bound* because the doubt travels beside it. Only a settled
    NOT_BOUND row is eligible: an UNRELIABLE or NEVER_ASKED row made no
    comparison to begin with, so it has no negative for a competitor to sit
    beside.

    `wasCompeted` exists so a statement can test the note. Without a predicate
    a condition naming the off-target passes on the state alone, the doubt is
    lost, and *not bound* becomes a claim the run never earned. It is emitted
    as an explicit "true"/"false" string on every row, matching the convention
    this project already uses for a boolean that becomes a p-column value
    (see `emit_feature_properties.py`'s control-feature marker) -- never null,
    because a downstream filter for the absence of contention must be able to
    match on the flag alone.

    Which identities contend is chosen when the repertoire is annotated and is
    never inferred from the counts: contention is a property of the design, so
    no arithmetic over the readings recovers it. An identity may sit in more
    than one declared group; the note then names the union of bound
    competitors across every group that contains it, since each group is an
    independent claim of contention and a reader has no reason to see only
    one of them. The names are joined in sorted order so the same data always
    produces the same string, which matters once this column is
    content-addressed as a p-column.
    """
    if not contending:
        return verdicts.with_columns(
            pl.lit(None, dtype=pl.String).alias("competedWith"),
            pl.lit("false", dtype=pl.String).alias("wasCompeted"),
        )

    bound_by_set: dict[str, set[str]] = {}
    for row in verdicts.filter(pl.col("state") == State.BOUND.value).iter_rows(named=True):
        bound_by_set.setdefault(row["setId"], set()).add(row["identity"])

    notes, flags = [], []
    for row in verdicts.iter_rows(named=True):
        note = None
        if row["state"] == State.NOT_BOUND.value:
            bound_here = bound_by_set.get(row["setId"], set())
            rivals = {
                other
                for group in contending
                if row["identity"] in group
                for other in group & bound_here
                if other != row["identity"]
            }
            if rivals:
                note = ", ".join(sorted(rivals))
        notes.append(note)
        flags.append("true" if note else "false")

    return verdicts.with_columns(
        pl.Series("competedWith", notes, dtype=pl.String),
        pl.Series("wasCompeted", flags, dtype=pl.String),
    )


def set_counts(verdicts: pl.DataFrame) -> pl.DataFrame:
    """Per set: bound, offered, settled, unsettled -- in identities.

    The denominator is the identities the set was offered and whose reading
    settled, not the size of the panel. A clonotype whose cells came from a
    sample carrying only eight of ten, and which bound all eight, covered
    everything it was ever asked; reported as eight of ten it looks like a
    clone with two failures.

    An offered position that did not settle leaves the count and is reported
    beside it. Voiding the count instead is what the four-state model
    literally implies, but on a large panel a single bad reading would then
    destroy every count in the run.

    `offeredCount` always equals `settledCount + unsettledCount`, since
    UNRELIABLE is the only offered-but-unsettled state. A set asked nothing
    (every identity NEVER_ASKED) reports all four counts as zero; a consumer
    computing a rate from `boundCount` and `offeredCount` must guard the
    division themselves, since this function cannot produce a rate for a set
    that was asked nothing. A set that is entirely UNRELIABLE reports
    `boundCount=0, settledCount=0, unsettledCount=N` -- read that as nothing
    settled, never as a failure to bind N identities, since none of them
    were ever compared.

    `verdicts` is read at its existing (setId, identity) row grain, one row
    per identity regardless of how many tags fed it, so counting rows counts
    identities, never tags.
    """
    return (
        verdicts.group_by("setId")
        .agg(
            (pl.col("state") == State.BOUND.value).sum().alias("boundCount"),
            (pl.col("state") != State.NEVER_ASKED.value).sum().alias("offeredCount"),
            pl.col("state").is_in(SETTLED).sum().alias("settledCount"),
            (pl.col("state") == State.UNRELIABLE.value).sum().alias("unsettledCount"),
        )
        .sort("setId")
    )


def self_disagreement(
    states: pl.DataFrame,
    universe: set[str],
    offered: dict[str, set[str]],
    cells_by_set: dict[str, list[tuple[str, str]]],
    admissibility: Admissibility,
    level: str,
) -> pl.DataFrame:
    """How often sets contradict themselves, at an identity or at a tag.

    A clonotype is one receptor and one receptor has one specificity, so
    where two of a set's evaluable cells read differently at one position, at
    least one reading is wrong. That makes this the cheapest quality signal
    available: no threshold and no external reference, because the
    contradiction comes from the data disagreeing with itself.

    A set's evaluable cells at a position are every admissible cell that
    settled there, whether the reading is an explicit row in `states` or
    silent. A silent admissible cell always resolves not bound (see
    `specificity_score` in verdict.py), so it is as evaluable as an explicit
    row and votes not bound; an inadmissible cell, silent or explicit, never
    votes. The silent count comes from `silent_tally`, generalised to key its
    tally by set rather than only by sample -- the same source
    `combine_cells` draws on for the same fact, never recomputed here.

    Both levels are always carried by calling this twice, once per level. The
    two answer different questions: a tag with a high rate is a reagent
    misbehaving for everyone; an identity with a high rate is the answer a
    scientist acts on being unstable. Where an identity carries one tag the
    two coincide, and saying so beats dropping a row a reader would then hunt
    for.

    The tag figure is diagnostic only. It rests on comparing each tag against
    the reference separately, which no verdict is built from, so it is never
    read as evidence about an answer.

    `states` carries a `key` column holding the identity or the tag according
    to `level`, plus sampleId, cellId and state -- the same sparse shape
    `combine_cells` takes as `states`, with `identity` renamed to `key` and
    with no setId column: which set a cell belongs to comes only from
    `cells_by_set`, never from a second column that could disagree with it.
    `offered` and `universe` are at that same grain: the identities or the
    tags each sample offers, and the full set of keys to report on, since a
    key with every one of its cells silent has no explicit row anywhere in
    `states` and cannot be recovered from it.

    Only a set's position with two or more evaluable cells contributes: a
    singleton cannot disagree with itself. The rate is over sets evaluated,
    not every set that exists, so a key nobody could evaluate reports a null
    rate rather than a rate of zero, which would read as agreement.
    """
    group_by_cell: dict[tuple[str, str], str] = {}
    for set_id, members in cells_by_set.items():
        for cell_key in members:
            owner = group_by_cell.get(cell_key)
            # Raised rather than asserted: under -O an assert vanishes, and this one vanishing does not
            # crash -- it double-counts the cell into two sets and reports tallies that are simply wrong,
            # which is the failure the docstring above says this check exists to prevent.
            if owner is not None and owner != set_id:
                raise ValueError(
                    f"cell {cell_key!r} appears in both set {owner!r} and set {set_id!r} in cells_by_set: "
                    "a cell must belong to exactly one set"
                )
            group_by_cell[cell_key] = set_id

    cells_frame = pl.DataFrame(list(group_by_cell), orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    observed_for_tally = states.select("sampleId", "cellId", pl.col("key").alias("identity"))
    tally = silent_tally(
        observed_for_tally, cells_frame, offered, admissibility, group_by_cell=group_by_cell, group_column="setId"
    )

    settled = states.filter(pl.col("state").is_in(SETTLED))
    explicit_counts: dict[tuple[str, str], dict[str, int]] = {}
    for sample_id, cell_id, key, state in zip(
        settled["sampleId"].to_list(),
        settled["cellId"].to_list(),
        settled["key"].to_list(),
        settled["state"].to_list(),
        strict=True,
    ):
        set_id = group_by_cell.get((sample_id, cell_id))
        if set_id is None:
            # Same drop `combine_cells` applies: a vote is never counted for
            # a cell that no set's membership list names.
            continue
        if key not in offered.get(sample_id, frozenset()):
            # This cell's own sample never offered the identity, so the cell
            # was never asked about it and its reading is not a vote. The
            # denominator below counts only members whose own sample offered
            # it; counting the vote here would mix two populations, and where
            # a set sits in one sample this reading is already discarded as
            # never-asked. The multi-sample case now behaves the same way.
            continue
        bucket = explicit_counts.setdefault((set_id, key), {})
        bucket[state] = bucket.get(state, 0) + 1

    sets_evaluated: dict[str, int] = {}
    sets_disagreeing: dict[str, int] = {}
    for row in tally.iter_rows(named=True):
        set_id, key = row["setId"], row["identity"]
        counts = dict(explicit_counts.get((set_id, key), {}))
        counts[State.NOT_BOUND.value] = counts.get(State.NOT_BOUND.value, 0) + row["silentNotBound"]
        evaluable = sum(counts.values())
        if evaluable < 2:
            continue
        sets_evaluated[key] = sets_evaluated.get(key, 0) + 1
        if sum(1 for n in counts.values() if n > 0) > 1:
            sets_disagreeing[key] = sets_disagreeing.get(key, 0) + 1

    return (
        pl.DataFrame({"key": sorted(universe)})
        .with_columns(
            pl.col("key").replace_strict(sets_evaluated, default=0, return_dtype=pl.Int64).alias("setsEvaluated"),
            pl.col("key").replace_strict(sets_disagreeing, default=0, return_dtype=pl.Int64).alias("setsDisagreeing"),
        )
        .with_columns(
            pl.when(pl.col("setsEvaluated") > 0)
            .then(pl.col("setsDisagreeing") / pl.col("setsEvaluated"))
            .otherwise(None)
            .alias("disagreementRate"),
            pl.lit(level).alias("level"),
            pl.lit("true" if level == "tag" else "false").alias("diagnosticOnly"),
        )
        .sort("key")
    )
