"""The dense reference implementation `silent_tally` is tested against.

Lives in the test package, not in `src/`, so the block cannot reach it. On a realistic run the
grid it builds is 11-20x the sparse input and does not fit a large panel at all, and the package
is eager polars throughout -- a caller that reached this in production would not run slowly, it
would die. Keeping it here is what makes that unreachable, rather than a rule someone has to
remember.
"""

import polars as pl
from verdict import CELL_KEY


def densify(identities: pl.DataFrame, cells: pl.DataFrame, offered_by_sample: dict[str, set[str]]) -> pl.DataFrame:
    """Every cell against every identity its sample offered, zeros filled in.

    Without this, an antigen every cell failed to bind produces no rows and its failure is
    indistinguishable from a reagent nobody offered. Production reads the same fact out of
    `silent_tally`, which counts silent positions instead of materializing them.
    """
    # Guard on the assembled blocks, never on offered_by_sample. A map whose every value is
    # empty -- a sample stained with nothing -- is non-empty itself but contributes no block,
    # and concat of an empty list raises.
    blocks = [
        cells.filter(pl.col("sampleId") == sample).join(pl.DataFrame({"identity": sorted(offered)}), how="cross")
        for sample, offered in sorted(offered_by_sample.items())
        if offered
    ]
    grid = (
        pl.concat(blocks, how="vertical")
        if blocks
        else cells.head(0).with_columns(pl.lit(None, pl.String).alias("identity"))
    )

    return grid.join(identities, on=[*CELL_KEY, "identity"], how="left").with_columns(
        pl.col("umiCount").fill_null(0).cast(pl.Int64)
    )
