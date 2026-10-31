"""Rung 3: a tag's own count distribution across a sample's cells, split in two.

The rung: that tag's own distribution across the sample's cells, split into two components,
where the sample holds at least 300 cells. Separation is NOT a condition -- no automatic check
that the counts separated exists, so the run shows the fit and leaves the judgement to the
reader. It is the only rung with orthogonal validation behind it and the only one validated at
these panel sizes.

**Where the method comes from.** The study the 300-cell figure comes from computes a kernel
density estimate of log2 counts and takes *"the local minimum that optimally separates the KDE
into two populations"*. That paper applies it to cell hashes, not antigen barcodes -- its own
antigen rule is the empty-droplet one, which needs gene expression this block does not receive.
The same paper records why a Gaussian mixture or k-means is not used: both degrade when the two
populations are unequal in size, which is the antigen case exactly.

**This returns a comparator, not a classification.** The published use of the split point is a
threshold. Nothing downstream thresholds: it scores a reading against a comparator count. So the
split point identifies which readings are background, and the comparator is the middle of those.

**Where the fit starts.** While the EM runs, the two components are interchangeable -- the paper says
so, and both it and this module decide afterwards which is which, by median. So the starting point does
not change what the components mean; it changes which answer the EM settles on. On a mostly-background
population that is the difference between a background weight near 0.8 and one near 0.95. The starting
split is the paper's, `DEFAULT_INITIAL_SIGNAL_WEIGHT` above.

**No normalization.** The paper normalizes by each cell's UMI total. Every reading here is a raw
integer UMI count, and a normalized comparator would be the only non-count in the pipeline. The
cost is that a cell sequenced twice as deeply contributes a reading twice as large; the split is
taken across cells, so this widens both components rather than shifting one.

Both gates are gates, not settings: below 300 cells, or with no separation demonstrated, the
baseline this rung would produce is wrong rather than conservative.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import NamedTuple

import numpy as np
import polars as pl
from panel import ANY_SAMPLE
from qc_measures import bin_values, log1p_bin_edges, log1p_edges_for
from scipy.special import logsumexp
from scipy.stats import nbinom

CELL_KEY = ("sampleId", "cellId")

# Both from the study the method comes from, and both gate rather than tune.
DEFAULT_DISTRIBUTION_MIN_CELLS = 300


class Background(NamedTuple):
    """A fitted background, as a reader needs to see it.

    The two means together are the finding. A background alone says nothing about whether the counts
    separated.

    `weight` is the share of cells the background component holds. A tag that bound nothing is split
    anyway -- the method assumes two components exist -- and shows itself here as two means sitting
    almost on top of each other rather than as a refusal.
    """

    mean: float
    signal_mean: float
    weight: float


class TagFit(NamedTuple):
    """One tag's per-cell probability of binding in one sample, or why there is none.

    `probabilities` is None exactly when `reason` is not None, and a caller must branch on that
    rather than defaulting: a tag whose counts did not separate established NOTHING for its cells,
    which is not the same fact as every cell reading a low probability.

    Aligned to the counts the fit was given, one entry per cell in the sample.

    `background` travels with the probabilities and is None on exactly the same condition.
    """

    probabilities: np.ndarray | None
    reason: str | None
    cells: int
    background: Background | None = None


# The prose a reader sees. Nothing branches on these strings.
TOO_FEW_CELLS = "the sample holds too few cells for a distribution to be fitted"
# Reached only where no two-component fit exists to report: nothing survives the trim, the EM returns
# one component, or the probabilities are incomputable. It is a statement about the fit, never a
# finding that this tag's counts failed to separate -- no check for that is built.
NO_FIT = "no two-component fit could be computed for this tag"


# Roughly what share of cells are expected to bind an antigen. The source paper's own initial weight,
# and an OVERRIDE here rather than the default.
DEFAULT_INITIAL_SIGNAL_WEIGHT = 0.1

# The share of the highest counts dropped before the fit, so that a handful of very high readings
# cannot drag the signal component's mean up and pull the boundary with it. The dropped cells still
# get a probability -- they are the most bound cells in the sample.
_UPPER_TRIM_PERCENTILE = 99.0

# EM stops when the mean log-likelihood moves less than this, or after this many rounds. Neither
# number is published. They are convergence controls rather than parameters of the method.
_EM_TOLERANCE = 1e-6
_EM_MAX_ROUNDS = 200

# Two component means this close are one component. A numerical guard on the EM, never a test of
# whether a tag's counts separated -- no such test exists. Loose enough to catch a converged pair
# that float equality misses, tight enough that two means a reader would call distinct survive.
_CONVERGED_COMPONENT_RTOL = 1e-9

# The dispersion a component falls back to where its variance does not exceed its mean. The negative
# binomial has no such shape -- that is the Poisson boundary -- so the fit uses a large size, which
# IS Poisson in the limit, rather than failing.
_POISSON_LIMIT_SIZE = 1e6

# The smallest mean a component may hold. A component fitted entirely on zeros has a mean of zero,
# and a negative binomial with a mean of zero puts all of its mass on zero -- so every non-zero count
# becomes impossible under it and the fit collapses. A mostly-silent tag is the ordinary case here.
_MIN_COMPONENT_MEAN = 1e-6


def _nb_size(mean: float, variance: float) -> float:
    """The negative binomial's size from a mean and a variance, by moments.

    var = mean + mean^2 / size, so size = mean^2 / (var - mean). Where the variance does not exceed
    the mean the distribution is Poisson or narrower and no finite size fits it, so the Poisson limit
    stands in.
    """
    if mean <= 0.0 or variance <= mean:
        return _POISSON_LIMIT_SIZE
    return float(mean * mean / (variance - mean))


def _nb_logpmf(counts: np.ndarray, mean: float, size: float) -> np.ndarray:
    """The negative binomial log pmf, parameterised by mean and size rather than by p.

    LOG, not the density itself, and that is load-bearing. A binder's count sits far enough into the
    tail of the background component that the density underflows to exactly zero in double precision.
    Both components then call the count impossible, the normalisation divides by zero, and the cell
    most obviously bound in the sample is the one the fit cannot answer for.
    """
    return nbinom.logpmf(counts, size, size / (size + mean))


def _responsibilities(counts: np.ndarray, means: np.ndarray, sizes: np.ndarray, weights: np.ndarray):
    """Each count's posterior probability per component, and the mean log-likelihood.

    Softmax over log weights plus log densities, so a count in either tail normalises without
    underflowing. Returns None where a log density is not finite, which happens only for a degenerate
    component rather than for an extreme count.
    """
    logs = np.vstack([np.log(weights[k]) + _nb_logpmf(counts, means[k], sizes[k]) for k in (0, 1)])
    totals = logsumexp(logs, axis=0)
    # A single -inf is fine and expected: it says one component calls that count impossible. Only a
    # count BOTH reject has no answer, and that is a degenerate fit rather than an extreme reading.
    if not np.all(np.isfinite(totals)):
        return None
    return np.exp(logs - totals), float(np.mean(totals))


class _Mixture(NamedTuple):
    """A fitted two-component negative binomial, with the signal component identified."""

    means: np.ndarray
    sizes: np.ndarray
    weights: np.ndarray
    signal: int


# @TODO: Review this function in more detail looking for logic flaws that might have been missed
def _scan_pivot(x: np.ndarray) -> float | None:
    """The split point that best explains the counts as two negative binomials, or None.

    Scores every cut of the counts into `[0, t]` and the rest, and returns the `t` that scores best.
    None where the counts hold fewer than two distinct values, or where no cut scores finitely.
    """
    # The histogram, not the cells: everything below is sized by the number of DISTINCT counts, which is
    # why the scan costs milliseconds against the EM's seconds and does not grow with the sample.
    vals, mult = np.unique(x, return_counts=True)
    k = vals.size
    if k < 2:
        return None

    # Prefix sums of the cell count, of the counts, and of their squares. They make each cut's two-sided
    # weight, mean and variance a subtraction instead of a pass over the data.
    n = float(x.size)
    cum_w = np.cumsum(mult).astype(float)
    cum_s = np.cumsum(vals * mult)
    cum_q = np.cumsum((vals**2) * mult)

    # One entry per cut, and `[:-1]` is the cut set: cutting at the highest count leaves the upper side
    # empty. Variance by `E[x^2] - E[x]^2`, so it can land just below zero on a near-constant side.
    w_lo = cum_w[:-1]
    w_hi = n - w_lo
    m_lo = cum_s[:-1] / w_lo
    m_hi = (cum_s[-1] - cum_s[:-1]) / w_hi
    v_lo = cum_q[:-1] / w_lo - m_lo**2
    v_hi = (cum_q[-1] - cum_q[:-1]) / w_hi - m_hi**2
    m_lo = np.maximum(m_lo, _MIN_COMPONENT_MEAN)
    m_hi = np.maximum(m_hi, _MIN_COMPONENT_MEAN)

    # Dispersions through `_nb_size` rather than inline, so each side inherits the same Poisson-limit
    # fallback the EM's own components get and the two cannot disagree about what a dispersion is.
    s_lo = np.array([_nb_size(m, v) for m, v in zip(m_lo, np.maximum(v_lo, 0.0), strict=True)])
    s_hi = np.array([_nb_size(m, v) for m, v in zip(m_hi, np.maximum(v_hi, 0.0), strict=True)])

    # The score, one row per cut and one column per distinct count. It is the CLASSIFICATION
    # log-likelihood -- every count read against the side its cut assigns it, weighted by that side's
    # share and by how many cells hold the count.
    below = vals[None, :] <= vals[:-1, None]
    lo = _nb_logpmf(vals[None, :], m_lo[:, None], s_lo[:, None]) + np.log(w_lo / n)[:, None]
    hi = _nb_logpmf(vals[None, :], m_hi[:, None], s_hi[:, None]) + np.log(w_hi / n)[:, None]
    scored = np.where(below, lo, hi) * mult[None, :]

    # A cut where any count underflowed is dropped rather than left to win the argmax on a -inf.
    total = np.where(np.all(np.isfinite(scored), axis=1), scored.sum(axis=1), -np.inf)
    if not np.any(np.isfinite(total)):
        return None
    return float(vals[int(np.argmax(total))])


def _fit_two_component_nb(counts: np.ndarray, initial_signal_weight: float | None = None) -> _Mixture | None:
    """A two-component negative binomial fitted to `counts`, or None where none exists.

    The method: fit a two-component negative binomial mixture and label the higher-median component
    the signal one. Scoring a cell against the fit is `_signal_probability`, kept separate because the
    fit is taken over the trimmed counts and every cell is scored, including the trimmed ones.

    Returns None where no two-component fit exists -- every count identical, or the two components
    converging onto one another. A tag with no binders has one population, and the honest answer is
    that this rung established nothing for it.

    EM with the dispersions re-estimated by moments each round. The dispersions are not free
    parameters of the likelihood here: solving for them jointly needs a numerical root per component
    per round, and the moment estimate is stable on integer counts where that is not. A round that
    produces a degenerate component ends the fit rather than continuing from it.
    """
    x = counts.astype(float)
    if np.unique(x).size < 2:
        return None

    # Where the scientist stated a share, split the counts so that `initial_signal_weight` of them sit
    # above the pivot. Otherwise derive the split from the counts themselves. Each side then seeds one
    # component, and the two side sizes are the starting weights.
    scanned = _scan_pivot(x) if initial_signal_weight is None else None
    if scanned is not None:
        pivot = scanned
    else:
        pivot = float(np.quantile(x, 1.0 - (initial_signal_weight or DEFAULT_INITIAL_SIGNAL_WEIGHT)))
    low, high = x[x <= pivot], x[x > pivot]
    if low.size == 0 or high.size == 0:
        # The quantile landed on the maximum, so one side got every cell. Split at the mean instead:
        # on a skewed count distribution the mean sits below the upper quantile, so it still divides.
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

        # M step. A component that loses all its mass has collapsed, and continuing from it fits one
        # population while reporting two.
        mass = responsibilities.sum(axis=1)
        if np.any(mass <= 0.0):
            return None
        weights = mass / x.size
        for k in (0, 1):
            r = responsibilities[k]
            means[k] = max(float((r * x).sum() / mass[k]), _MIN_COMPONENT_MEAN)
            variance = float((r * (x - means[k]) ** 2).sum() / mass[k])
            sizes[k] = _nb_size(means[k], variance)

        # The two components have converged onto each other, so there is one population. Compared to a
        # relative tolerance, not by `==`: two floats from separate M-step reductions land on the same
        # value only by luck, so exact equality let a converged pair through.
        #
        # The tolerance is numerical and NOT a separation criterion. It catches an EM that ran two
        # components onto one solution, and nothing else.
        if np.isclose(means[0], means[1], rtol=_CONVERGED_COMPONENT_RTOL, atol=0.0):
            return None

        if abs(loglik - previous) < _EM_TOLERANCE:
            break
        previous = loglik

    return _Mixture(means.copy(), sizes.copy(), weights.copy(), _signal_component(means, sizes))


def _signal_component(means: np.ndarray, sizes: np.ndarray) -> int:
    """Which component is the signal one: the higher MEDIAN, the mean breaking a tie.

    The published rule is the higher-median component, and a negative binomial's
    median is not ordered by its mean. The median depends on the size as much as the mean: at mean 50
    and size 0.05 it is 0, at mean 5 and size 1e6 it is 5. Sizes are re-estimated per component from
    that component's own variance every round, so the two orderings are free to disagree -- and an
    overdispersed component with a high mean is what ambient mass looks like.

    Labelling the wrong one inverts every call for the tag, since `_signal_probability` then returns
    the other component's responsibility: cells reading nothing score high and cells reading a lot
    score low.

    Two components fitted over mostly-zero counts both have a median of zero. The published rule does
    not resolve that, and the mean is the only thing left separating them.
    """
    medians = [float(nbinom.median(sizes[k], sizes[k] / (sizes[k] + means[k]))) for k in (0, 1)]
    if medians[0] != medians[1]:
        return int(np.argmax(medians))
    return int(np.argmax(means))


def _signal_probability(counts: np.ndarray, fit: _Mixture) -> np.ndarray | None:
    """Each count's probability of belonging to the fit's signal component.

    Separate from the fit so that a cell trimmed out of the fit still gets an answer. Those cells are
    the most bound in the sample.
    """
    step = _responsibilities(counts.astype(float), fit.means, fit.sizes, fit.weights)
    if step is None:
        return None
    return step[0][fit.signal]


def fit_tag_probabilities(
    counts: np.ndarray,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
    scored: np.ndarray | None = None,
    initial_signal_weight: float | None = None,
) -> TagFit:
    """One tag's counts across one sample's cells, as a probability of binding per cell.

    `counts` is one entry per cell in the sample, INCLUDING cells that read nothing, which enter as
    zeros, and including cells an admissibility gate will later set aside. A population narrowed by
    the gate would be narrowed by the baseline's own consequences: excluding the highest readings
    lowers the baseline, which excludes more cells, which lowers it again. Passing only observed
    readings breaks it the same way -- the background is where most cells sit, and the cells that read
    nothing are most of it.

    The count of cells, not of readings, is what the 300 gates.

    `scored` is the reading each cell is turned into a state on, defaulting to `counts`. The two differ
    by the minimum: this rung is fitted on the RAW counts, while the minimum decides a count per cell
    and per tag on the raw count, before anything is read against a baseline. A count the minimum
    zeroed is therefore in
    the population the background is estimated from and is read as the zero it became. Both arrays are
    one entry per cell, in the same order.

    The returned probabilities are aligned to `scored`. The trim above the 99th percentile keeps those
    cells and excludes them from the FIT alone.
    """
    if scored is None:
        scored = counts
    if scored.size != counts.size:
        raise ValueError(
            f"the fitted population holds {counts.size} cells and the scored one {scored.size}: "
            "both are one entry per cell of the sample and the probabilities are aligned to the second"
        )
    n = int(counts.size)
    if n < min_cells:
        return TagFit(None, TOO_FEW_CELLS, n)

    # Fit without the top tail, then score every cell against what was fitted.
    ceiling = float(np.percentile(counts.astype(float), _UPPER_TRIM_PERCENTILE))
    fitted_on = counts[counts.astype(float) <= ceiling]
    if fitted_on.size == 0:
        return TagFit(None, NO_FIT, n)

    fit = _fit_two_component_nb(fitted_on, initial_signal_weight)
    if fit is None:
        return TagFit(None, NO_FIT, n)
    probabilities = _signal_probability(scored, fit)
    if probabilities is None:
        return TagFit(None, NO_FIT, n)
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
    probability that cell's count belongs to the signal component. A pair that established nothing
    contributes no rows and appears in `reasons` instead. A caller must branch on absence rather than
    defaulting: a tag that established nothing said NOTHING about its cells.
    """

    probabilities: pl.DataFrame
    reasons: dict[tuple[str, str], str]
    # Each pair's fitted cells, already binned for the plot: one entry per cell in the sample, with a
    # cell that read nothing counted as a zero.
    bins: dict[tuple[str, str], list[int]]
    # One entry per pair that fitted, on the same condition as a contribution to `probabilities`. A
    # pair in `reasons` is absent here.
    backgrounds: dict[tuple[str, str], Background] = {}
    # Each fitted pair's distinct counts, ascending, with the probability the fit gave each one.
    curves: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}


_PROB_SCHEMA = {"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "pBound": pl.Float64}


def fit_tag_probabilities_by_pair(
    counts: pl.DataFrame,
    cells: list[tuple[str, str]],
    panel: pl.DataFrame,
    min_cells: int = DEFAULT_DISTRIBUTION_MIN_CELLS,
    floor: int = 0,
    reference_tags: Collection[str] = (),
    initial_signal_weight: float | None = None,
) -> TagFits:
    """One fit per (sample, tag) the panel declares, scored per cell.

    Per tag and before any grouping: a background fitted per identity would depend on the grouping,
    so changing a grouping would change the background and a regrouping would stop being a re-reading
    of unchanged counts.

    `counts` is the RAW frame -- not floored, not densified. `cells` is the analysed cell universe,
    and it is the population every fit is taken over: a cell that read nothing for a tag enters that
    tag's fit as a zero, and cells an admissibility gate will later set aside are still in it. That is
    what makes the background a background rather than the shape of whatever happened to be observed.

    `floor` is the declared minimum count, and it applies to what each cell is SCORED on rather than to
    what the fit is taken over. The two rules meet here and neither gives way: this rung fits on the raw
    counts, and a count below the minimum is not evidence of binding, decided per cell and per tag on
    the raw count before anything is read against a baseline. So a floored reading stays in the
    background population and is read as the zero it became -- which is how it contributes to every
    other rung. `reference_tags` are exempt from the floor here for the same reason `apply_floor`
    exempts them: the minimum asks whether a count is evidence of binding, and a tag declared to be
    bound by nothing never is.

    A tag the panel declared but the reads never showed is fitted over all zeros. Every count is then
    identical, so no two-component fit exists and the pair establishes nothing.

    Fits are local: every sample is fitted on its own cells, so two samples' probabilities do not
    share a currency.
    """
    universe = pl.DataFrame(cells, orient="row", schema={"sampleId": pl.String, "cellId": pl.String})
    # Checked rather than deduplicated. A duplicate would add one zero to the population it duplicates
    # -- a background estimate over a population nobody chose, small, plausible, and invisible.
    if universe.height != universe.unique().height:
        raise ValueError("the cell universe holds duplicated cells; every fit's population would be wrong")

    # Scoped to the cell universe first, so a barcode outside the analysis never enters a background it
    # was not part of.
    scoped = counts.join(universe, on=CELL_KEY, how="semi")
    # The same reasoning as the universe check, one level down. A tag read twice in one cell contributes
    # twice to its own background and displaces a zero.
    if scoped.height != scoped.select(*CELL_KEY, "tag").unique().height:
        raise ValueError("the counts frame holds duplicated readings; every fit's population would be wrong")

    exempt = set(reference_tags)
    by_sample = {s: f.select("cellId") for (s,), f in universe.group_by("sampleId")}
    frames: list[pl.DataFrame] = []
    reasons: dict[tuple[str, str], str] = {}
    binned: dict[tuple[str, str], list[int]] = {}
    backgrounds: dict[tuple[str, str], Background] = {}
    curves: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    for sample, tag in _declared_pairs(panel, sorted(by_sample)):
        sample_cells = by_sample.get(sample)
        if sample_cells is None:
            continue
        # Every cell in the sample, with an unobserved reading as the zero it is. The join keeps the
        # frame's own order, so the probabilities come back aligned to these cells.
        dense = sample_cells.join(
            scoped.filter((pl.col("sampleId") == sample) & (pl.col("tag") == tag)).select("cellId", "umiCount"),
            on="cellId",
            how="left",
        ).with_columns(pl.col("umiCount").fill_null(0))

        raw = dense["umiCount"].to_numpy()
        # Binned before the fit is attempted, so a pair that established nothing still carries the
        # distribution a reader would judge that outcome against. `raw` dies with the iteration; only
        # the bins outlive it.
        binned[(sample, tag)] = (
            bin_values(raw, log1p_bin_edges(int(raw.max())) or log1p_edges_for(1)) if raw.size else []
        )
        scored = raw if floor <= 0 or tag in exempt else np.where(raw < floor, 0, raw)
        fit = fit_tag_probabilities(raw, min_cells, scored=scored, initial_signal_weight=initial_signal_weight)
        if fit.probabilities is None:
            reasons[(sample, tag)] = fit.reason or NO_FIT
            continue
        if fit.background is not None:
            backgrounds[(sample, tag)] = fit.background
        # Built from `scored`, not `raw`. The probabilities were computed on the scored values, so a
        # reading the floor zeroed carries the probability of 0, not of the count it originally held.
        distinct, first = np.unique(scored, return_index=True)
        curves[(sample, tag)] = (distinct, np.asarray(fit.probabilities, dtype=float)[first])
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
    return TagFits(probabilities, reasons, binned, backgrounds, curves)


def bound_at_count(curve: tuple[np.ndarray, np.ndarray], probability_cutoff: float) -> int | None:
    """The lowest count at or above which every count is called bound, or None if there is none.

    `curve` is one `TagFits.curves` entry: ascending distinct counts, and the probability each was given.

    Note it looks for the lowest count from which the cutoff holds all the way up, NOT simply the first
    count to cross it. Usually these are the same, because the probability normally rises with the count.

    They differ when a fit comes back inverted. Components are labelled by median, so a pair can end up
    with its "signal" component sitting BELOW its background. The probability then FALLS as the count
    rises, and it is the LOW counts that cross the cutoff. Taking the first crossing would report a
    threshold of 1, which the run does not apply. Requiring the cutoff to hold all the way up returns
    None instead, which says plainly that this fit has no count above which it calls a cell bound.
    """
    counts, probabilities = curve
    if counts.size == 0 or counts.size != probabilities.size:
        return None
    missed = np.flatnonzero(probabilities < probability_cutoff)
    start = 0 if missed.size == 0 else int(missed[-1]) + 1
    return int(counts[start]) if start < counts.size else None


_EMPTY = np.zeros(0, dtype=np.int64)


def _declared_pairs(panel: pl.DataFrame, samples: list[str]) -> list[tuple[str, str]]:
    """Every (sample, tag) the panel declares, with a global declaration expanded.

    A panel with no sample column declares one row per tag under ANY_SAMPLE. The fit is per sample
    regardless, because the population it is taken over is a sample's cells.
    """
    pairs = {(sample, tag) for tag, sample in panel.select("tag", "sample").iter_rows() if sample != ANY_SAMPLE}
    global_tags = sorted({tag for tag, sample in panel.select("tag", "sample").iter_rows() if sample == ANY_SAMPLE})
    pairs |= {(sample, tag) for sample in samples for tag in global_tags}
    return sorted(pairs)
