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
    """
    reasons = {_cell_admissibility_reason(k, admissibility) for k in asked_keys}
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

    `states` is per-cell output shaped like `read_states`' -- columns
    setId, sampleId, cellId, identity, state -- one row per (cell, identity)
    that got an explicit reading; a cell asked about an identity and absent
    here is silent for it, not unasked.

    `offered` is keyed by sample: for a given set, the identities it was
    offered are the union, over its member samples, of what each sample's
    panel offered -- so a set spanning two samples with different panels
    reads as offered whatever either panel offered, while `cellsCouldAnswer`
    below still counts only the members whose OWN sample offered that
    specific identity.

    `cells_by_set` gives each set's full cell membership, including cells
    with no row in `states` at all -- the set's silent cells, which vote
    through `silent_tally` rather than through a row that was never written.
    """
    group_by_cell: dict[tuple[str, str], str] = {}
    for set_id, members in cells_by_set.items():
        for key in members:
            group_by_cell[key] = set_id

    cells_frame = pl.DataFrame(list(group_by_cell), orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    tally = silent_tally(states, cells_frame, offered, admissibility, group_by_cell=group_by_cell, group_column="setId")
    silent_by_pair = {(row["setId"], row["identity"]): row for row in tally.iter_rows(named=True)}

    settled = states.filter(pl.col("state").is_in(SETTLED))
    explicit_counts: dict[tuple[str, str], dict[str, int]] = {}
    for set_id, identity, state in zip(
        settled["setId"].to_list(), settled["identity"].to_list(), settled["state"].to_list(), strict=True
    ):
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
            "agreement": pl.Float64,
            "unreliableReason": pl.String,
        },
    ).sort(["setId", "identity"])
