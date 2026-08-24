"""Rung 3: a tag's own count distribution across a sample's cells, split in two.

The ladder in `what-plays-the-baseline` puts this third: *"that tag's own distribution
across the sample's cells, split into two components, where the sample holds at least
300 cells and the counts actually separate."* It is the only rung with orthogonal
validation behind it and the only one validated at these panel sizes, and it serves a
run with no declared comparator once the panel rung's member condition rules that out.

**Where the method comes from.** The study the 300-cell figure comes from computes a
kernel density estimate of log2 counts and takes *"the local minimum that optimally
separates the KDE into two populations"*. That paper applies it to cell hashes, not
antigen barcodes -- its own antigen rule is the empty-droplet one, the rung above this,
which needs gene expression this block does not receive. The same paper records why a
Gaussian mixture or k-means is not used: both degrade when the two populations are
unequal in size, which is the antigen case exactly.

**This returns a comparator, not a classification.** The published use of the split
point is a threshold -- above it, positive. Nothing downstream thresholds. It scores a
reading against a comparator count. So the split point identifies which readings are
background, and the comparator is the middle of those. Reporting the split point itself
would put a classification boundary in a slot every other rung fills with a reading.

**No normalization.** The paper normalizes by each cell's UMI total. Every reading here
is a raw integer UMI count, and a normalized comparator would be the only non-count in
the pipeline. The cost is that a cell sequenced twice as deeply contributes a reading
twice as large. The split is taken across cells, so this widens both components rather
than shifting one.

Both gates are gates, not settings: below 300 cells, or with no separation demonstrated,
the baseline this rung would produce is wrong rather than conservative, and the rung
reports itself unavailable.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import polars as pl
from panel import ANY_SAMPLE
from scipy.special import logsumexp
from scipy.stats import nbinom

CELL_KEY = ("sampleId", "cellId")

# Both from the study the method comes from, and both gate rather than tune.
DEFAULT_DISTRIBUTION_MIN_CELLS = 300


class Background(NamedTuple):
    """A fitted background, as a reader needs to see it.

    The two means together are the finding. A background alone says nothing about whether the
    counts separated, and `330-the-quality-readout` wants this precisely so a scientist can see
    whether they did before choosing a baseline.

    `weight` is the share of cells the background component holds. A tag that bound nothing is
    split anyway -- the method assumes two components exist -- and shows itself here as two
    means sitting almost on top of each other rather than as a refusal.
    """

    mean: float
    signal_mean: float
    weight: float


class TagFit(NamedTuple):
    """One tag's per-cell probability of binding in one sample, or why there is none.

    `probabilities` is None exactly when `reason` is not None, and a caller must branch on
    that rather than defaulting: a tag whose counts did not separate established NOTHING for
    its cells, which is not the same fact as every cell reading a low probability.

    Aligned to the counts the fit was given, one entry per cell in the sample.

    `background` travels with the probabilities and is None on exactly the same condition. The
    fit was taken either way, and discarding its parameters left the one number a reader needs
    in order to judge the fit inside the function that made it.
    """

    probabilities: np.ndarray | None
    reason: str | None
    cells: int
    background: Background | None = None


# The prose a reader sees. Nothing branches on these strings.
TOO_FEW_CELLS = "the sample holds too few cells for a distribution to be fitted"
NO_SEPARATION = "this tag's counts do not separate into two populations"


# The share of the highest counts dropped before the fit. `what-plays-the-baseline` fixes it:
# the rule drops "the counts above the 99th percentile" so that a handful of very high
# readings cannot drag the signal component's mean up and pull the boundary with it. The
# dropped cells still get a probability -- they are the most bound cells in the sample, and
# withholding an answer for them would be the opposite of what the trim is for.
_UPPER_TRIM_PERCENTILE = 99.0

# EM stops when the mean log-likelihood moves less than this, or after this many rounds.
# Neither number is published. They are convergence controls rather than parameters of the
# method: a run that hit the iteration cap has not been given a different rule, only a
# slightly less settled one.
_EM_TOLERANCE = 1e-6
_EM_MAX_ROUNDS = 200

# The dispersion a component falls back to where its variance does not exceed its mean. The
# negative binomial has no such shape -- that is the Poisson boundary -- so the fit uses a
# large size, which IS Poisson in the limit, rather than failing.
_POISSON_LIMIT_SIZE = 1e6

# The smallest mean a component may hold. A component fitted entirely on zeros has a mean of
# zero, and a negative binomial with a mean of zero puts all of its mass on zero -- so every
# non-zero count becomes impossible under it and the fit collapses. A mostly-silent tag is
# the ordinary case here, not an edge one, so the mean is held just above zero instead: a
# spike at zero that still admits the rest of the data.
_MIN_COMPONENT_MEAN = 1e-6


def _nb_size(mean: float, variance: float) -> float:
    """The negative binomial's size from a mean and a variance, by moments.

    var = mean + mean^2 / size, so size = mean^2 / (var - mean). Where the variance does not
    exceed the mean the distribution is Poisson or narrower and no finite size fits it, so the
    Poisson limit stands in.
    """
    if mean <= 0.0 or variance <= mean:
        return _POISSON_LIMIT_SIZE
    return float(mean * mean / (variance - mean))


def _nb_logpmf(counts: np.ndarray, mean: float, size: float) -> np.ndarray:
    """The negative binomial log pmf, parameterised by mean and size rather than by p.

    LOG, not the density itself, and that is load-bearing. A binder's count sits far in the
    tail of the background component, and far enough that the density underflows to exactly
    zero in double precision. Both components then call the count impossible, the
    normalisation divides by zero, and the cell most obviously bound in the sample is the one
    the fit cannot answer for. In log space the same count is a large negative number and the
    comparison between components still holds.
    """
    return nbinom.logpmf(counts, size, size / (size + mean))


def _responsibilities(counts: np.ndarray, means: np.ndarray, sizes: np.ndarray, weights: np.ndarray):
    """Each count's posterior probability per component, and the mean log-likelihood.

    Softmax over log weights plus log densities, so a count in either tail normalises without
    underflowing. Returns None where a log density is not finite, which happens only for a
    degenerate component rather than for an extreme count.
    """
    logs = np.vstack([np.log(weights[k]) + _nb_logpmf(counts, means[k], sizes[k]) for k in (0, 1)])
    totals = logsumexp(logs, axis=0)
    # A single -inf is fine and expected: it says one component calls that count impossible,
    # which is what a two-component fit is for. Only a count BOTH reject has no answer, and
    # that is a degenerate fit rather than an extreme reading.
    if not np.all(np.isfinite(totals)):
        return None
    return np.exp(logs - totals), float(np.mean(totals))


class _Mixture(NamedTuple):
    """A fitted two-component negative binomial, with the signal component identified."""

    means: np.ndarray
    sizes: np.ndarray
    weights: np.ndarray
    signal: int


def _fit_two_component_nb(counts: np.ndarray) -> _Mixture | None:
    """A two-component negative binomial fitted to `counts`, or None where none exists.

    `what-plays-the-baseline` fixes the method: fit a two-component negative binomial
    mixture and label the higher-median component the signal one. Scoring a cell against the
    fit is `_signal_probability`, kept separate because the fit is taken over the trimmed
    counts and every cell is scored, including the trimmed ones.

    Returns None where no two-component fit exists -- every count identical, or the two
    components converging onto one another. A tag with no binders has one population, and the
    honest answer is that this rung established nothing for it rather than a boundary drawn
    through the middle of a single mode.

    EM with the dispersions re-estimated by moments each round. The dispersions are not free
    parameters of the likelihood here: solving for them jointly needs a numerical root per
    component per round, and the moment estimate is stable on integer counts where that is
    not. A round that produces a degenerate component ends the fit rather than continuing
    from it.
    """
    x = counts.astype(float)
    if np.unique(x).size < 2:
        return None

    # Split at the median to start. Two components initialised on the same statistics never
    # separate, and the median is the one split point that is always available.
    pivot = float(np.median(x))
    low, high = x[x <= pivot], x[x > pivot]
    if low.size == 0 or high.size == 0:
        # A median equal to the maximum puts everything in one half. Fall back to splitting
        # at the mean, which differs from the median exactly when the counts are skewed --
        # which is this case.
        pivot = float(np.mean(x))
        low, high = x[x <= pivot], x[x > pivot]
        if low.size == 0 or high.size == 0:
            return None

    means = np.maximum(np.array([float(low.mean()), float(high.mean())]), _MIN_COMPONENT_MEAN)
    sizes = np.array([_nb_size(means[0], float(low.var())), _nb_size(means[1], float(high.var()))])
    weights = np.array([low.size / x.size, high.size / x.size])

    previous = -np.inf
    for _ in range(_EM_MAX_ROUNDS):
        # E step.
        step = _responsibilities(x, means, sizes, weights)
        if step is None:
            return None
        responsibilities, loglik = step

        # M step. A component that loses all its mass has collapsed, and continuing from it
        # fits one population while reporting two.
        mass = responsibilities.sum(axis=1)
        if np.any(mass <= 0.0):
            return None
        weights = mass / x.size
        for k in (0, 1):
            r = responsibilities[k]
            means[k] = max(float((r * x).sum() / mass[k]), _MIN_COMPONENT_MEAN)
            variance = float((r * (x - means[k]) ** 2).sum() / mass[k])
            sizes[k] = _nb_size(means[k], variance)

        # The two components have converged onto each other, so there is one population.
        if means[0] == means[1]:
            return None

        if abs(loglik - previous) < _EM_TOLERANCE:
            break
        previous = loglik

    # "Label the higher-median component the signal one." The medians of the fitted
    # components are ordered by their means, which the negative binomial's shape guarantees.
    return _Mixture(means.copy(), sizes.copy(), weights.copy(), int(np.argmax(means)))


def _signal_probability(counts: np.ndarray, fit: _Mixture) -> np.ndarray | None:
    """Each count's probability of belonging to the fit's signal component.

    Separate from the fit so that a cell trimmed out of the fit still gets an answer. Those
    cells are the most bound in the sample, and withholding a probability for them would be
    the opposite of what the trim is for.
    """
    step = _responsibilities(counts.astype(float), fit.means, fit.sizes, fit.weights)
    if step is None:
        return None
    return step[0][fit.signal]


def fit_tag_probabilities(
    counts: np.ndarray,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
) -> TagFit:
    """One tag's counts across one sample's cells, as a probability of binding per cell.

    `counts` is one entry per cell in the sample, INCLUDING cells that read nothing, which
    enter as zeros, and including cells an admissibility gate will later set aside.
    `baseline-over-all-returned-cells` requires the second: a population narrowed by the gate
    would be narrowed by the baseline's own consequences, and excluding the highest readings
    lowers the baseline, which excludes more cells, which lowers it again. Passing only
    observed readings breaks the first the same way -- the background is where most cells sit,
    and the cells that read nothing are most of it.

    The count of cells, not of readings, is what the 300 gates.

    The returned probabilities are aligned to `counts`. The trim above the 99th percentile
    keeps those cells and excludes them from the FIT alone, so every cell still gets an
    answer.
    """
    n = int(counts.size)
    if n < min_cells:
        return TagFit(None, TOO_FEW_CELLS, n)

    # Fit without the top tail, then score every cell against what was fitted.
    ceiling = float(np.percentile(counts.astype(float), _UPPER_TRIM_PERCENTILE))
    fitted_on = counts[counts.astype(float) <= ceiling]
    if fitted_on.size == 0:
        return TagFit(None, NO_SEPARATION, n)

    fit = _fit_two_component_nb(fitted_on)
    if fit is None:
        return TagFit(None, NO_SEPARATION, n)
    probabilities = _signal_probability(counts, fit)
    if probabilities is None:
        return TagFit(None, NO_SEPARATION, n)
    background_index = 1 - fit.signal
    background = Background(
        mean=float(fit.means[background_index]),
        signal_mean=float(fit.means[fit.signal]),
        weight=float(fit.weights[background_index]),
    )
    return TagFit(probabilities, None, n, background)


class TagFits(NamedTuple):
    """Every (sample, tag) fit, as a frame of per-cell probabilities plus the misses.

    `probabilities` carries one row per (sample, cell, tag) the fit scored, with `pBound` the
    probability that cell's count belongs to the signal component. A pair that established
    nothing contributes no rows and appears in `reasons` instead. A caller must branch on
    absence rather than defaulting: a tag that established nothing said NOTHING about its
    cells, which is not the same fact as every cell reading a low probability.
    """

    probabilities: pl.DataFrame
    reasons: dict[tuple[str, str], str]
    # One entry per pair that fitted, on the same condition as a contribution to
    # `probabilities`. A pair in `reasons` is absent here.
    backgrounds: dict[tuple[str, str], Background] = {}


_PROB_SCHEMA = {"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "pBound": pl.Float64}


def fit_tag_probabilities_by_pair(
    counts: pl.DataFrame,
    cells: list[tuple[str, str]],
    panel: pl.DataFrame,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
) -> TagFits:
    """One fit per (sample, tag) the panel declares, scored per cell.

    Per tag and before any grouping, which `what-plays-the-baseline` requires of every
    fitting rung: a background fitted per identity would depend on the grouping, so changing
    a grouping would change the background and a regrouping would stop being a re-reading of
    unchanged counts.

    `counts` is the RAW frame -- not floored, not densified. `cells` is the analysed cell
    universe, and it is the population every fit is taken over: a cell that read nothing for
    a tag enters that tag's fit as a zero, and cells an admissibility gate will later set
    aside are still in it. `baseline-over-all-returned-cells` requires the second, and the
    first is what makes the background a background rather than the shape of whatever
    happened to be observed.

    A tag the panel declared but the reads never showed is fitted over all zeros. Every count
    is then identical, so no two-component fit exists and the pair establishes nothing -- the
    honest answer, and also the QC finding.

    Fits are local: every sample is fitted on its own cells, so two samples' probabilities do
    not share a currency. That is the ladder's property, not an artefact.
    """
    universe = pl.DataFrame(cells, orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    # Checked rather than deduplicated. A duplicate would add one zero to the population it
    # duplicates -- a background estimate over a population nobody chose, small, plausible,
    # and invisible in the output.
    if universe.height != universe.unique().height:
        raise ValueError("the cell universe holds duplicated cells; every fit's population would be wrong")

    # Scoped to the cell universe first, so a barcode outside the analysis never enters a
    # background it was not part of.
    scoped = counts.join(universe, on=CELL_KEY, how="semi")
    # The same reasoning as the universe check, one level down. A tag read twice in one cell
    # contributes twice to its own background and displaces a zero.
    if scoped.height != scoped.select(*CELL_KEY, "tag").unique().height:
        raise ValueError("the counts frame holds duplicated readings; every fit's population would be wrong")

    by_sample = {s: f.select("cellId") for (s,), f in universe.group_by("sampleId")}
    frames: list[pl.DataFrame] = []
    reasons: dict[tuple[str, str], str] = {}
    backgrounds: dict[tuple[str, str], Background] = {}
    for sample, tag in _declared_pairs(panel, sorted(by_sample)):
        sample_cells = by_sample.get(sample)
        if sample_cells is None:
            continue
        # Every cell in the sample, with an unobserved reading as the zero it is. The join
        # keeps the frame's own order, so the probabilities come back aligned to these cells.
        dense = sample_cells.join(
            scoped.filter((pl.col("sampleId") == sample) & (pl.col("tag") == tag)).select("cellId", "umiCount"),
            on="cellId",
            how="left",
        ).with_columns(pl.col("umiCount").fill_null(0))

        fit = fit_tag_probabilities(dense["umiCount"].to_numpy(), min_cells)
        if fit.probabilities is None:
            reasons[(sample, tag)] = fit.reason or NO_SEPARATION
            continue
        if fit.background is not None:
            backgrounds[(sample, tag)] = fit.background
        frames.append(
            dense.select("cellId")
            .with_columns(
                pl.lit(sample).alias("sampleId"),
                pl.lit(tag).alias("tag"),
                pl.Series("pBound", fit.probabilities, dtype=pl.Float64),
            )
            .select(*_PROB_SCHEMA)
        )

    probabilities = pl.concat(frames) if frames else pl.DataFrame(schema=_PROB_SCHEMA)
    return TagFits(probabilities, reasons, backgrounds)


_EMPTY = np.zeros(0, dtype=np.int64)


def _declared_pairs(panel: pl.DataFrame, samples: list[str]) -> list[tuple[str, str]]:
    """Every (sample, tag) the panel declares, with a global declaration expanded.

    A panel with no sample column declares one row per tag under ANY_SAMPLE, meaning every
    sample was stained with it. The fit is per sample regardless, because the population it
    is taken over is a sample's cells.
    """
    pairs = {(sample, tag) for tag, sample in panel.select("tag", "sample").iter_rows() if sample != ANY_SAMPLE}
    global_tags = sorted({tag for tag, sample in panel.select("tag", "sample").iter_rows() if sample == ANY_SAMPLE})
    pairs |= {(sample, tag) for sample in samples for tag in global_tags}
    return sorted(pairs)
