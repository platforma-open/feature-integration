"""Rung 3's fit: does a tag's own distribution split, and where.

Every bed here is generated from a seeded generator rather than written out by
hand, because the thing under test is a density and a hand-written handful of
counts has no density to speak of. The seed is fixed, so the beds are the same
bytes on every run and on every machine.

The population sizes are the ones a real run carries: the manual bed's presets
are 2000 cells per donor, and a binder fraction of a few percent is what this
method exists to find. The published method it follows was built for exactly
that shape -- its own paper rejects a Gaussian mixture and k-means because both
degrade when the two populations are unequal.
"""

import numpy as np
import polars as pl
import pytest
from panel import ANY_SAMPLE
from tag_distribution import (
    DEFAULT_DISTRIBUTION_MIN_CELLS,
    NO_SEPARATION,
    TOO_FEW_CELLS,
    fit_tag_background,
    fit_tag_baselines,
)

SEED = 7


def _mixture(n_background, background_rate, n_binders, binder_rate, seed=SEED):
    """A background population and a binder population, in that order."""
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.poisson(background_rate, n_background), rng.poisson(binder_rate, n_binders)])


def test_the_cell_condition_is_the_shipped_one():
    # It comes from the study the method comes from, and it gates rather than
    # tunes. A change here is a change of method, not a setting.
    assert DEFAULT_DISTRIBUTION_MIN_CELLS == 300


def test_a_sample_below_the_cell_condition_gets_no_baseline():
    # 299 cells that would separate perfectly well. The condition is on the
    # population, never on how clean the answer looks.
    fit = fit_tag_background(_mixture(290, 2, 9, 300))
    assert fit.baseline is None
    assert fit.reason == TOO_FEW_CELLS


def test_the_cell_condition_counts_cells_not_readings():
    # 400 cells, most of which read nothing. Counting only the observed readings
    # would put this under the condition and lose a baseline the run can make --
    # and the cells that read nothing are most of the background, so dropping
    # them breaks the fit as well as the gate.
    counts = _mixture(380, 0.3, 20, 200)
    assert counts.size == 400
    assert int((counts > 0).sum()) < DEFAULT_DISTRIBUTION_MIN_CELLS
    assert fit_tag_background(counts).baseline is not None


def test_a_few_percent_binder_population_separates():
    # The customer case: 2000 cells, 3% of them binding. The fit must find the
    # background and must find all of it -- a background short by the binders is
    # the same estimate the panel rung already gives.
    fit = fit_tag_background(_mixture(1940, 2, 60, 300))
    assert fit.reason is None
    assert fit.background_cells == 1940
    assert fit.baseline == 2


def test_a_one_percent_binder_population_still_separates():
    # Where a Gaussian mixture degrades. 20 binders in 2000 cells.
    fit = fit_tag_background(_mixture(1980, 1, 20, 200))
    assert fit.reason is None
    assert fit.background_cells == 1980


def test_a_mostly_silent_background_separates():
    # The ordinary shape on a real panel: most cells read nothing at all for a
    # given tag, and the baseline is honestly zero.
    fit = fit_tag_background(_mixture(1900, 0.4, 100, 150))
    assert fit.reason is None
    assert fit.background_cells == 1900
    assert fit.baseline == 0


def test_an_even_split_separates():
    # Not the shape this rung is for, but it must not be the shape that breaks
    # it: the two populations are equal and the trough is deepest of all.
    fit = fit_tag_background(_mixture(1000, 2, 1000, 400))
    assert fit.reason is None
    assert fit.background_cells == 1000


def test_a_tag_nothing_bound_does_not_separate():
    # THE false positive this rung must not produce. One population, and any
    # split invented inside it puts a comparator on a line nothing drew -- and
    # it would be a LOW comparator, which moves every verdict toward *bound*.
    rng = np.random.default_rng(SEED)
    for rate in (0.4, 2.0):
        fit = fit_tag_background(rng.poisson(rate, 2000))
        assert fit.baseline is None, f"invented a split in a single population at rate {rate}"
        assert fit.reason == NO_SEPARATION


def test_integer_counts_do_not_split_on_their_own_teeth():
    # The failure the bandwidth floor exists for, pinned directly. log2(n+1) of
    # small integers lands on a comb -- 0, 1, 1.58, 2 -- and an unfloored
    # bandwidth resolves the gap between a count of nothing and a count of one
    # as two populations. Every count here is 0, 1 or 2 and there is nothing to
    # separate.
    rng = np.random.default_rng(SEED)
    fit = fit_tag_background(rng.integers(0, 3, 2000))
    assert fit.baseline is None
    assert fit.reason == NO_SEPARATION


def test_a_tag_every_cell_read_identically_does_not_separate():
    # One value repeated has one population by construction. The density
    # estimator raises on it rather than returning that answer, so the guard is
    # load-bearing, not defensive.
    fit = fit_tag_background(np.zeros(2000, dtype=int))
    assert fit.baseline is None
    assert fit.reason == NO_SEPARATION


def test_two_populations_too_close_together_do_not_separate():
    # Binders at eight against a background at three. This is the conservative
    # direction and it is the intended one: the rung reports itself unavailable
    # rather than returning a baseline from a split it cannot demonstrate.
    fit = fit_tag_background(_mixture(1900, 3, 100, 8))
    assert fit.baseline is None
    assert fit.reason == NO_SEPARATION


def test_the_baseline_is_the_background_not_the_split_point():
    # The published use of the split is a threshold; nothing here thresholds.
    # The comparator is the middle of the background, which is far below the
    # split -- reporting the split instead would make every score harder to
    # clear than the method it came from intends.
    counts = _mixture(1940, 2, 60, 300)
    fit = fit_tag_background(counts)
    assert fit.split is not None
    assert fit.baseline < 2**fit.split - 1


def test_the_baseline_is_an_integer_count():
    # Every reading in the pipeline is an integer count of UMIs -- the minimum
    # acts on one, the panel rung truncates to keep one. A float comparator here
    # would be the only non-count in the block.
    fit = fit_tag_background(_mixture(1900, 5, 100, 400))
    assert isinstance(fit.baseline, int)


def test_the_fit_is_deterministic():
    # Two runs over the same counts return the same split to the bit. The grid
    # is fixed for this reason; a data-derived one would not be.
    counts = _mixture(1940, 2, 60, 300)
    assert fit_tag_background(counts) == fit_tag_background(counts)


def test_the_cell_condition_can_be_moved_and_the_separation_condition_cannot_be_bypassed():
    # The first is a number a run may change and says it changed. The second is
    # not a number at all -- lowering the cell condition does not make an
    # unseparated distribution separate.
    counts = np.concatenate([np.random.default_rng(SEED).poisson(2, 200)])
    assert fit_tag_background(counts, min_cells=100).reason == NO_SEPARATION
    assert fit_tag_background(counts, min_cells=300).reason == TOO_FEW_CELLS


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

    Returns the counts frame and the cell universe. Cells that read nothing are
    absent from the counts frame and present in the universe, which is the shape
    the block actually receives.
    """
    rng = np.random.default_rng(seed)
    background = rng.poisson(2, n_cells - n_binders)
    binders = rng.poisson(300, n_binders)
    values = np.concatenate([background, binders])
    cells = [(sample, f"c{i}") for i in range(n_cells)]
    rows = [(sample, f"c{i}", tag, int(v)) for i, v in enumerate(values) if v > 0]
    return _counts_frame(rows), cells


def test_the_silent_cells_are_in_the_fit():
    # The counts frame holds only observed readings. A fit taken over those
    # alone is a fit over the cells that read SOMETHING, which is not the
    # background -- and on a mostly-silent tag it is barely any of it.
    rng = np.random.default_rng(SEED)
    cells = [("S1", f"c{i}") for i in range(2000)]
    values = np.concatenate([rng.poisson(0.2, 1900), rng.poisson(200, 100)])
    rows = [("S1", f"c{i}", "AAAA", int(v)) for i, v in enumerate(values) if v > 0]
    counts = _counts_frame(rows)
    assert counts.height < 500, "the bed must be mostly silent or it proves nothing"

    fits = fit_tag_baselines(counts, cells, _panel([("AAAA", "S1")]))
    fit = fits[("S1", "AAAA")]
    assert fit.reason is None
    assert fit.background_cells == 1900


def test_a_declared_tag_the_reads_never_showed_gets_no_baseline():
    # Fitted over all zeros. One population, so no separation -- which is the
    # honest answer and is also the quality finding.
    counts, cells = _bed()
    fits = fit_tag_baselines(counts, cells, _panel([("AAAA", "S1"), ("DEAD", "S1")]))
    assert fits[("S1", "AAAA")].baseline is not None
    assert fits[("S1", "DEAD")].baseline is None
    assert fits[("S1", "DEAD")].reason == NO_SEPARATION


def test_every_sample_is_fitted_on_its_own_cells():
    # Baselines are local. Two samples staining the same tag get two fits, and
    # the one below the cell condition gets none -- the other is unaffected.
    big_counts, big_cells = _bed(n_cells=2000, sample="S1")
    small_counts, small_cells = _bed(n_cells=200, n_binders=6, sample="S2")
    counts = pl.concat([big_counts, small_counts])
    fits = fit_tag_baselines(counts, big_cells + small_cells, _panel([("AAAA", "S1"), ("AAAA", "S2")]))
    assert fits[("S1", "AAAA")].reason is None
    assert fits[("S2", "AAAA")].reason == TOO_FEW_CELLS


def test_a_panel_with_no_sample_column_is_fitted_per_sample_anyway():
    # ANY_SAMPLE declares the tag for every sample. The population a fit is
    # taken over is still one sample's cells, so there is still one fit each.
    big_counts, big_cells = _bed(n_cells=2000, sample="S1")
    small_counts, small_cells = _bed(n_cells=200, n_binders=6, sample="S2")
    counts = pl.concat([big_counts, small_counts])
    fits = fit_tag_baselines(counts, big_cells + small_cells, _panel([("AAAA", ANY_SAMPLE)]))
    assert set(fits) == {("S1", "AAAA"), ("S2", "AAAA")}
    assert fits[("S2", "AAAA")].reason == TOO_FEW_CELLS


def test_cells_outside_the_universe_do_not_enter_a_fit():
    # A barcode the analysis excluded carries real counts, and letting them into
    # the background makes the fit a fit over a population nobody chose.
    counts, cells = _bed()
    intruders = _counts_frame([("S1", "x1", "AAAA", 900), ("S1", "x2", "AAAA", 900)])
    fits = fit_tag_baselines(pl.concat([counts, intruders]), cells, _panel([("AAAA", "S1")]))
    assert fits[("S1", "AAAA")] == fit_tag_baselines(counts, cells, _panel([("AAAA", "S1")]))[("S1", "AAAA")]


def test_a_duplicated_cell_is_refused():
    # A duplicate adds one zero to the population it duplicates. That is a
    # background over a population nobody chose -- small, plausible, and
    # invisible in the output, which is why it raises rather than deduplicates.
    counts, cells = _bed()
    with pytest.raises(ValueError, match="duplicated cells"):
        fit_tag_baselines(counts, cells[:-1] + [cells[0]], _panel([("AAAA", "S1")]))


def test_a_tag_read_twice_in_one_cell_is_refused():
    # More readings than cells makes the zero padding negative, and numpy
    # returns an EMPTY array for that rather than raising -- so the fit would
    # quietly run over the observed readings alone and look like it worked.
    counts, cells = _bed()
    doubled = pl.concat([counts, counts.head(1)])
    with pytest.raises(ValueError, match="duplicated readings"):
        fit_tag_baselines(doubled, cells, _panel([("AAAA", "S1")]))


def test_the_bandwidth_follows_the_cell_count_not_the_distinct_values():
    # The density is fitted over distinct counts carrying frequencies, which is
    # forty times cheaper. Scipy would derive its bandwidth from the effective
    # weighted size -- a few dozen here -- and over-smooth by a factor of three,
    # which loses this split entirely. A mostly-silent tag has few distinct
    # values and many cells, so it is where the two diverge most.
    rng = np.random.default_rng(SEED)
    counts = np.concatenate([rng.poisson(0.4, 1900), rng.poisson(150, 100)])
    assert np.unique(counts).size < 60
    assert fit_tag_background(counts).reason is None
