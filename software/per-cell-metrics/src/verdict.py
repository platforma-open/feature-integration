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

This module implements step 1 only.
"""

from __future__ import annotations

import polars as pl

CELL_KEY = ["sampleId", "cellId"]

# The value the antibody-side lineage uses and later work inherited. Not a
# calibrated line, which is why it ships as a declared default the scientist
# can move rather than as a constant.
DEFAULT_FLOOR = 4


def apply_floor(counts: pl.DataFrame, floor: int, reference_tags: set[str]) -> tuple[pl.DataFrame, dict[str, int]]:
    """Zero every (cell, tag) count below `floor`, except the comparator's.

    A floored count contributes exactly as a count of zero does — the position
    reads *not bound*, not *unreliable*. The floor is not a statement that the
    reading could not be settled; it is that a count that small is not
    distinguishable from none.

    Reference tags are exempt. The floor removes what is not evidence *of
    binding*; the comparator is not evidence of binding, and flooring it lowers
    every denominator and shifts the whole run toward *bound*.

    Scope limit: `reference_tags` is global, so a tag is a comparator in every
    sample or in none. The panel is keyed (tag, sample) and can in principle
    declare a barcode a control in one sample and a real antigen in another;
    that case is NOT handled here, and Task 1's consistent_properties() would
    already have dropped such a divergent role rather than honouring it per
    sample. Revisit when the reference is resolved (Tasks 5 and 13), not here.

    Returns the floored counts and {"readingsFloored", "cellsEmptied"}, the two
    quantities the quality measurement set asks of this step.
    """
    if floor <= 0:
        return counts, {"readingsFloored": 0, "cellsEmptied": 0}

    is_ref = pl.col("tag").is_in(list(reference_tags)) if reference_tags else pl.lit(False)
    below = (pl.col("umiCount") < floor) & ~is_ref

    readings_floored = int(counts.select(below.sum()).item())
    out = counts.with_columns(
        pl.when(below).then(pl.lit(0, dtype=pl.Int64)).otherwise(pl.col("umiCount")).alias("umiCount")
    )

    # "Emptied" is scoped to non-reference readings: a cell holding only the
    # comparator never had evidence of binding for the floor to remove.
    before = counts.filter(~is_ref).group_by(CELL_KEY).agg(pl.len().alias("n"))
    after = out.filter(~is_ref).filter(pl.col("umiCount") > 0).group_by(CELL_KEY).agg(pl.len().alias("n"))
    cells_emptied = before.join(after, on=CELL_KEY, how="anti").height

    return out, {"readingsFloored": readings_floored, "cellsEmptied": cells_emptied}
