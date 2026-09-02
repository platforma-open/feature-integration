"""Rung 3's fit: a two-component negative binomial per (sample, tag), scored per cell.

The rule: on the raw counts, drop the counts above the 99th percentile, fit a two-component negative
binomial mixture, label the higher-median component the signal one, and give each cell the probability
that its count belongs to it. A cell reads bound at 0.9 or above.

Every bed here is generated from a seeded generator rather than written out by hand, because the thing
under test is a distribution and a hand-written handful of counts has none.

The population sizes are the ones a real run carries: 2000 cells per donor, and a binder fraction of a
few percent.

**One thing this file pins deliberately, and it looks like a bug.** A tag nothing bound still fits, and
the fit still calls some of its cells bound. The method assumes two components exist, so it splits a
single population and calls its upper slice signal, and no published test replaces the eye. An earlier
implementation rejected such a tag with a separation test of its own invention.
"""

import numpy as np
import polars as pl
import pytest
from panel import ANY_SAMPLE
from tag_distribution import (
    DEFAULT_DISTRIBUTION_MIN_CELLS,
    DEFAULT_INITIAL_SIGNAL_WEIGHT,
    NO_FIT,
    TOO_FEW_CELLS,
    _fit_two_component_nb,
    _signal_component,
    bound_at_count,
    fit_tag_probabilities,
    fit_tag_probabilities_by_pair,
)
from verdict import DISTRIBUTION_BOUND_PROBABILITY

SEED = 7


def _mixture(n_background, background_rate, n_binders, binder_rate, seed=SEED):
    """A background population and a binder population, in that order."""
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.poisson(background_rate, n_background), rng.poisson(binder_rate, n_binders)])


def _bound(probabilities):
    return probabilities >= DISTRIBUTION_BOUND_PROBABILITY


def _panel(rows):
    return pl.DataFrame(rows, orient="row", schema={"tag": pl.String, "sample": pl.String})


def _counts_frame(rows):
    return pl.DataFrame(
        rows,
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64},
    )


def _bed(n_cells=2000, n_binders=60, sample="S1", tag="AAAA", seed=SEED):
    """A sample of `n_cells` cells where `n_binders` of them bind `tag`.

    Returns the counts frame and the cell universe. Cells that read nothing are absent from the counts
    frame and present in the universe, which is the shape the block actually receives.
    """
    rng = np.random.default_rng(seed)
    values = np.concatenate([rng.poisson(2, n_cells - n_binders), rng.poisson(300, n_binders)])
    cells = [(sample, f"c{i}") for i in range(n_cells)]
    rows = [(sample, f"c{i}", tag, int(v)) for i, v in enumerate(values) if v > 0]
    return _counts_frame(rows), cells


# --- the cell condition -------------------------------------------------------------------------


def test_the_cell_condition_is_the_shipped_one():
    # 300 is the study's own bootstrapping figure, and it gates rather than tunes.
    assert DEFAULT_DISTRIBUTION_MIN_CELLS == 300


def test_a_sample_below_the_cell_condition_gets_no_fit():
    fit = fit_tag_probabilities(_mixture(279, 2, 20, 300))
    assert fit.probabilities is None
    assert fit.reason == TOO_FEW_CELLS


def test_a_sample_holding_exactly_the_cell_condition_fits():
    # "at least 300 cells", so 300 is inside. The below-floor case uses 299 and the above-floor one 400,
    # and nothing sat on the line itself -- so `<` and `<=` read the same across the whole suite.
    counts = _mixture(DEFAULT_DISTRIBUTION_MIN_CELLS - 15, 2, 15, 300)
    assert counts.size == DEFAULT_DISTRIBUTION_MIN_CELLS
    assert fit_tag_probabilities(counts).reason is None


def test_the_cell_condition_counts_cells_not_readings():
    # A mostly-silent tag over 400 cells clears the condition, though far fewer than 300 of them carry a
    # reading. The population the fit is taken over is the sample's cells.
    counts = _mixture(390, 0.2, 10, 200)
    assert int((counts > 0).sum()) < DEFAULT_DISTRIBUTION_MIN_CELLS
    assert fit_tag_probabilities(counts).reason is None


# --- what the fit calls -------------------------------------------------------------------------


def test_a_few_percent_binder_population_is_called():
    fit = fit_tag_probabilities(_mixture(1940, 2, 60, 300))
    assert fit.reason is None
    called = _bound(fit.probabilities)
    assert called[1940:].all(), "every planted binder must be called"
    assert not called[:1940].any(), "no background cell may be called"


def test_a_one_percent_binder_population_is_still_called():
    fit = fit_tag_probabilities(_mixture(1980, 2, 20, 300))
    assert fit.reason is None
    assert _bound(fit.probabilities)[1980:].all()


def test_a_mostly_silent_background_is_handled():
    # A background that read almost nothing is the common shape, and the zeros are most of it.
    fit = fit_tag_probabilities(_mixture(1900, 0.2, 100, 200))
    assert fit.reason is None
    assert _bound(fit.probabilities)[1900:].all()


def test_an_even_split_is_handled():
    # Two populations of the same size. The published method's own paper rejects a Gaussian mixture
    # because it degrades on UNEQUAL populations, so the equal case must also hold.
    fit = fit_tag_probabilities(_mixture(1000, 2, 1000, 300))
    assert fit.reason is None
    assert _bound(fit.probabilities)[1000:].all()


def test_the_signal_component_is_the_higher_median_one_even_where_the_means_disagree():
    """The labelling rule itself, on parameters rather than on a fitted bed.

    `what-plays-the-baseline` fixes the rule as the higher-median component. A negative binomial's
    median is not ordered by its mean -- the median depends on the size too -- so the two orderings can
    disagree, and labelling by mean inverts every call for the tag: cells reading nothing score high and
    cells reading a lot score low. That was a shipped bug, fixed by labelling on the median.

    Asserted on component parameters directly, NOT through a fit. The old form of this test built a bed
    whose components the mean ordering got wrong, and so it also depended on the EM reaching one
    particular decomposition of that bed -- which the initialisation decides. That made a labelling-rule
    guard fail whenever the initialisation changed, for reasons that had nothing to do with labelling.
    The rule is a pure function of the fitted parameters, so it is tested as one.
    """
    # The pair from `_signal_component`'s own docstring. Component 0 has the higher MEAN, component 1
    # the higher MEDIAN: at mean 50 with size 0.05 the median is 0, at mean 5 with size 1e6 it is 5.
    assert _signal_component(np.array([50.0, 5.0]), np.array([0.05, 1e6])) == 1

    # Where the two orderings agree there is nothing to choose between them.
    assert _signal_component(np.array([2.0, 40.0]), np.array([3.0, 4.0])) == 1


def test_two_components_with_equal_medians_are_separated_by_their_means():
    # Fitted over mostly-zero counts, both components have a median of zero, and the published rule
    # says nothing about that case -- the mean is all that is left to separate them. Pinned because
    # that tie-break is our own choice, so it needs to be written down somewhere.
    assert _signal_component(np.array([2.0, 8.0]), np.array([0.02, 0.02])) == 1


def test_a_cell_that_read_nothing_is_never_called_bound():
    """A cell holding no count of a tag cannot bind it, whatever the fit made of the rest.

    This is the invariant `silent_tally` rests on. It resolves the unobserved positions by arithmetic --
    asked minus observed minus unreliable -- instead of reading each one, and that is only sound while a
    zero-count cell cannot reach the bound line. A breach would make the run call a cell bound in one
    place and count it not-bound in the other, with nothing raised.

    The bed is an ambient population that is mostly zero with a few enormous counts, which is the shape
    that puts the most pressure on it.
    """
    rng = np.random.default_rng(3)
    ambient = rng.negative_binomial(0.15, 0.15 / (0.15 + 40), 1000)
    binders = rng.poisson(12, 1000)
    counts = np.concatenate([ambient, binders])

    fit = fit_tag_probabilities(counts)
    assert fit.reason is None
    silent = fit.probabilities[counts == 0]
    assert silent.size > 0, "the bed must hold cells that read nothing for this to say anything"
    assert silent.max() < DISTRIBUTION_BOUND_PROBABILITY, "a cell holding no count of the tag cannot bind it"


def test_an_ambient_tail_heavier_than_the_binders_is_labelled_the_signal_and_says_nothing():
    """KNOWN LIMITATION. This test records it; it does not guard against it.

    When the background counts have a long enough tail that their MEAN sits above the binders', the fit
    splits the data the wrong way. Instead of {background} and {binders} it finds {everything} and {the
    background's tail}, and calls that tail the signal. Every real binder then scores below the line,
    and part of the background tail scores above it.

    Not one unlucky dataset: 0 of 12 seeds come out right on this shape, at every binder share from 2%
    to 50%. It is a property of where the fit starts.

    The median start this used to have got this shape right -- and got the mostly-background case wrong
    instead, which is every tag in a real run. Neither start wins both. The source paper reports the
    same weakness for its own no-control path.

    THE RUN GIVES NO WARNING. That is what the asserts below are for: the fit comes back with its
    signal mean above its background mean, so the probability rises with the count and an ordinary
    threshold gets drawn. Nothing in the output distinguishes this from a fit that got it right. Anyone
    building a check for it should start here.
    """
    rng = np.random.default_rng(3)
    ambient = rng.negative_binomial(0.15, 0.15 / (0.15 + 40), 1000)
    binders = rng.poisson(12, 1000)
    counts = np.concatenate([ambient, binders])

    fit = fit_tag_probabilities(counts)
    assert fit.reason is None
    # The wrong population carries the signal label: the binders score BELOW the ambient.
    assert fit.probabilities[1000:].mean() < fit.probabilities[:1000].mean()
    assert not _bound(fit.probabilities[1000:]).any(), "no real binder clears the line on this bed"
    assert _bound(fit.probabilities[:1000]).any(), "part of the ambient tail does"
    # And it looks healthy. Two means the right way round, so a gate is drawn like any other fit.
    assert fit.background.signal_mean > fit.background.mean
    counts_seen = np.unique(counts)
    curve = (counts_seen, np.array([fit.probabilities[counts == c][0] for c in counts_seen]))
    assert bound_at_count(curve, DISTRIBUTION_BOUND_PROBABILITY) is not None, (
        "the fit resolves a bound count, so nothing marks this as suspect"
    )


def test_the_probability_is_a_probability():
    fit = fit_tag_probabilities(_mixture(1940, 2, 60, 300))
    assert fit.probabilities.min() >= 0.0
    assert fit.probabilities.max() <= 1.0


def test_one_probability_per_cell():
    counts = _mixture(1940, 2, 60, 300)
    assert fit_tag_probabilities(counts).probabilities.size == counts.size


# --- what does not fit --------------------------------------------------------------------------


def test_a_tag_every_cell_read_identically_does_not_fit():
    # One value is one population by construction. That is the answer rather than an error.
    fit = fit_tag_probabilities(np.full(2000, 4, dtype=np.int64))
    assert fit.probabilities is None
    assert fit.reason == NO_FIT


def test_a_tag_no_cell_read_at_all_does_not_fit():
    fit = fit_tag_probabilities(np.zeros(2000, dtype=np.int64))
    assert fit.probabilities is None
    assert fit.reason == NO_FIT


def test_a_tag_nothing_bound_still_fits_and_now_calls_no_cell_bound():
    """A single population still FITS -- no rejection -- and no longer reaches the bound line.

    The method assumes two components exist, so it splits a single population and calls its upper slice
    signal. That much is unchanged, and the pin below is unchanged with it: the fit must return
    probabilities rather than refuse, because rejecting here would be a separation test of our own
    invention and `what-plays-the-baseline` declines to build one.

    What changed is WHERE the split lands. This rung now starts the EM from the source paper's own
    pivot rather than from the median, and from that start a single background population no longer
    produces a slice above 0.9. The earlier assertion -- that some cell IS called bound -- recorded the
    median start's behaviour, not a decision the spec had taken: the spec fixes the trim, the labelling
    rule and the 0.9, and says nothing about the initialisation.

    So this test still pins the thing it was written to pin, that nobody restores the rejection, and
    reads the count off the fit instead of requiring it to be non-zero.

    The background here is OVERDISPERSED rather than Poisson, which is what a real one is: a Poisson bed
    would pass for the wrong reason.
    """
    rng = np.random.default_rng(SEED)
    fit = fit_tag_probabilities(rng.negative_binomial(3, 3 / (3 + 2), size=2000))
    assert fit.reason is None, "a single population must still fit rather than be rejected"
    assert fit.probabilities is not None
    assert fit.probabilities.size == 2000
    assert not _bound(fit.probabilities).any(), "the paper's pivot invents no binders on this bed"


# --- the trim -----------------------------------------------------------------------------------


def test_the_trimmed_cells_still_get_a_probability():
    # The fit drops the counts above the 99th percentile so a handful of very high readings cannot drag
    # the signal component's mean. Those cells are the most bound in the sample.
    counts = _mixture(1940, 2, 60, 300)
    counts[-1] = 100_000
    fit = fit_tag_probabilities(counts)
    assert fit.reason is None
    assert fit.probabilities.size == counts.size
    assert _bound(fit.probabilities)[-1], "the highest reading in the sample must read bound"


def test_the_fit_is_deterministic():
    counts = _mixture(1940, 2, 60, 300)
    first = fit_tag_probabilities(counts).probabilities
    second = fit_tag_probabilities(counts).probabilities
    assert np.array_equal(first, second)


# --- the per-pair driver ------------------------------------------------------------------------


def test_the_silent_cells_are_in_the_fit():
    # The counts frame holds only observed readings. A fit over those alone is a fit over the cells that
    # read SOMETHING, which is not the background -- and on a mostly-silent tag it is barely any of it.
    rng = np.random.default_rng(SEED)
    cells = [("S1", f"c{i}") for i in range(2000)]
    values = np.concatenate([rng.poisson(0.2, 1900), rng.poisson(200, 100)])
    rows = [("S1", f"c{i}", "AAAA", int(v)) for i, v in enumerate(values) if v > 0]
    counts = _counts_frame(rows)
    assert counts.height < 500, "the bed must be mostly silent or it proves nothing"

    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    assert fits.reasons == {}
    assert fits.probabilities.height == 2000, "every cell in the sample is scored"


def test_the_probabilities_are_keyed_to_the_right_cells():
    # The fit is taken over an array and the answer is read back per cell, so the alignment between the
    # two is the thing most easily lost. Only the planted binders may come back bound.
    counts, cells = _bed(n_cells=2000, n_binders=60)
    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    called = fits.probabilities.filter(pl.col("pBound") >= DISTRIBUTION_BOUND_PROBABILITY)
    planted = {f"c{i}" for i in range(1940, 2000)}
    assert set(called["cellId"].to_list()) == planted


def test_a_declared_tag_the_reads_never_showed_gets_no_fit():
    # Fitted over all zeros. One population, so no fit -- the honest answer, and the quality finding.
    counts, cells = _bed()
    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1"), ("DEAD", "S1")]))
    assert fits.reasons == {("S1", "DEAD"): NO_FIT}
    assert set(fits.probabilities["tag"].unique().to_list()) == {"AAAA"}


def _weak_reagent_bed(sample="S1", tag="AAAA", n_cells=400, n_binders=100, probe_count=3, seed=5):
    """A weak reagent: a signal population around five counts beside a near-silent background.

    One extra cell reads `probe_count`, which such a fit calls bound with near certainty -- and which
    the shipped minimum of four says is not evidence of binding. Returns the counts frame, the cell
    universe, and the probe cell's id.
    """
    rng = np.random.default_rng(seed)
    values = np.concatenate([rng.poisson(0.01, n_cells - n_binders), rng.poisson(5, n_binders)])
    cells = [(sample, f"c{i}") for i in range(n_cells)] + [(sample, "probe")]
    rows = [(sample, f"c{i}", tag, int(v)) for i, v in enumerate(values) if v > 0]
    rows.append((sample, "probe", tag, probe_count))
    return _counts_frame(rows), cells, (sample, "probe")


def _probe_probability(fits, key, tag="AAAA"):
    row = fits.probabilities.filter(
        (pl.col("sampleId") == key[0]) & (pl.col("cellId") == key[1]) & (pl.col("tag") == tag)
    )
    return row["pBound"].item()


def test_the_minimum_count_decides_what_a_cell_is_scored_on():
    # A count below the declared minimum is not evidence of binding, decided per cell and per tag on
    # the raw count before anything is read against a baseline. That rule does not stop at this rung:
    # a weak reagent fits a distribution that calls a count of three bound with near certainty, and
    # with the shipped floor of four that cell is scored on the zero the minimum made it.
    counts, cells, probe = _weak_reagent_bed()
    panel = _panel([("AAAA", "S1")])

    unfloored = fit_tag_probabilities_by_pair(counts, cells, panel)
    assert _probe_probability(unfloored, probe) >= DISTRIBUTION_BOUND_PROBABILITY, (
        "the bed has to reach a bound reading at the probe count, or the floor has nothing to change"
    )

    floored = fit_tag_probabilities_by_pair(counts, cells, panel, floor=4)
    assert _probe_probability(floored, probe) < DISTRIBUTION_BOUND_PROBABILITY


def test_the_minimum_does_not_narrow_the_population_the_fit_is_taken_over():
    # The fit stays on the raw counts, which is what this rung is specified on. Only the reading each
    # cell is scored on moves, so the background the run reports is the same either way.
    counts, cells, _ = _weak_reagent_bed()
    panel = _panel([("AAAA", "S1")])

    assert (
        fit_tag_probabilities_by_pair(counts, cells, panel, floor=4).backgrounds
        == fit_tag_probabilities_by_pair(counts, cells, panel).backgrounds
    )


def test_the_minimum_never_reaches_a_declared_baseline_tag():
    # `apply_floor` exempts a comparator with no switch, and the exemption has to hold here too or the
    # same tag would be floored on one path and spared on the other.
    counts, cells, probe = _weak_reagent_bed()
    panel = _panel([("AAAA", "S1")])
    spared = fit_tag_probabilities_by_pair(counts, cells, panel, floor=4, reference_tags={"AAAA"})
    assert _probe_probability(spared, probe) >= DISTRIBUTION_BOUND_PROBABILITY


def test_every_sample_is_fitted_on_its_own_cells():
    # Fits are local. Two samples staining one tag get two fits, and the one below the cell condition
    # gets none.
    big_counts, big_cells = _bed(n_cells=2000, sample="S1")
    small_counts, small_cells = _bed(n_cells=200, n_binders=6, sample="S2")
    counts = pl.concat([big_counts, small_counts])
    fits = fit_tag_probabilities_by_pair(counts, big_cells + small_cells, _panel([("AAAA", "S1"), ("AAAA", "S2")]))
    assert fits.reasons == {("S2", "AAAA"): TOO_FEW_CELLS}
    assert set(fits.probabilities["sampleId"].unique().to_list()) == {"S1"}


def test_a_panel_with_no_sample_column_is_fitted_per_sample_anyway():
    # ANY_SAMPLE declares the tag for every sample. The population a fit is taken over is still one
    # sample's cells, so there is still one fit each.
    big_counts, big_cells = _bed(n_cells=2000, sample="S1")
    small_counts, small_cells = _bed(n_cells=200, n_binders=6, sample="S2")
    counts = pl.concat([big_counts, small_counts])
    fits = fit_tag_probabilities_by_pair(counts, big_cells + small_cells, _panel([("AAAA", ANY_SAMPLE)]))
    assert fits.reasons == {("S2", "AAAA"): TOO_FEW_CELLS}
    assert fits.probabilities.height == 2000


def test_cells_outside_the_universe_do_not_enter_a_fit():
    # A barcode the analysis excluded carries real counts, and letting them into the background makes the
    # fit a fit over a population nobody chose.
    counts, cells = _bed()
    intruders = _counts_frame([("S1", "x1", "AAAA", 900), ("S1", "x2", "AAAA", 900)])
    clean = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    with_intruders = fit_tag_probabilities_by_pair(pl.concat([counts, intruders]), cells, _panel([("AAAA", "S1")]))
    assert clean.probabilities.equals(with_intruders.probabilities)


def test_a_duplicated_cell_is_refused():
    # A duplicate adds one zero to the population it duplicates -- a background over a population nobody
    # chose, small, plausible, and invisible in the output.
    counts, cells = _bed()
    with pytest.raises(ValueError, match="duplicated cells"):
        fit_tag_probabilities_by_pair(counts, cells[:-1] + [cells[0]], _panel([("AAAA", "S1")]))


def test_a_tag_read_twice_in_one_cell_is_refused():
    # A tag read twice in one cell contributes twice to its own background and displaces a zero.
    counts, cells = _bed()
    doubled = pl.concat([counts, counts.head(1)])
    with pytest.raises(ValueError, match="duplicated readings"):
        fit_tag_probabilities_by_pair(doubled, cells, _panel([("AAAA", "S1")]))


def test_a_fit_returns_the_background_it_fitted():
    # The parameters used to die inside the function that made them, so the one number a reader needs in
    # order to judge a fit never left it. Two clear populations: a low background and a high signal,
    # overdispersed so the negative binomial is the right model.
    rng = np.random.default_rng(11)
    background = rng.negative_binomial(2, 2 / (2 + 3), size=900)
    signal = rng.negative_binomial(6, 6 / (6 + 90), size=300)
    counts = np.concatenate([background, signal])

    fit = fit_tag_probabilities(counts, min_cells=100)

    assert fit.probabilities is not None
    assert fit.background is not None
    # The background sits below the signal, which is what labelling the higher-median component signal
    # means. Bounds rather than point values: the fit is stochastic.
    assert fit.background.mean < fit.background.signal_mean
    assert 0.0 < fit.background.weight < 1.0
    # The background holds the larger share, since three quarters of the cells are background.
    assert fit.background.weight > 0.5


def test_a_fit_that_established_nothing_carries_no_background():
    # `background` is None on exactly the condition `probabilities` is. A caller must branch on absence,
    # and a background sitting beside a None probability would invite the other reading.
    fit = fit_tag_probabilities(np.zeros(500, dtype=int), min_cells=100)
    assert fit.probabilities is None
    assert fit.background is None


def test_backgrounds_are_collected_per_sample_and_tag():
    # A pair in `reasons` contributes no background, and a pair that fitted contributes exactly one. The
    # two dicts partition the declared pairs.
    rng = np.random.default_rng(5)
    rows = []
    for sample in ("S1", "S2"):
        for cell in range(400):
            rows.append((sample, f"c{cell}", "T1", int(rng.negative_binomial(2, 2 / (2 + 4)))))
    counts = pl.DataFrame(
        rows, orient="row", schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64}
    )
    cells = [(s, f"c{c}") for s in ("S1", "S2") for c in range(400)]
    panel = pl.DataFrame({"sample": ["S1", "S2"], "tag": ["T1", "T1"]})

    fits = fit_tag_probabilities_by_pair(counts, cells, panel, min_cells=100)

    for key in fits.backgrounds:
        assert key not in fits.reasons
    for key in fits.reasons:
        assert key not in fits.backgrounds


def test_padding_the_universe_with_ambient_barcodes_collapses_the_fit():
    # The defect this guards against: the fit is over "the sample's cells", and feeding the observed
    # barcodes instead pads the population with ambient droplets. In droplet data they outnumber the cells
    # by one to two orders of magnitude and each holds a count or two, so both components land on that
    # mass and the fit reports a background sitting on top of its own signal.
    counts, cells = _bed()
    rng = np.random.default_rng(SEED + 1)
    ambient_ids = [("S1", f"ambient{i}") for i in range(50 * len(cells))]
    ambient_rows = [(s, c, "AAAA", 1) for s, c in ambient_ids if rng.random() < 0.35]

    clean = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    swamped = fit_tag_probabilities_by_pair(
        pl.concat([counts, _counts_frame(ambient_rows)]), cells + ambient_ids, _panel([("AAAA", "S1")])
    )

    over_cells = clean.backgrounds[("S1", "AAAA")]
    assert over_cells.signal_mean > over_cells.mean * 10

    # Over the barcodes the rung either refuses outright or returns two components that do not stand
    # apart. Both are unusable.
    over_barcodes = swamped.backgrounds.get(("S1", "AAAA"))
    assert over_barcodes is None or over_barcodes.signal_mean < over_barcodes.mean * 2


def test_bins_count_every_cell_the_fit_saw_including_the_zeros() -> None:
    # The plot a reader judges the background against is drawn from these bins, so they have to cover the
    # cells the fit actually saw: one entry per cell in the sample, with a cell that read nothing counted
    # as a zero. The sparse counts frame has no rows for those zeros, and on real data the zeros are most
    # of the background -- bin the sparse frame instead and the plot shows one decaying hump no matter
    # what the fit found, because the left half is simply missing.
    counts, cells = _bed()
    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))

    weights = fits.bins[("S1", "AAAA")]
    in_sample = len([c for c in cells if c[0] == "S1"])
    assert sum(weights) == in_sample, "every cell of the sample lands in a bin"

    observed = counts.filter((pl.col("sampleId") == "S1") & (pl.col("tag") == "AAAA")).height
    assert observed < in_sample, "the bed must leave some cells unobserved or this pins nothing"
    # The zeros sit in the first bin, which spans [0, expm1(width)) and so holds no other count.
    assert weights[0] == in_sample - observed


def test_bins_are_recorded_even_where_no_fit_came_out() -> None:
    # A pair where nothing separated is the case a reader most needs to see, so its bins are kept too. A
    # tag the reads never showed is all zeros, and cannot separate.
    _, cells = _bed()
    empty = _counts_frame([])
    fits = fit_tag_probabilities_by_pair(empty, cells, _panel([("AAAA", "S1")]))

    assert ("S1", "AAAA") in fits.reasons
    assert ("S1", "AAAA") not in fits.backgrounds
    weights = fits.bins[("S1", "AAAA")]
    # All zeros, so one bin holding every cell of the sample -- not an empty list.
    assert weights == [len([c for c in cells if c[0] == "S1"])]


def test_bound_at_count_takes_the_start_of_the_final_crossing_region():
    counts = np.array([0, 1, 2, 5, 9])
    probabilities = np.array([0.01, 0.10, 0.55, 0.93, 0.99])
    assert bound_at_count((counts, probabilities), 0.9) == 5


def test_bound_at_count_refuses_an_inverted_fit_rather_than_marking_its_low_crossing():
    # Components are labelled by median, so a pair can come back with its signal component BELOW its
    # background, and then the probability falls as the count rises. The low counts cross; no count
    # sits above the line, so there is no gate to draw.
    counts = np.array([0, 1, 2, 5, 9])
    probabilities = np.array([0.99, 0.95, 0.40, 0.05, 0.01])
    assert bound_at_count((counts, probabilities), 0.9) is None


def test_bound_at_count_is_none_where_the_fit_never_reaches_the_cutoff():
    assert bound_at_count((np.array([0, 1, 4]), np.array([0.1, 0.2, 0.7])), 0.9) is None


def test_bound_at_count_rises_with_the_cutoff():
    curve = (np.array([0, 1, 2, 5, 9]), np.array([0.01, 0.10, 0.92, 0.97, 0.995]))
    assert bound_at_count(curve, 0.9) == 2
    assert bound_at_count(curve, 0.95) == 5
    assert bound_at_count(curve, 0.999) is None


def test_curves_carry_one_entry_per_distinct_scored_count():
    counts = pl.DataFrame(
        {
            "sampleId": ["s"] * 6,
            "cellId": [f"c{i}" for i in range(6)],
            "tag": ["T"] * 6,
            "umiCount": [1, 1, 2, 2, 40, 41],
        }
    )
    cells = [("s", f"c{i}") for i in range(400)]
    panel = pl.DataFrame({"tag": ["T"], "sample": [ANY_SAMPLE]})
    fits = fit_tag_probabilities_by_pair(counts, cells, panel)
    curve = fits.curves.get(("s", "T"))
    if curve is None:
        pytest.skip("this population produced no fit, so it carries no curve")
    distinct, probabilities = curve
    assert distinct.tolist() == [0, 1, 2, 40, 41]
    assert probabilities.size == distinct.size
    # Ascending counts, so a monotone fit gives a resolvable crossing.
    assert np.all(np.diff(distinct) > 0)


# --- the expected binder fraction ---------------------------------------------------------------


def _from_bins(weights: list[int], top: int = 1381, bins: int = 24) -> np.ndarray:
    """Per-cell counts rebuilt from a run's own histogram, at each bin's geometric middle.

    Coarse on purpose: it carries the SHAPE of a real distribution into a test without a fixture file.
    """
    ratio = (top + 1) ** (1.0 / bins)
    edges = [1]
    while len(edges) < bins:
        step = max(edges[-1] + 1, round(edges[-1] * ratio))
        if step >= top:
            break
        edges.append(step)
    edges.append(top + 1)
    edges.insert(0, 0)
    out: list[int] = []
    for i, count in enumerate(weights):
        if count == 0:
            continue
        low, high = edges[i], edges[i + 1]
        out += [0 if low == 0 else int(round(np.sqrt(low * max(high - 1, low))))] * count
    return np.array(out)


# One real tag from a synthetic bound panel: a tight background, a gap at counts 7-11, then a broad
# upper mode holding 27% of the cells.
_BINDER_RICH = [
    2140,
    441,
    453,
    363,
    79,
    28,
    0,
    0,
    11,
    20,
    41,
    73,
    80,
    101,
    106,
    61,
    78,
    65,
    76,
    111,
    104,
    132,
    163,
    102,
]


def test_the_published_default_is_the_papers_initial_weight():
    assert DEFAULT_INITIAL_SIGNAL_WEIGHT == 0.1


def test_a_binder_rich_tag_needs_a_higher_expected_fraction_to_split_at_its_own_gap():
    """Why the fraction is settable at all.

    The default assumes a tenth of cells bind. This tag's upper mode holds 27% of them, so the pivot
    lands INSIDE that mode, the background component is seeded with most of the binders and ends up
    wide enough to explain the whole range, and no count is ever 90% likely to be signal. Told the
    right fraction, the same fit splits at the gap the histogram shows.
    """
    counts = _from_bins(_BINDER_RICH)
    x = counts.astype(float)

    default_fit = _fit_two_component_nb(x, DEFAULT_INITIAL_SIGNAL_WEIGHT)
    informed_fit = _fit_two_component_nb(x, 0.3)
    assert default_fit is not None and informed_fit is not None

    # The background the default settles on is two orders of magnitude wider than the real one.
    assert min(default_fit.means) > 10.0
    assert min(informed_fit.means) < 2.0


def test_the_expected_fraction_reaches_the_per_pair_driver():
    """Threaded rather than read from the module at the bottom of the stack.

    Asserted on the binder-rich shape, because that is where the weight changes the answer. A bed whose
    two populations stand well apart converges to the same fit from either start -- correctly, since the
    start only picks which optimum the EM walks to -- so it cannot tell a threaded weight from a
    dropped one.
    """
    population = _from_bins(_BINDER_RICH)
    cells = [("s", f"c{i}") for i in range(population.size)]
    observed = [(key[1], int(v)) for key, v in zip(cells, population) if v > 0]
    counts = pl.DataFrame(
        {
            "sampleId": ["s"] * len(observed),
            "cellId": [c for c, _ in observed],
            "tag": ["T"] * len(observed),
            "umiCount": [v for _, v in observed],
        }
    )
    panel = pl.DataFrame({"tag": ["T"], "sample": [ANY_SAMPLE]})

    default = fit_tag_probabilities_by_pair(counts, cells, panel, initial_signal_weight=DEFAULT_INITIAL_SIGNAL_WEIGHT)
    informed = fit_tag_probabilities_by_pair(counts, cells, panel, initial_signal_weight=0.3)
    low, high = default.backgrounds.get(("s", "T")), informed.backgrounds.get(("s", "T"))
    assert low is not None and high is not None
    # The same divergence the direct-fit test pins, seen through the driver.
    assert low.mean > 10.0
    assert high.mean < 2.0
