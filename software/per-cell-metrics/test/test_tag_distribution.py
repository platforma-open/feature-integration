"""Rung 3's fit: a two-component negative binomial per (sample, tag), scored per cell.

`what-plays-the-baseline` fixes the rule: on the raw counts, drop the counts above the 99th percentile,
fit a two-component negative binomial mixture, label the higher-median component the signal one, and
give each cell the probability that its count belongs to it. A cell reads bound at 0.9 or above.

Every bed here is generated from a seeded generator rather than written out by hand, because the thing
under test is a distribution and a hand-written handful of counts has none. The seed is fixed, so the
beds are the same bytes on every run and on every machine.

The population sizes are the ones a real run carries: the manual bed's presets are 2000 cells per donor,
and a binder fraction of a few percent is what this method exists to find.

**One thing this file pins deliberately, and it looks like a bug.** A tag nothing bound still fits, and
the fit still calls some of its cells bound. `what-plays-the-baseline` says so outright -- the method
assumes two components exist, "the fit will split that single population anyway and call its upper slice
signal, inventing binders on exactly the tag that had none", and no published test replaces the eye. An
earlier implementation rejected such a tag with a separation test of its own invention. That test is
exactly what the spec refuses, so it is gone, and the run shows the fit instead of judging it.
"""

import numpy as np
import polars as pl
import pytest
from panel import ANY_SAMPLE
from tag_distribution import (
    DEFAULT_DISTRIBUTION_MIN_CELLS,
    NO_SEPARATION,
    TOO_FEW_CELLS,
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


def test_the_cell_condition_counts_cells_not_readings():
    # A mostly-silent tag over 400 cells clears the condition, though far fewer than 300 of them
    # carry a reading. The population the fit is taken over is the sample's cells.
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
    # Two populations of the same size. The published method's own paper rejects a Gaussian
    # mixture because it degrades on UNEQUAL populations, so the equal case must also hold.
    fit = fit_tag_probabilities(_mixture(1000, 2, 1000, 300))
    assert fit.reason is None
    assert _bound(fit.probabilities)[1000:].all()


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
    assert fit.reason == NO_SEPARATION


def test_a_tag_no_cell_read_at_all_does_not_fit():
    fit = fit_tag_probabilities(np.zeros(2000, dtype=np.int64))
    assert fit.probabilities is None
    assert fit.reason == NO_SEPARATION


def test_a_tag_nothing_bound_still_fits_and_calls_some_cells_bound():
    # DELIBERATE, and the spec says so: the method assumes two components exist, so it splits a
    # single population and calls its upper slice signal. Rejecting this would be a separation test
    # of our own invention, which `what-plays-the-baseline` refuses -- the run shows the fit instead.
    #
    # The background here is OVERDISPERSED rather than Poisson, which is what a real one is: the
    # invented binders are the long tail of a single skewed population, so a Poisson bed does not
    # produce them and would let this pass for the wrong reason.
    #
    # Pinned so that nobody restores the rejection as a bug fix. If the spec ever admits a published
    # separation test, this is the test to change.
    rng = np.random.default_rng(SEED)
    fit = fit_tag_probabilities(rng.negative_binomial(3, 3 / (3 + 2), size=2000))
    assert fit.reason is None
    assert _bound(fit.probabilities).any(), "the spec accepts invented binders on a tag that bound nothing"


# --- the trim -----------------------------------------------------------------------------------


def test_the_trimmed_cells_still_get_a_probability():
    # The fit drops the counts above the 99th percentile so a handful of very high readings cannot
    # drag the signal component's mean. Those cells are the most bound in the sample, so withholding
    # an answer for them would be the opposite of what the trim is for.
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
    # The counts frame holds only observed readings. A fit over those alone is a fit over the cells
    # that read SOMETHING, which is not the background -- and on a mostly-silent tag it is barely
    # any of it.
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
    # The fit is taken over an array and the answer is read back per cell, so the alignment between
    # the two is the thing most easily lost. Only the planted binders may come back bound.
    counts, cells = _bed(n_cells=2000, n_binders=60)
    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    called = fits.probabilities.filter(pl.col("pBound") >= DISTRIBUTION_BOUND_PROBABILITY)
    planted = {f"c{i}" for i in range(1940, 2000)}
    assert set(called["cellId"].to_list()) == planted


def test_a_declared_tag_the_reads_never_showed_gets_no_fit():
    # Fitted over all zeros. One population, so no fit -- the honest answer, and the quality finding.
    counts, cells = _bed()
    fits = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1"), ("DEAD", "S1")]))
    assert fits.reasons == {("S1", "DEAD"): NO_SEPARATION}
    assert set(fits.probabilities["tag"].unique().to_list()) == {"AAAA"}


def test_every_sample_is_fitted_on_its_own_cells():
    # Fits are local. Two samples staining one tag get two fits, and the one below the cell
    # condition gets none -- the other is unaffected.
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
    # A barcode the analysis excluded carries real counts, and letting them into the background makes
    # the fit a fit over a population nobody chose.
    counts, cells = _bed()
    intruders = _counts_frame([("S1", "x1", "AAAA", 900), ("S1", "x2", "AAAA", 900)])
    clean = fit_tag_probabilities_by_pair(counts, cells, _panel([("AAAA", "S1")]))
    with_intruders = fit_tag_probabilities_by_pair(pl.concat([counts, intruders]), cells, _panel([("AAAA", "S1")]))
    assert clean.probabilities.equals(with_intruders.probabilities)


def test_a_duplicated_cell_is_refused():
    # A duplicate adds one zero to the population it duplicates. That is a background over a
    # population nobody chose -- small, plausible, and invisible in the output.
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
    # The parameters used to die inside the function that made them, so the one number a reader
    # needs in order to judge a fit never left it. Two clear populations: a low background and a
    # high signal, overdispersed so the negative binomial is the right model.
    rng = np.random.default_rng(11)
    background = rng.negative_binomial(2, 2 / (2 + 3), size=900)
    signal = rng.negative_binomial(6, 6 / (6 + 90), size=300)
    counts = np.concatenate([background, signal])

    fit = fit_tag_probabilities(counts, min_cells=100)

    assert fit.probabilities is not None
    assert fit.background is not None
    # The background sits below the signal, which is what labelling the higher-median component
    # signal means. Bounds rather than point values: the fit is stochastic.
    assert fit.background.mean < fit.background.signal_mean
    assert 0.0 < fit.background.weight < 1.0
    # The background holds the larger share, since three quarters of the cells are background.
    assert fit.background.weight > 0.5


def test_a_fit_that_established_nothing_carries_no_background():
    # `background` is None on exactly the condition `probabilities` is. A caller must branch on
    # absence, and a background sitting beside a None probability would invite the other reading.
    fit = fit_tag_probabilities(np.zeros(500, dtype=int), min_cells=100)
    assert fit.probabilities is None
    assert fit.background is None


def test_backgrounds_are_collected_per_sample_and_tag():
    # A pair in `reasons` contributes no background, and a pair that fitted contributes exactly
    # one. The two dicts partition the declared pairs.
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
