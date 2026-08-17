"""Turning a cell's counts into states.

Five steps, in this order, and the order is load-bearing:

  1. the floor, on the raw count, per cell and per tag;
  2. densify — every cell against every identity its sample offered, so a cell
     asked and silent is a real zero rather than a missing row;
  3. tags combine into an identity by the highest of their counts;
  4. the identity's count is read against that cell's own reference reading;
  5. the comparison becomes one of the four states.

Step 2 exists because tag-stat emits only observed pairs. Without it an antigen
every cell failed to bind produces no rows at all, and the absence is
indistinguishable from a reagent nobody offered.

The cell key is (sampleId, cellId) throughout: cell barcodes are bare 16-mers
shared across samples.

Compare `min_umi` in per_cell_metrics.py, which is also a UMI threshold on
this data but resolves the other way: a barcode below it makes the feature
absent for that cell, omitted rather than zeroed. Both keep a reading exactly
at the threshold. The difference is the whole point of the floor — a floored
reading is still a reading, and it answers "not bound"; an omitted one leaves
nothing to answer with.

After step 3 this module holds two frame shapes: the sparse per-tag frame the
floor works on, and the per-identity frame combining produces from it — both
keyed by CELL_KEY, which is the column vocabulary spanning both.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

import polars as pl

CELL_KEY = ("sampleId", "cellId")

# Uncalibrated: a declared default the scientist can move, not a fitted line.
DEFAULT_FLOOR = 4


class Floored(NamedTuple):
    counts: pl.DataFrame
    stats: dict[str, int]


def apply_floor(counts: pl.DataFrame, floor: int, reference_tags: set[str]) -> Floored:
    """Zero every (cell, tag) count below `floor`, except the comparator's.

    A floored count contributes exactly as a count of zero does — the position
    reads *not bound*, not *unreliable*. The floor is not a statement that the
    reading could not be settled; it is that a count that small is not
    distinguishable from none.

    Reference tags are exempt. The floor removes what is not evidence *of
    binding*; the comparator is not evidence of binding, and flooring it lowers
    every denominator and shifts the whole run toward *bound*.

    Scope limit: `reference_tags` is global, so a tag is a comparator in every
    sample or in none. The panel is keyed (tag, sample) and could in principle
    declare a barcode a control in one sample and a real antigen in another.
    That case is not handled here — and the panel reader's consistent_properties()
    drops any property whose value disagrees across a tag's rows, so a
    per-sample control designation would already have been discarded rather
    than honoured. Handling it belongs where the reference is selected and
    where the CLI resolves it, not in the floor.

    Returns the floored counts and {"readingsFloored", "cellsEmptied"}: the two
    counters that land in this sample's row of the QC report.

    Both counters assume the sparse frame this step receives, where every row
    is an observed reading and so a count is at least 1. Densification, which
    manufactures genuine zeros, happens after this step: run it before, and
    every manufactured row inflates readingsFloored while every unbound cell
    counts as emptied though the floor removed nothing.
    """
    # Not an optimisation: falling through would count a cell whose only
    # reading is already zero as "emptied", when the floor removed nothing.
    if floor <= 0:
        return Floored(counts, {"readingsFloored": 0, "cellsEmptied": 0})

    # is_in yields null for a null tag, so a null-tag row would escape both the
    # floor and the emptied populations here while flooring normally when no
    # reference is declared. The panel reader never emits one; this is a note
    # for anyone who feeds this an unvalidated frame.
    is_ref = pl.col("tag").is_in(list(reference_tags)) if reference_tags else pl.lit(False)
    below = (pl.col("umiCount") < floor) & ~is_ref

    readings_floored = int(counts.select(below.sum()).item())
    out = counts.with_columns(
        pl.when(below).then(pl.lit(0, dtype=pl.Int64)).otherwise(pl.col("umiCount")).alias("umiCount")
    )

    # "Emptied" is scoped to non-reference readings: a cell holding only the
    # comparator never had evidence of binding for the floor to remove.
    # had_evidence deliberately does not filter on umiCount > 0 — that absence
    # is the sparse-frame assumption above, not an oversight to "symmetrise".
    had_evidence = counts.filter(~is_ref).select(CELL_KEY).unique()
    kept_evidence = out.filter(~is_ref & (pl.col("umiCount") > 0)).select(CELL_KEY).unique()
    cells_emptied = had_evidence.join(kept_evidence, on=CELL_KEY, how="anti").height

    return Floored(out, {"readingsFloored": readings_floored, "cellsEmptied": cells_emptied})


# Shipped defaults. Every one is a visible parameter rather than a constant,
# because nothing published sets any of them and a hard-coded line would pretend
# to a basis nobody has.
DEFAULT_PANEL_MIN_MEMBERS = 8
# A reference reading below this is too thin to compare against: the position
# reads *unreliable*, not *not bound* — the comparison could not be made, not
# that it was made and failed.
DEFAULT_REFERENCE_THIN_LINE = 2
DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE = 100


class ReferenceChoice(str, Enum):
    """Which comparator served. Two runs served differently do not compare.

    EMPTY_DROPLETS is deliberately absent: it needs gene expression and an
    empty-droplet population this block does not receive. Declaring a value the
    software cannot serve would put a crashing option in the dropdown.
    """

    DECLARED = "declared reference tag"
    PANEL = "the panel's own readings"
    NONE = "no comparator available"


def resolve_default_source(reference_tags: set[str]) -> ReferenceChoice:
    """The *default* source only. The scientist overrides it; this never does."""
    return ReferenceChoice.DECLARED if reference_tags else ReferenceChoice.NONE


def served_source(
    source: ReferenceChoice,
    reference_tags: set[str],
    panel_size: int,
    min_members: int,
) -> ReferenceChoice:
    """The source that can actually be served. Only ever the one asked for, or NONE.

    A comparison that cannot be made is reported as absent rather than
    approximated: a caller who asked for a comparator this run cannot produce
    gets told plainly, never handed a different one it did not ask for.
    """
    if source is ReferenceChoice.DECLARED and not reference_tags:
        return ReferenceChoice.NONE
    if source is ReferenceChoice.PANEL and panel_size < min_members:
        return ReferenceChoice.NONE
    return source


class Reference(NamedTuple):
    by_cell: dict[tuple[str, str], int]
    served: ReferenceChoice


def reference_by_cell(
    counts: pl.DataFrame,
    reference_tags: set[str],
    source: ReferenceChoice,
    cells: list[tuple[str, str]] | None = None,
    panel_size: int = 0,
    min_members: int = DEFAULT_PANEL_MIN_MEMBERS,
) -> Reference:
    """The reference reading per cell, and which source actually served.

    `source` is supplied, never inferred; `served_source` decides whether it
    can actually be served, and the result differs from the request in exactly
    one direction — down to NONE.

    `by_cell` is EMPTY when `served` is NONE — not a mapping of zeros. When a
    comparator did serve, it holds a key for every analysed cell, zero where
    that cell showed none of it. So a reader switches on `served`, never on key
    presence: `by_cell.get(key, 0)` would read "no comparator was available" as
    "the comparator read zero", which is the difference between a position that
    could not be settled and one that was settled as not bound.

    `cells`, when given, is authoritative in both directions: the result holds
    exactly those cells, zero-filled where the comparator read nothing. Omit it
    and the cell universe is taken from the counts frame instead, which covers
    only cells with an observed reading — pass it explicitly wherever the
    analysis has a cell list, or a cell that was asked and read nothing will be
    missing rather than zero.

    Receives the floored, sparse per-tag frame — before densification. The
    PANEL median is a median of observed readings; on a densified frame every
    manufactured zero would drag it toward zero and change the comparator for
    every cell, not just the ones that gained one.

    `reference_tags` is not excluded from the PANEL median: the panel
    comparator is the cell's own readings, and a declared comparator, where
    also present, is one of those readings rather than something held out of
    them.
    """
    served = served_source(source, reference_tags, panel_size, min_members)
    if served is ReferenceChoice.NONE:
        return Reference({}, ReferenceChoice.NONE)

    all_cells = (
        # Deduplicated: a cell with several tag readings otherwise appears once
        # per reading, and the zero-fill loop below would revisit it that many
        # times for no effect but the extra work.
        {(s, c) for s, c in zip(counts["sampleId"].to_list(), counts["cellId"].to_list(), strict=True)}
        if cells is None
        else cells
    )
    # A semi join on the cell list, applied before either branch aggregates, so
    # a cell outside the analysis is dropped before its rows are ever combined
    # rather than combined and then discarded.
    scoped = (
        counts
        if cells is None
        else counts.join(
            pl.DataFrame(cells, orient="row", schema={"sampleId": pl.String, "cellId": pl.String}),
            on=CELL_KEY,
            how="semi",
        )
    )

    if served is ReferenceChoice.DECLARED:
        # Several reference tags combine as any identity's tags do: by the
        # highest. Taking an arbitrary one would make the comparator depend on
        # row order.
        rows = (
            scoped.filter(pl.col("tag").is_in(list(reference_tags)))
            .group_by(CELL_KEY)
            .agg(pl.col("umiCount").max().alias("ref"))
        )
    elif served is ReferenceChoice.PANEL:
        # cast(Int64) truncates rather than rounds: a panel split between 1 and
        # 2 medians to 1.5, cast to 1 — one below the default thin line of 2 —
        # so that cell reads unreliable rather than being compared against a
        # number resting on half a UMI.
        rows = scoped.group_by(CELL_KEY).agg(pl.col("umiCount").median().cast(pl.Int64).alias("ref"))
    else:
        # Reachable only if ReferenceChoice gains a member with no aggregation
        # branch here — most plausibly EMPTY_DROPLETS. That is a missing
        # implementation, not a fact about this run, so it must not be
        # reported as "unavailable this time".
        raise SystemExit(f"no comparator implementation for reference source {served.value!r}")

    ref = {(s, c): v for s, c, v in zip(rows["sampleId"], rows["cellId"], rows["ref"], strict=True)}
    # The tag was offered; a cell showing none of it read zero, not nothing.
    for key in all_cells:
        ref.setdefault(key, 0)
    return Reference(ref, served)


def gate_cells(
    reference: dict[tuple[str, str], int],
    threshold: int | None,
    observation_line: int = DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE,
) -> tuple[set[tuple[str, str]], int]:
    """Which cells a declared gate sets aside, and how many read high regardless.

    The gate defaults off. The exposure count is returned either way, so a run's
    exposure is visible to a scientist who has left it off — a sticky cell left
    in returns as a confident *not bound*, which is the collapse the four-state
    model exists to prevent.
    """
    # The observation line is independent of the gate: it measures how many
    # cells sat in high background, which is a fact about the run whether or
    # not anything was set aside. Folding it into the threshold would make the
    # count mean "cells the gate removed" and lose the measurement entirely.
    high = sum(1 for v in reference.values() if v >= observation_line)
    if threshold is None:
        return set(), high
    return {k for k, v in reference.items() if v >= threshold}, high
