"""Rung 3: a tag's own count distribution across a sample's cells, split in two.

The ladder in `what-plays-the-baseline` puts this third: *"that tag's own
distribution across the sample's cells, split into two components, where the
sample holds at least 300 cells and the counts actually separate."* It is the
only rung with orthogonal validation behind it and the only one validated at
the panel sizes these runs carry, and it is the rung that serves a run with no
declared comparator once the panel rung's member condition rules that out.

**Where the method comes from.** The study the 300-cell figure comes from
computes a kernel density estimate of log2 counts and takes *"the local minimum
that optimally separates the KDE into two populations"*. That paper applies it
to cell hashes, not to antigen barcodes -- its own antigen rule is the
empty-droplet one, which is the rung above this and needs gene expression this
block does not receive. The same paper records why a Gaussian mixture or
k-means is not used in its place: both degrade when the two populations are
unequal in size, which is the antigen case exactly, where the binders are the
small one.

**What this returns is a comparator, not a classification.** The published use
of the split point is a threshold -- above it, positive. Nothing downstream
here thresholds; it scores a reading against a comparator count. So the split
point is used to identify which readings are background, and the comparator is
the middle of those. Reporting the split point itself as the comparator would
put a classification boundary in a slot every other rung fills with a reading,
and would make this rung far more conservative than the paper it comes from.

**No normalization.** The paper normalizes by each cell's UMI total. Every
reading in this block is a raw integer count of UMIs -- the minimum count acts
on one, the comparator is compared against one, and the panel rung's median is
deliberately truncated to keep it one. A normalized comparator would be the
only non-count in the pipeline. The cost is that a cell sequenced twice as
deeply contributes a reading twice as large to the fit; the split is taken
across cells, so this widens both components rather than shifting one.

Both gates are gates, not settings: below 300 cells, or with no separation
demonstrated, the baseline this rung would produce is not conservative but
wrong, and the rung reports itself unavailable rather than returning one.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import polars as pl
from panel import ANY_SAMPLE, Grouping
from scipy.stats import gaussian_kde

CELL_KEY = ("sampleId", "cellId")

# Both from the study the method comes from, and both gate rather than tune.
DEFAULT_DISTRIBUTION_MIN_CELLS = 300

# Uncalibrated, and the only number here that is: the paper shows the trough in
# a figure and never states how deep a trough must be to count. A run may move
# it, and one that did says so wherever its verdicts appear. Read it as: the
# trough between the two components must sit at or below this fraction of the
# SMALLER of the two peaks flanking it. At 1.0 any dent counts, and the rung
# would serve a distribution that never separated.
DEFAULT_SEPARATION_DEPTH = 0.5

# Evaluation grid for the density. Fixed rather than derived from the data so
# two runs over the same counts return the same split to the bit.
_GRID_POINTS = 512

# The smallest bandwidth the density is allowed, in log2 units, and the one
# place this implementation departs from the paper it follows.
#
# The paper fits UMI-NORMALIZED counts, which are continuous. These are raw
# integer counts, so log2(n+1) lands on a comb -- 0, 1, 1.58, 2, 2.32 -- whose
# widest gap is the 1.0 between a count of nothing and a count of one. Scott's
# rule on a real panel picks a bandwidth near 0.2, which resolves those teeth as
# separate modes: both tallest peaks then sit inside the background, the split
# lands between a count of nothing and a count of one, and a tag with no binders
# at all comes back "separated". On synthetic panels that inverted the answer in
# every case -- a 3% binder population read as no separation, and pure
# background as separation. Smoothing across the widest tooth removes the
# artefact without touching a real split, which sits several log2 units away.
# Above this floor Scott's rule governs, so a broad distribution is not
# over-smoothed.
_MIN_BANDWIDTH_LOG2 = 0.75


class TagBaseline(NamedTuple):
    """What the fit found for one (sample, tag), or why it found nothing.

    `baseline` is None exactly when `reason` is not None, and a caller must
    branch on that rather than defaulting the number: a tag whose counts did not
    separate has NO comparator, which is not the same fact as a comparator that
    read zero.
    """

    baseline: int | None
    reason: str | None
    split: float | None  # log2 space, diagnostic only
    background_cells: int


# The prose a reader sees. Nothing branches on these strings.
TOO_FEW_CELLS = "the sample holds too few cells for a distribution to be fitted"
NO_SEPARATION = "this tag's counts do not separate into two populations"


def fit_tag_background(
    counts: np.ndarray,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
    separation_depth: float = DEFAULT_SEPARATION_DEPTH,
) -> TagBaseline:
    """Split one tag's counts across one sample's cells, and return the background's middle.

    `counts` is one entry per cell in the sample, INCLUDING the cells that read
    nothing, which enter as zeros, and including the cells an admissibility gate
    will later set aside. `baseline-over-all-returned-cells` requires the second:
    a population narrowed by the gate would be narrowed by the baseline's own
    consequences, and excluding the highest readings lowers the baseline, which
    excludes more cells, which lowers it again. Passing only observed readings
    breaks the first the same way in the other direction -- the background is
    where most cells sit, and the cells that read nothing are most of it.

    The count of cells, not the count of readings, is what the 300 gates.
    """
    n = int(counts.size)
    if n < min_cells:
        return TagBaseline(None, TOO_FEW_CELLS, None, n)

    x = np.log2(counts.astype(float) + 1.0)
    # gaussian_kde inverts the covariance and raises on a degenerate one. A tag
    # every cell read identically has one population by construction, which is
    # the answer rather than an error to propagate.
    if float(np.std(x)) == 0.0:
        return TagBaseline(None, NO_SEPARATION, None, n)

    grid = np.linspace(float(x.min()), float(x.max()), _GRID_POINTS)
    density = _density(counts, x, grid)

    split_index = _deepest_admissible_trough(density, separation_depth)
    if split_index is None:
        return TagBaseline(None, NO_SEPARATION, None, n)

    split = float(grid[split_index])
    background = counts[x <= split]
    # Unreachable while the trough sits strictly between two peaks, since the
    # lower peak's mass is below it. A comparator taken from an empty population
    # is the one failure that must never be silent.
    if background.size == 0:
        return TagBaseline(None, NO_SEPARATION, split, n)

    # The median, truncated toward zero for the same reason the panel rung's is:
    # the comparator stays an integer count of UMIs, as every other reading in
    # the pipeline is.
    return TagBaseline(int(np.median(background)), None, split, int(background.size))


def _deepest_admissible_trough(density: np.ndarray, separation_depth: float) -> int | None:
    """The index of the trough that separates the two largest peaks, or None.

    "Optimally separates" is read here as: of the troughs lying between the two
    highest peaks, the lowest one. With two clean modes there is exactly one
    trough and the choice does not arise; the rule exists for a noisy density,
    where a shoulder on either mode adds troughs that separate nothing.

    Returns None where the density has fewer than two peaks, or where the trough
    is not deep enough to be a separation rather than a dent.
    """
    rising = density[1:-1] > density[:-2]
    falling = density[1:-1] > density[2:]
    # +1 because the comparisons above are taken over the interior only.
    peaks = np.flatnonzero(rising & falling) + 1
    troughs = np.flatnonzero((density[1:-1] < density[:-2]) & (density[1:-1] < density[2:])) + 1
    if peaks.size < 2 or troughs.size == 0:
        return None

    tallest = peaks[np.argsort(density[peaks])[-2:]]
    left, right = int(min(tallest)), int(max(tallest))
    between = troughs[(troughs > left) & (troughs < right)]
    if between.size == 0:
        return None

    deepest = int(between[np.argmin(density[between])])
    # Against the SMALLER flanking peak. Against the larger, a tiny second mode
    # on the shoulder of a dominant one would clear any threshold.
    if density[deepest] > separation_depth * min(float(density[left]), float(density[right])):
        return None
    return deepest


def _density(counts: np.ndarray, x: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """The kernel density over `grid`, never smoothed less than the floor.

    Fitted over the DISTINCT counts carrying their frequencies as weights,
    which is the same density the full vector gives -- verified identical on
    every shape in the test file -- and roughly forty times cheaper, because
    the cost is one kernel per distinct value rather than one per cell. Counts
    are small integers, so a sample of thousands of cells holds tens of distinct
    values. Without this, a thousand-member panel across two dozen samples is
    tens of thousands of fits over the full vector each time.

    The bandwidth is computed rather than taken from `kde.factor`. Scipy derives
    its own from the EFFECTIVE sample size, which for a weighted fit is
    1/sum(w^2) -- a few dozen here rather than the thousands of cells actually
    measured. That over-smooths by a factor of three and loses real splits: two
    of the shapes in the test file stopped separating. Scott's rule over the
    true cell count restores it exactly.

    `set_bandwidth` takes a factor scipy multiplies by the data's own spread, so
    an absolute width in log2 units is converted before it is applied. The
    spread is the weighted one, matching the covariance scipy computes
    internally, or the requested width and the applied one would differ.
    """
    values, frequency = np.unique(counts, return_counts=True)
    xs = np.log2(values.astype(float) + 1.0)
    weights = frequency / frequency.sum()

    mean = float(np.sum(weights * xs))
    # The same reliability correction np.cov applies to aweights, so the spread
    # here and the one inside gaussian_kde agree.
    spread = float(np.sqrt(np.sum(weights * (xs - mean) ** 2) / (1.0 - np.sum(weights**2))))

    kde = gaussian_kde(xs, weights=weights)
    bandwidth = max(x.size ** (-0.2) * spread, _MIN_BANDWIDTH_LOG2)
    kde.set_bandwidth(bandwidth / spread)
    return kde(grid)


def fit_tag_baselines(
    counts: pl.DataFrame,
    cells: list[tuple[str, str]],
    panel: pl.DataFrame,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
    separation_depth: float = DEFAULT_SEPARATION_DEPTH,
) -> dict[tuple[str, str], TagBaseline]:
    """One fit per (sample, tag) the panel declares. Keyed (sample, tag).

    Per tag and before any grouping, which `what-plays-the-baseline` requires of
    both fitting rungs: a background fitted per identity would depend on the
    grouping, so changing a grouping would change the background and a
    regrouping would stop being a re-reading of unchanged counts.

    `counts` is the RAW frame -- not floored, not densified. `cells` is the
    analysed cell universe, and it is the population every fit is taken over:
    a cell of the sample that read nothing for a tag enters that tag's fit as a
    zero, and the cells an admissibility gate will later set aside are still in
    it. `baseline-over-all-returned-cells` requires the second, and the first is
    what makes the background a background rather than the shape of whatever
    happened to be observed.

    A tag the panel declared for a sample but the reads never showed is fitted
    over all zeros, does not separate, and comes back with no baseline -- which
    is the honest answer and is also the QC finding.

    Baselines are local: every sample is fitted on its own cells, so two
    samples' comparators are not the same currency. That is the ladder's own
    property, not an artefact of doing it this way.
    """
    universe = pl.DataFrame(cells, orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    # Checked rather than deduplicated. A duplicate would add one zero to the
    # population it duplicates, which is a background estimate over a population
    # nobody chose -- small, plausible, and invisible in the output.
    if universe.height != universe.unique().height:
        raise ValueError("the cell universe holds duplicated cells; every fit's population would be wrong")
    cells_per_sample = dict(
        universe.group_by("sampleId").len().iter_rows()  # (sampleId, count)
    )

    # Scoped to the cell universe first, so a barcode outside the analysis never
    # enters a background it was not part of.
    scoped = counts.join(universe, on=CELL_KEY, how="semi")
    # The same reasoning as the universe check above, one level down. A tag read
    # twice in one cell contributes twice to its own background and displaces a
    # zero, and the padding below cannot detect it: the readings stay far short
    # of the cell count on any real panel, so nothing overflows.
    if scoped.height != scoped.select(*CELL_KEY, "tag").unique().height:
        raise ValueError("the counts frame holds duplicated readings; every fit's population would be wrong")

    observed = scoped.group_by(["sampleId", "tag"]).agg(pl.col("umiCount"))
    readings = {(s, t): np.asarray(v, dtype=np.int64) for s, t, v in observed.iter_rows()}

    out: dict[tuple[str, str], TagBaseline] = {}
    for sample, tag in _declared_pairs(panel, sorted(cells_per_sample)):
        n_cells = cells_per_sample.get(sample, 0)
        seen = readings.get((sample, tag), _EMPTY)
        if seen.size > n_cells:
            # Unreachable while both checks above hold, and raised anyway because
            # the alternative is silent: a negative pad makes numpy return an
            # EMPTY array rather than raise, so the fit would run over the
            # observed readings alone and look like it worked.
            raise ValueError(f"tag {tag!r} has more readings than sample {sample!r} has cells")
        # The cells that read nothing are not missing data. They are the
        # background, and they are most of it.
        values = np.concatenate([seen, np.zeros(n_cells - seen.size, dtype=np.int64)])
        out[(sample, tag)] = fit_tag_background(values, min_cells, separation_depth)
    return out


_EMPTY = np.zeros(0, dtype=np.int64)


def _declared_pairs(panel: pl.DataFrame, samples: list[str]) -> list[tuple[str, str]]:
    """Every (sample, tag) the panel declares, with a global declaration expanded.

    A panel with no sample column declares one row per tag under ANY_SAMPLE,
    meaning every sample was stained with it. The fit is per sample regardless,
    because the population it is taken over is a sample's cells.
    """
    pairs = {(sample, tag) for tag, sample in panel.select("tag", "sample").iter_rows() if sample != ANY_SAMPLE}
    global_tags = sorted({tag for tag, sample in panel.select("tag", "sample").iter_rows() if sample == ANY_SAMPLE})
    pairs |= {(sample, tag) for sample in samples for tag in global_tags}
    return sorted(pairs)


class IdentityBaselines(NamedTuple):
    """Per (sample, identity): the comparator, or why there is none.

    Keyed by sample rather than by cell because that is the grain this rung
    measures at -- one distribution per tag across a sample's cells yields one
    number for every cell of that sample. Every other rung is keyed by cell, so
    a caller must not treat the two as interchangeable.
    """

    baseline: dict[tuple[str, str], int]
    reason: dict[tuple[str, str], str]


def identity_baselines(
    fits: dict[tuple[str, str], TagBaseline],
    grouping: Grouping,
    samples: list[str],
) -> IdentityBaselines:
    """Carry the per-tag fits up to the identities the grouping builds from them.

    Two rules, and neither is stated by the spec -- both are recorded as open
    with the spec's author and both are one line to reverse.

    **The identity's comparator is the highest of its tags'.** Its READING is
    the highest of its tags' readings, so a comparator taken any other way
    would read one tag's count against another tag's background.

    **An identity whose tags did not all separate has no comparator at all.**
    The reading may have come from the tag whose background could not be
    estimated, and there is no way to tell from the reading which tag supplied
    it. Taking the highest of the tags that did separate would answer as though
    the missing one had read nothing.

    The fitting stays per tag either way, which is what
    `what-plays-the-baseline` requires: nothing here refits, so changing a
    grouping re-reads unchanged counts rather than moving the background.
    """
    baseline: dict[tuple[str, str], int] = {}
    reason: dict[tuple[str, str], str] = {}

    for sample in samples:
        for identity, tags in sorted(_identity_tags(grouping, sample).items()):
            fitted = [fits.get((sample, tag)) for tag in sorted(tags)]
            missing = [(tag, f) for tag, f in zip(sorted(tags), fitted, strict=True) if f is None or f.baseline is None]
            if missing:
                tag, first = missing[0]
                reason[(sample, identity)] = (
                    first.reason if first is not None else f"tag {tag!r} was never fitted for this sample"
                )
                continue
            baseline[(sample, identity)] = max(f.baseline for f in fitted)

    return IdentityBaselines(baseline, reason)


def _identity_tags(grouping: Grouping, sample: str) -> dict[str, set[str]]:
    """Which tags carry each identity in one sample.

    A declaration keyed to the sample wins over a global one, the same
    precedence the reading side applies when it maps tags to identities. Doing
    it differently here would compare a reading built from one set of tags
    against a background built from another.
    """
    star = {tag: identity for (tag, s), identity in grouping.items() if s == ANY_SAMPLE}
    keyed = {tag: identity for (tag, s), identity in grouping.items() if s == sample}
    resolved = {**star, **keyed}

    out: dict[str, set[str]] = {}
    for tag, identity in resolved.items():
        out.setdefault(identity, set()).add(tag)
    return out
