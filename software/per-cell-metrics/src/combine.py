"""Reducing a set's cells to the set's verdict, identity by identity.

Cells of one set are replicates of one measurement, so where they differ at an
identity the difference is error and the modal answer is the best available
reading. The vote is per identity: a single winning antigen would collapse a set
that bound several.

The row set is the identity universe, never the offered subset. A set's verdict at
an identity the panel never offered is NEVER_ASKED, and that comes only from the
offered map, never from a row's absence. `offered` is keyed by sample, matching
`silent_tally`'s `offered_by_sample`, because staining is a property of the sample
rather than of the clonotype grouping built on it. A set's offered set is therefore
the union over its member samples.

A cell asked about an identity and showing no reading in `states` is silent, not
absent from the vote. `silent_tally` supplies the silent contribution. A silent
admissible cell always resolves NOT_BOUND (see `specificity_score` in verdict.py),
so silent cells vote not bound and silent inadmissible cells vote nowhere. That is
what keeps a set whose every cell failed to bind reading NOT_BOUND rather than
UNRELIABLE or NEVER_ASKED.
"""

from __future__ import annotations

from enum import Enum

import polars as pl
from verdict import Admissibility, State, UnreliableReason, _admissibility_reason, silent_tally

# Both limits default permissively, because the failure they would prevent is visible
# and the failure they would cause is not. Requiring two voting cells would silently
# discard every singleton, which many clonotypes in a run are.
DEFAULT_MIN_VOTERS = 1
DEFAULT_MIN_AGREEMENT = None

SETTLED = (State.BOUND.value, State.NOT_BOUND.value)


class SetUnreliableReason(str, Enum):
    """Why a set's verdict at one identity could not be settled, or why it was never
    asked. `verdict.UnreliableReason` answers "why can't this cell be compared". This
    answers "why can't this set's cells, taken together, produce a verdict" -- a
    different grain, kept in its own enum.

    NEVER_OFFERED is the reason recorded on a NEVER_ASKED row. It is not a reliability
    problem, but the same column carries it, so a reader always finds a reason wherever
    the state is not BOUND or NOT_BOUND.

    NO_COMPARATOR reuses the cell-level concept for the same fact observed over a whole
    set: no settled vote exists because every asked cell individually had no
    comparator. ALL_CELLS_GATED is reported only when every asked cell was gated with
    no other reason mixed in. A mix of gated and comparator-less cells reports as
    NO_COMPARATOR, since a gate excluding part of a set is not why the rest failed.

    TIE and BELOW_AGREEMENT_FLOOR both leave the identity UNRELIABLE and look alike
    from the counts, but call for different action. A TIE has no majority to trust: the
    settled cells split evenly, which may be real heterogeneity, and no parameter moves
    it. A BELOW_AGREEMENT_FLOOR set formed a majority and was refused only because
    `min_agreement` was raised above it -- the fix is to lower that floor or gather more
    cells, not to suspect the biology. `min_agreement` defaults to off, so this reason
    appears only because someone raised it.
    """

    NEVER_OFFERED = "never-offered"
    NO_COMPARATOR = "no-comparator"
    ALL_CELLS_GATED = "all-cells-gated"
    TIE = "tie"
    BELOW_AGREEMENT_FLOOR = "below-agreement-floor"
    TOO_FEW_VOTERS = "too-few-voters"


def _dominant_reason(
    asked_keys: list[tuple[str, str]], identity: str, admissibility: Admissibility
) -> SetUnreliableReason:
    """The one reason that explains why none of `asked_keys` settled.

    Called only when the set has zero settled votes for the identity, which happens only
    when every asked cell is individually inadmissible: an admissible cell always
    settles, directly or, if silent, through `silent_tally`'s proof that a silent
    admissible cell resolves NOT_BOUND. So every key here has a real, non-None
    cell-level reason and this only picks among three.

    The assertion is not a formality. Without it, an admissible key slipping in -- the
    caller's vote-counting and its admissibility disagreeing about which cells were
    asked -- reads a `None` reason, misses the gated check, and falls through to
    NO_COMPARATOR, reporting a missing comparator for a cell whose comparator is fine.
    """
    # Takes the identity because one rung's comparator depends on it. A set can fail to
    # settle one identity and settle every other.
    reasons = {_admissibility_reason(k, identity, admissibility) for k in asked_keys}
    # Raised rather than asserted: stripped under -O this does not crash, it falls through
    # to NO_COMPARATOR and reports a missing comparator for a cell whose comparator is
    # fine -- the exact wrong answer this check exists to turn into a loud failure.
    if None in reasons:
        raise ValueError(
            f"an admissible cell reached _dominant_reason among {asked_keys!r}: this is only called "
            "when cellsAnswered is 0, which should be possible only when every asked cell is "
            "individually inadmissible -- a None reason here means the caller's vote count and "
            "admissibility disagree about which cells were actually asked"
        )
    if reasons == {UnreliableReason.GATED}:
        return SetUnreliableReason.ALL_CELLS_GATED
    return SetUnreliableReason.NO_COMPARATOR


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

    `states` is `read_states`' output directly: one row per (cell, identity) that got an
    explicit reading. A cell asked about an identity and absent here is silent for it,
    not unasked. There is deliberately no setId column in that shape -- which set a row
    belongs to is decided once, below, by looking the cell up in `cells_by_set`, never
    by trusting a column that could disagree. A row for a cell no set lists is dropped,
    exactly as `silent_tally` drops such cells.

    `offered` is keyed by sample: a set's offered identities are the union over its
    member samples, so a set spanning two panels reads as offered whatever either
    offered, while `cellsCouldAnswer` still counts only members whose OWN sample offered
    that identity.

    `cells_by_set` gives each set's full membership, including cells with no row in
    `states` -- the silent cells, which vote through `silent_tally`. It must be disjoint:
    a cell key may repeat within one set's list with no effect, but must not appear
    under two set ids, which is asserted below rather than surfacing later as a
    `silent_tally` precondition failure pointing at the wrong function.
    """
    group_by_cell: dict[tuple[str, str], str] = {}
    for set_id, members in cells_by_set.items():
        for key in members:
            owner = group_by_cell.get(key)
            # Raised rather than asserted: under -O an assert vanishes, and this one
            # vanishing does not crash -- it double-counts the cell into two sets and
            # reports tallies that are simply wrong.
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
            # In no set's membership list: the same drop `silent_tally` applies, so a
            # vote is never counted for a cell nobody asked to vote.
            continue
        if identity not in offered.get(sample_id, frozenset()):
            # This cell's own sample never offered the identity, so its reading is not a
            # vote. The denominator counts only members whose own sample offered it, so
            # counting this would mix two populations.
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
                        # No tally exists for a position never put to this clonotype. 0
                        # is the honest count -- a null would ride into the punch value
                        # as an empty field. Same for cellsNotBound.
                        "cellsBound": 0,
                        "cellsNotBound": 0,
                        "agreement": None,
                        "unreliableReason": SetUnreliableReason.NEVER_OFFERED.value,
                    }
                )
                continue

            # Guaranteed present: `identity` is in `offered_for_set` only because at
            # least one member's own sample offers it, which is exactly when
            # silent_tally emits a row for (set_id, identity).
            silent_row = silent_by_pair[(set_id, identity)]
            could = silent_row["asked"]

            counts = dict(explicit_counts.get((set_id, identity), {}))
            counts[State.NOT_BOUND.value] = counts.get(State.NOT_BOUND.value, 0) + silent_row["silentNotBound"]
            answered = sum(counts.values())

            if answered == 0:
                asked_keys = [key for key in members if identity in offered.get(key[0], set())]
                reason = _dominant_reason(asked_keys, identity, admissibility)
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

            # A tie has no majority to trust: the settled cells split evenly and nothing
            # says which side to believe. A narrow majority below the agreement floor
            # has one, refused only because the operator raised that floor. Different
            # action, so different reasons.
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
                    # Read from the tally, never from `agreement`. `agreement` is
                    # top_count/answered -- the MAJORITY's share -- and the majority is
                    # not always bound, so deriving a bound count from it reports the
                    # wrong state's cells wherever the verdict is not bound.
                    "cellsBound": counts.get(State.BOUND.value, 0),
                    # Same reasoning as cellsBound. SETTLED holds only BOUND and
                    # NOT_BOUND, so this could be cellsAnswered - cellsBound, but reading
                    # the tally directly does not lean on which state was top_state.
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
    """Name the bound competitor beside a not-bound reading. Change nothing else.

    A negative beside a bound competitor and one beside nothing are different evidence,
    and the counts cannot tell them apart. The verdict reports what could have caused
    the reading and leaves the call to the reader. The state stays *not bound*, because
    the doubt travels beside it. Only a settled NOT_BOUND row is eligible: an UNRELIABLE
    or NEVER_ASKED row made no comparison, so it has no negative to sit beside.

    `wasCompeted` exists so a statement can test the note. Without a predicate, a
    condition naming the off-target passes on the state alone, the doubt is lost, and
    *not bound* becomes a claim the run never earned. It is an explicit "true"/"false"
    string on every row -- the convention this project uses for a boolean becoming a
    p-column value -- never null, because a filter for the absence of contention must
    match on the flag alone.

    Which identities contend is chosen when the repertoire is annotated and is never
    inferred from the counts: contention is a property of the design. An identity may
    sit in more than one declared group, and the note then names the union of bound
    competitors across every group containing it, since each group is an independent
    claim. Names are joined in sorted order, so the same data always produces the same
    string -- which matters once this column is content-addressed as a p-column.
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

    The denominator is the identities the set was offered and whose reading settled, not
    the size of the panel. A clonotype from a sample carrying only eight of ten, which
    bound all eight, covered everything it was asked. Reported as eight of ten it looks
    like a clone with two failures.

    An offered position that did not settle leaves the count and is reported beside it.
    Voiding the count instead is what the four-state model literally implies, but on a
    large panel a single bad reading would then destroy every count in the run.

    `offeredCount` always equals `settledCount + unsettledCount`, since UNRELIABLE is
    the only offered-but-unsettled state. A set asked nothing reports all four as zero,
    so a consumer computing a rate must guard the division itself. A set that is
    entirely UNRELIABLE reports `boundCount=0, settledCount=0, unsettledCount=N` -- read
    that as nothing settled, never as a failure to bind N identities.

    `verdicts` is read at its existing (setId, identity) grain, one row per identity
    regardless of how many tags fed it, so counting rows counts identities, never tags.
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
) -> pl.DataFrame:
    """How often a tag's cells contradict the rest of their own set.

    A clonotype is one receptor with one specificity, so where two of a set's evaluable
    cells read differently at one position, at least one reading is wrong. That makes
    this the cheapest quality signal available: no threshold and no external reference,
    because the contradiction comes from the data disagreeing with itself.

    **The figure pools CELLS rather than scoring sets.** For one key: every set with two
    or more evaluable cells contributes all of them to `cellsCompared`, and the cells in
    the minority of their own set to `minorityCells`. The rate is the second over the
    first.

    Pooling needs no small-set cutoff. A per-set share does: a share over three cells
    takes only four values and would otherwise set the figure, and excluding those sets
    makes the counted population differ from key to key. Pooling's own weakness is that
    one very large set can set the number, and that cancels -- the same set sets it for
    every tag, so a tag standing clear still stands clear.

    Two states cap the rate at half, the minority being the smaller side by definition.

    A set's evaluable cells at a position are every admissible cell that settled there,
    explicit row or silent. A silent admissible cell always resolves not bound, so it is
    as evaluable as an explicit row. An inadmissible cell never votes. The silent count
    comes from `silent_tally`, keyed by set rather than by sample, never recomputed here.

    Measured at the TAG and nowhere else, and read as a comparison rather than a rate: a
    tag standing clear of the other tags in the same panel is misbehaving whatever its
    absolute value. The identity-level figure is not carried, because it has nothing to
    compare against and cannot separate a faulty reagent from a panel of weak binders.

    Diagnostic only. It rests on comparing each tag against the baseline separately,
    which no verdict is built from, so it is evidence about a reagent, never about an
    answer.

    `states` carries `key` (the tag), sampleId, cellId and state -- `combine_cells`'
    sparse shape with `identity` renamed and no setId column. `offered` and `universe`
    are at that same grain, since a key with every cell silent has no explicit row
    anywhere in `states`.

    A key no set could compare reports a NULL rate rather than zero, which would read as
    agreement.
    """
    group_by_cell: dict[tuple[str, str], str] = {}
    for set_id, members in cells_by_set.items():
        for cell_key in members:
            owner = group_by_cell.get(cell_key)
            # Raised rather than asserted: under -O an assert vanishes, and this one
            # vanishing does not crash -- it double-counts the cell into two sets and
            # reports tallies that are simply wrong.
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
            # Same drop `combine_cells` applies: a vote is never counted for a cell no
            # set's membership list names.
            continue
        if key not in offered.get(sample_id, frozenset()):
            # This cell's own sample never offered the identity, so its reading is not a
            # vote. The denominator counts only members whose own sample offered it, so
            # counting this would mix two populations.
            continue
        bucket = explicit_counts.setdefault((set_id, key), {})
        bucket[state] = bucket.get(state, 0) + 1

    minority_cells: dict[str, int] = {}
    cells_compared: dict[str, int] = {}
    for row in tally.iter_rows(named=True):
        set_id, key = row["setId"], row["identity"]
        counts = dict(explicit_counts.get((set_id, key), {}))
        counts[State.NOT_BOUND.value] = counts.get(State.NOT_BOUND.value, 0) + row["silentNotBound"]
        evaluable = sum(counts.values())
        if evaluable < 2:
            # One cell has no minority of its own set to sit in. Left out of BOTH counts,
            # so a key whose every set is a singleton reports nothing to compare rather
            # than a rate of zero, which would read as agreement.
            continue
        cells_compared[key] = cells_compared.get(key, 0) + evaluable
        # SETTLED holds exactly BOUND and NOT_BOUND, so the majority is one of two numbers
        # and every other evaluable cell is in the minority. The zero-valued entry the
        # silent add above can create contributes to neither term.
        minority_cells[key] = minority_cells.get(key, 0) + (evaluable - max(counts.values()))

    return (
        pl.DataFrame({"key": sorted(universe)})
        .with_columns(
            pl.col("key").replace_strict(cells_compared, default=0, return_dtype=pl.Int64).alias("cellsCompared"),
            pl.col("key").replace_strict(minority_cells, default=0, return_dtype=pl.Int64).alias("minorityCells"),
        )
        .with_columns(
            pl.when(pl.col("cellsCompared") > 0)
            .then(pl.col("minorityCells") / pl.col("cellsCompared"))
            .otherwise(None)
            .alias("disagreementRate"),
            pl.lit("tag").alias("level"),
            pl.lit("true").alias("diagnosticOnly"),
        )
        .sort("key")
    )
