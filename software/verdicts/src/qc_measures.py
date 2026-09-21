"""The quality measurements a run carries, and the figures the other quality surfaces are built from.

`MEASUREMENTS` is the declared set behind one surface: a SAMPLE's own report. Every entry carries
what it counts, because the reader who meets it is not the person who chose it. Where a line can be
defended, it also carries what a bad value implies. Where none can, it carries nothing -- the number
and its distribution are shown and the reader judges. None carries what to do about it, because
advice depends on the run, the study and what else is available.

A declared measurement keeps its place in that report whether or not the run could compute it, with
the reason in place of a number, so a reader never mistakes "nothing computed this" for "checked and
found fine". Most of the set is computed elsewhere and only declared here: the floor's counts and the
high-reference-cell count in ``verdict.py``, read and per-cell totals in ``qc_report.py``.

The per-TAG figures are NOT declared here. They belong to the reagent and undeclared-barcode tables,
which declare their own columns; the functions computing them live below (`per_antigen_measures`,
`sibling_disagreement`) alongside the binning the run-quality plots draw from.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

import numpy as np
import polars as pl
from verdict import SETTLED


# Three values and no fourth: a reader meeting five words in one column reads them as a scale.
# The two cases a fourth word covered are read from the VALUE instead -- a computed measurement
# shows its number, and one the run could not supply the inputs for shows the reason in place of
# one. So a measurement with no line behind it carries no status (`status_for` returns None) and
# the row is still there.
class Status(str, Enum):
    OK = "OK"
    WARN = "warn"
    ALERT = "alert"


class Line(NamedTuple):
    """Where a measurement's boundaries sit.

    Two thresholds, because all four inherited lines arrive with both and collapsing them loses a
    distinction somebody calibrated. The field's word *error* is kept for the second threshold
    while the status it produces is *alert*.

    `error` is None where only one boundary was published.
    """

    warn: float
    error: float | None = None


class Reading(NamedTuple):
    """How one measurement came back, as `roll_up` needs to count it.

    Both no-status cases return None from `status_for`, and the coverage triple still separates
    them, so the value has to travel with the status -- a number means computed-but-unjudged, its
    absence means nothing computed it.
    """

    status: Status | None
    value: float | None


@dataclass(frozen=True)
class Coverage:
    """A level's status, and how much of it was actually checked.

    `status` is None where nothing at this level carried one. A level with nothing judged makes no
    claim.
    """

    status: Status | None
    judged: int
    unjudged: int
    not_evaluated: int


@dataclass(frozen=True)
class Measurement:
    """What the run computes about a figure -- never how it is described.

    The words a reader sees live in the model, as `QC_MEASUREMENT_DESCRIPTIONS`, keyed on `id`.
    The id is the join, and it is stable: it is also a value on the `measurement`
    axis and a p-column name.
    """

    id: str
    # Which surface owns the figure. Every declaration is "sample" today: the tag-level figures moved to
    # the reagent and undeclared-barcode tables, which declare their own columns, and the run's score
    # spread moved to its plot. The field stays because it is what says a figure belongs to the sample's
    # own report rather than to one of those, and `sample_report_rows` reads it.
    level: str
    # Which defence route backs the measurement. Read here, not display: `_CATEGORICAL` is derived from
    # it, and the model's copy uses it for nothing.
    line: str | None = None


MEASUREMENTS: tuple[Measurement, ...] = (
    # --- Did the sequencing produce usable reads? Every share below is a share of READS, and they
    # follow the order the pipeline runs in: match the pattern, correct the cell barcode, correct the
    # antigen barcode against the panel.
    # `readsTotal` is NOT declared here. It is a denominator rather than a finding: the usable-read share
    # divides by it and now prints it beside its own numerator, and the Main page's Read recovery bar
    # splits it. A row of its own said only how big the file was. It still reaches the across-samples QC
    # table, where comparing depth between samples is the actual use.
    #
    # The SHARE, not the count. `readsMatched` is not declared: it rides in this row's detail as the
    # numerator, the way `usableReadFraction` carries its own two counts, so the quantity
    # `panelAssignedFraction` is a fraction OF still appears on the page without taking a row that says
    # only how many reads survived.
    #
    # Named rather than placed: the order here is the pipeline's, not this dependency's, so "the row
    # below" would go stale the next time the list is reordered.
    #
    # Borrowed from blocks/peptide-extraction, which ships this as `ParseMatchRate` at warn 0.8 / alert
    # 0.5 -- but the MEANING does not carry across and neither do the numbers. That block's pattern has
    # constant flanking regions, so a low rate there means the flanks mutated. This block's pattern is
    # all fixed-length N-runs: there is nothing in it to mutate, so a read either covers the geometry or
    # it does not, and the share is close to binary. Hence a much higher warn.
    #
    # This is the only check that catches a wrong read-geometry preset. Nothing else in the set does.
    Measurement(
        "matchedFraction",
        "sample",
        "operator-set",
    ),
    # The id is kept even though the label no longer says "valid". The id is a p-column name and a value
    # on the measurement axis, so renaming it would split the rows and leave a table holding old and new
    # runs reading as two measurements.
    #
    # What it measures, verified in mitool's `TagCorrector.kt`: refine-tags' CELL step, which with no
    # chemistry whitelist selectable runs DE-NOVO correction -- it clusters the barcodes the reads carry
    # and merges rare ones onto frequent neighbours rather than rejecting them against a declared list.
    # Reads are dropped there by BASE QUALITY (`minQuality`, 12 by default): a tag below it is secondary,
    # and if clustering does not absorb it the read goes. So the figure is a read-quality share, which is
    # why the label says quality and not validity.
    #
    # The line is OPERATOR-SET, and the route says so. The inherited pair (warn 0.75 / alert 0.50) was
    # published for validity against a whitelist and does not transfer to a quality filter, so it was not
    # reused; 0.95 / 0.75 are this block's own estimate, `implies` tells a reader that, and both are
    # movable from the form. Nothing here is calibrated against a corpus of runs.
    #
    # Two caveats for whoever does. `postFilter` drops (the 10x presets attach Otsu group filters) are NOT
    # in this ratio -- they land in the top-level record counts. And where correction is disabled for a
    # level, mitool sets inputCount and outputCount from one counter, so the ratio is hard-wired to 1.0.
    Measurement(
        "cellBarcodeValidFraction",
        "sample",
        "operator-set",
    ),
    # `qc_report._refine_step_counts` reads the FEATURE step's (inputCount, outputCount) -- the share of
    # matched reads whose barcode corrects onto a panel entry. Its complement is the share landing
    # in barcodes the panel never declared, which is a property of a barcode and not of a sample. The
    # line that used to sit here now backs the undeclared-barcode table's own row
    # (`undeclaredBarcodeShare` below), keyed by sequence and computed on the pre-refine counts. This
    # row keeps the number and carries no status.
    #
    # The usable row is a different quantity: Cell Ranger `main`, defines
    # `frac_feature_reads_usable` as conf-mapped, barcoded reads restricted to the called-cell
    # partition (`cell_bcs_union`), over the whole library's read count. UMI validity is that source's
    # separate `good_umi_frac` figure. It is declared below as `usableReadFraction`.
    #
    # The id is a value on the `measurement` axis and a p-column name in the per-sample QC frame.
    # Renaming it breaks both.
    Measurement(
        "panelAssignedFraction",
        "sample",
        # Over MATCHED READS, which every read share in this set shares. It was over the FEATURE step's
        # own input -- the reads that survived cell-barcode correction before it -- a denominator no
        # other figure here had, and subtracting two such shares is what produced the unit error in
        # `rescued_share`. One denominator for every read share removes that class of error.
        "inherited",
    ),
    # No line. The four inherited numbers do not include this one, and nothing published says what a
    # low or high rescued share means -- a panel whose barcodes sit far apart rescues little because
    # little needs rescuing, and one whose barcodes sit close rescues more. The number is here to be
    # read against the undeclared-barcode table, whose rows are the PRE-refine pass: a row of that
    # table is not a read the run lost, and this says how much of it was not.
    #
    # The allowance is mitool's `refine-tags` preset default, verified in
    # mitool/src/main/kotlin/com/milaboratory/mitool/refinement/TagCorrector.kt (`TagCorrectorParameters
    # .Default`: maxSubstitutions 2, maxIndels 2, maxTotalErrors 3) and in mitool_presets.yaml. This
    # block passes no override. `maxErrorsFor()` in the same file pulls the effective limit BELOW those
    # caps for a barcode with few reads or poor quality, which is why the text says "a tighter limit".
    #
    # Note what the whitelist is NOT: refine-tags clusters over the sequences actually observed and only
    # then filters the substitution table to panel entries, so a declared barcode absent from the reads
    # can never be a correction target. Nothing is rescued ONTO a barcode no read carried.
    Measurement(
        "refineRescuedShare",
        "sample",
        "operator-set",
    ),
    # --- What did the run see? Every figure below is over EVERY OBSERVED cell barcode, ambient
    # droplets included, before the V(D)J data has narrowed anything.
    # The categorical route's member. That route is kept for an alerting
    # condition that is a fact rather than a quantity, and says the route stays because the next
    # measurement may need it. This is that measurement: no cell barcode observed at all is a fact, and
    # a sample that detects none produced nothing for anything downstream to read.
    #
    # Computed in `qc_report.py` as the count of distinct cell barcodes the tag-stat table carries,
    # before any cell-calling step. `implies` names only the zero case. Above zero nothing is claimed,
    # because how many cells a sample should yield depends on the experiment and no number for that is
    # published.
    Measurement(
        "cellsDetected",
        "sample",
        "categorical",
    ),
    # Ports Cell Ranger's `detect_outlier_umis_bcs`, which it calls for the ANTIGEN library
    # type while removing antibody and antigen aggregates.
    # (`detect_aggregate_barcodes`, cross-feature co-elevation against gene expression) is a different
    # rule and is not ported.
    #
    # The source's own floor -- a threshold under 1000 UMIs flags nothing -- is kept unchanged. A
    # shallow library can sit entirely under that floor while carrying real aggregate reads, in which
    # case this reports 0.0 with the computed threshold in its detail: the rule ran and found nothing
    # past its own gate.
    #
    # Divided by `readsTotal` (whole-library, pre-match), not `readsMatched`, matching the source.
    Measurement(
        "aggregateBarcodeFraction",
        "sample",
        "inherited",
    ),
    # --- What reached the analysis? Every figure below is over the V(D)J-MATCHED cells alone.
    #
    # The same sum over every OBSERVED cell barcode was declared here too, as the other half of a pair:
    # one population either side of this seam, and the gap between them was how much signal sat in
    # droplets that held no recovered receptor. The observed half is gone. It could not be judged -- its
    # floor is 1, since the population is barcodes holding at least one counted reading, so there was no
    # bad value for a line to name -- and a row a reader cannot act on, whose only use was a comparison
    # they had to make in their head, was answering no question. The gap it measured is no longer
    # reported anywhere.
    Measurement(
        "uniqueCountsPerCell",
        "sample",
        "operator-set",
    ),
    # Ported from Cell Ranger's own read-recovery metric,
    # `_report_genome_agnostic_metrics::frac_feature_reads_usable`: conf-mapped, barcoded reads
    # restricted to the called-cell partition, over the whole library's read count.
    # `usable_read_fraction` sums the post-refine tag-stat's `totalWeight` over rows whose cell barcode
    # is in the cell list, divided by readsTotal. Every row of that table already carries a
    # panel-recognised FEATURE value, so restricting to the cell list is the only condition left.
    Measurement(
        "usableReadFraction",
        "sample",
        # BOTH ends, because the label names neither and the numerator's condition invites the wrong
        # denominator: a reader who meets "usable for antigen calls" beside a V(D)J condition reads it as
        # a share of V(D)J-matched CELLS. It is a share of READS -- every read parsed.
        "inherited",
    ),
    # Saturation is deliberately NOT measured. The vendor's own report carries it, and a scientist
    # cannot act on it for the run already collected; whether the run was deep enough is answered by
    # reads per cell below.
    #
    # Per *cell*, not per observed barcode. The vendor's five thousand is per-cell, and in droplet data
    # the observed-barcode count exceeds the called-cell count by one to two orders of magnitude, so
    # dividing by it would alert on a healthy library. The cell list arrives later than this module,
    # which is why the division happens in the entrypoint.
    Measurement(
        "readsPerCell",
        "sample",
        # Two sentences and nothing else. What the 5,000 is and where it comes from lives on the control
        # that moves it, under More options -> Quality lines, which is where a reader who wants to argue
        # with a number goes. Restating it here made the longest text on the page out of the row with the
        # simplest meaning.
        "recommended-and-observed",
    ),
    # --- What the reading rules then removed, and the numbers behind them. The minimum count acts on
    # readings; the admissibility gate acts on cells, and the control-tag median above it is the number
    # a gate is chosen from.
    Measurement(
        "floorRemoved",
        "sample",
        # Counted in READINGS -- one cell with five antigens under the floor contributes five. The cell
        # count beside it is a different unit, which is why it is a line of its own rather than a second
        # number on this one.
    ),
    Measurement(
        "medianControlReading",
        # "V(D)J-matched" hyphenated as the compound adjective it is, and singular after "per", matching
        # the two median rows above. The label carries the population, so the text does not repeat it.
        "sample",
    ),
    # TWO rows, not one with an "or" in its label. These were one measurement whose value was a count of
    # cells where a gate was declared and a median UMI count where none was, so its units changed with a
    # setting and only the folded detail said which. A label is a declaration; it cannot describe two
    # quantities.
    #
    # Both read the control tag -- the reagent the panel marks as binding nothing -- and both are taken
    # over the V(D)J-matched cells alone, as every other per-cell figure on this page is. Neither carries
    # a line: nothing published says what share of cells is too sticky, nor what a control reading of any
    # size means.
    Measurement(
        "cellsSetAside",
        "sample",
    ),
    # --- What the cutoff asked of each sample, and what it returned. The score itself is one currency
    # across a run -- every cell is scored against its OWN baseline, so the cutoff asks the identical
    # question everywhere and its spread is one pooled plot. What differs between samples is DEPTH, and
    # depth decides how many cells can reach the line at all.
    Measurement(
        "medianAntigenReading",
        "sample",
    ),
    Measurement(
        "cutoffCountNeeded",
        "sample",
    ),
    Measurement(
        "boundReadingShare",
        "sample",
    ),
    # The per-TAG figures are deliberately NOT declared here. Each has a purpose-built surface that
    # declares its own columns: cells-with-a-count, the median count, cells called bound and the two
    # disagreement rates are the reagent table's (`qc_rows._REAGENT_SCHEMA`, computed by
    # `per_antigen_measures` and `sibling_disagreement` here and `combine.self_disagreement`); the
    # undeclared sequences are the undeclared-barcode table's; the fitted background is the
    # run-quality page's per-(sample, tag) grid, drawn from the binned counts. Declaring them a second
    # time as measurements put the same numbers on one page twice under two sets of words.
    #
    # The run's score spread is not declared either. It is one number for the run, and the plot a
    # scientist moves the cutoff against draws the binned spread rather than eleven decile points.
    #
    # Self-disagreement at an IDENTITY is deliberately not measured. Marginal binding inflates
    # disagreement everywhere, so comparing one tag against its siblings under the same cells, run and
    # line leaves a tag that stands clear standing clear for a reason that is not biology. The
    # identity-level figure has nothing to compare against: cells of one clonotype all agree only where
    # the reading sits clear of the line, so for anything marginal disagreement is near certain and the
    # rate measures how many clonotypes sit near it.
    #
    # Whether a clonotype of known specificity came back correctly is deliberately NOT measured. It
    # would be the only end-to-end check of the pipeline, and nothing computes it because no surface
    # asks a scientist which clonotype they already know the answer for.
    #
    # Doublets, read from cells positive on several antigens, are deliberately NOT measured. The field
    # does not read multi-antigen positivity as a doublet estimate, and one vendor states outright that
    # it should not be.
    #
    # A false-discovery rate is deliberately NOT measured. None exists for this assay.
    #
    # The share of counts landing in droplets that held no cell is deliberately NOT measured. It cannot
    # be computed against a cell list derived from recovered receptors, because cells whose receptor
    # did not assemble are classified as empty and inflate the very quantity being measured.
)

# `Measurement.line` names where a line comes from, and it is the *only* declaration of which
# measurements carry one -- the tables below are derived facts about the route. Four routes exist and no
# fifth: "inherited", "categorical", "recommended-and-observed" and "operator-set".
#
# "operator-set" is the honest label for a line WE chose, with nothing published behind it. It earns its
# place because the alternative was worse in both directions: calling such a line "inherited" claims a
# provenance it does not have, and refusing it leaves a real failure mode unflagged. A measurement on
# this route must say in its `implies` that the number is an estimate, and its threshold must be
# operator-movable -- an uncalibrated line nobody can move is the one a reader cannot argue with.
#
# A comparison against the other tags in a panel is NOT a line. It yields no boundary, and a status
# derived from it would need a multiplier nobody has published. Such a measurement reads unjudged
# beside its siblings. The cost is real and accepted: a barcoded reagent binding something other
# than the receptor no longer announces itself.
#
# The categorical route's member ids. Derived from `Measurement.line`, never a second declaration.
# It has one member, `cellsDetected`. A categorical fact carries no numeric threshold, so its id is
# deliberately absent from `DEFAULT_LINES` and `_COMPARISON` -- `status_for` answers it before either
# table is consulted.
_CATEGORICAL: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.line == "categorical")

# Every line is a parameter with a shipped default, and the operator may override any of them. No
# line is invented -- where none of the four routes applies the measurement carries no status.
#
# `undeclaredBarcodeShare` backs the undeclared-barcode table's own row rather than a declared
# `Measurement`: that status is the barcode's, never a sample's, so it is computed and carried where
# the barcode rows are, in emit_verdicts.py, and reaches `status_for` under this id. It is the one
# exception to "every line backs a declared measurement", and a test names it.
#
# It reads the ROW's own share, `barcodeShare`, not the sample-level `readShare` the id is named for.
# The sample-level share keeps its column and carries no status.
DEFAULT_LINES: dict[str, Line] = {
    # One published number gives one boundary, so depth warns and never alerts.
    "readsPerCell": Line(warn=5_000),
    # Published values for the aggregate-barcode read fraction: warn above 0.05, error at total
    # failure (1.0).
    "aggregateBarcodeFraction": Line(warn=0.05, error=1.0),
    # Reads whose antigen barcode is on the panel: warn below 0.50, alert at total failure (0.0).
    # INHERITED, as the complement of Cell Ranger's `ANTIGEN_unrecognized_feature_bc_frac`, which the
    # Antigen Capture library publishes at warn 0.50 / error 1.0
    # (cellranger-10.1.0, lib/rust/cr_websummary/src/multi/metrics_etl.toml, [antigen_physical_library_metrics]).
    # Pinned to that file and tag deliberately: the LEGACY count webshim
    # (lib/python/cellranger/webshim/constants/gex.py, METRIC_ALARMS) publishes a different number for
    # the ANTIBODY equivalent, and a future reader must not "fix" this against that table.
    #
    # This block carried the same 0.50 once. It was moved onto the undeclared-barcode table, correctly
    # rejected there as non-transferable -- an aggregate line does not apply to one sequence -- and never
    # moved back to the aggregate quantity it belongs to. This is that quantity.
    #
    # The line is LAX against a healthy run, which sits near 1.00. It is kept as published rather than
    # tightened, because an inherited line a reader can trace beats a tighter one this block invented.
    "panelAssignedFraction": Line(warn=0.50, error=0.0),
    # Reads matching the read pattern: warn below 0.90, alert below 0.50. OPERATOR-SET. The share is
    # close to binary on an all-N pattern -- a read covers the geometry or it does not -- so a healthy
    # run sits near 1.00 and 0.90 is already a signal. 0.50 is half the library unusable.
    "matchedFraction": Line(warn=0.90, error=0.50),
    # Reads whose cell barcode survived correction: warn below 0.95, alert below 0.75. OPERATOR-SET, and
    # the route says so. The drop mechanism is base quality -- mitool's `minQuality`, 12 by default -- so
    # a healthy run sits near 1.00 and 0.95 is meant to catch a real slide rather than ordinary variation.
    # 0.75 is the far end: a quarter of reads lost before per-cell counting is a failed library whatever
    # the chemistry. Neither number is calibrated against a corpus of runs; check them against your own
    # before trusting the amber.
    "cellBarcodeValidFraction": Line(warn=0.95, error=0.75),
    # ONE BARCODE's share of its sample's pre-refine reads: warn above 0.01, alert above 0.05.
    # Operator-set, not inherited. The field publishes 0.50/1.0 for a sample's AGGREGATE undeclared
    # share, and that line does not transfer to a single sequence: the aggregate reaches 0.50 while no
    # single sequence comes near it. Needs an atom on 315 before it can be called inherited.
    "undeclaredBarcodeShare": Line(warn=0.01, error=0.05),
    # Published values for the usable antigen-read fraction: warn below 0.20, error at total
    # failure (0.0).
    "usableReadFraction": Line(warn=0.20, error=0.0),
    # Reads a correction snapped onto a panel barcode: warn above 0.05, alert above 0.10. OPERATOR-SET,
    # and the route says so. Both ends face the same way, because this line has a gradient and no
    # catastrophe value to alert at -- 1.0 cannot occur, since a rescued read is one the pattern already
    # matched and the panel already assigned. The quantity is two-sided (`implies` says so) and these
    # numbers pick the side that costs the run something: a tenth of the matched library placed by
    # inference is a panel whose barcodes sit close enough together to be confused for each other.
    "refineRescuedShare": Line(warn=0.05, error=0.10),
    # The typical analysed cell's antigen total: warn below 4, alert at 1. OPERATOR-SET, and the route
    # says so. The warn is `verdict.DEFAULT_FLOOR`, the count a single reading needs to survive
    # flooring -- restated as a literal rather than imported, since an operator moving one must not move
    # the other -- so a median below it means most of the typical cell's readings are zeroed before any
    # call. The error ALERTS AT 1 rather than below it: the population is barcodes holding at least one
    # counted reading, so 1 is the floor of this quantity and no value below it exists to alert on.
    "uniqueCountsPerCell": Line(warn=4, error=1),
}

# How each line's thresholds are read. Deliberately *not* in DEFAULT_LINES: an operator moves a
# number, never a direction.
#
#   at-least    OK at or above the threshold, bad strictly below
#   at-most     OK at or below the threshold, bad strictly above
#   alerting-at bad where the value equals the threshold
#
# In every case the named value satisfies the condition it names. The second entry is None where
# the line published no error threshold.
#
# The two thresholds of one line are read INDEPENDENTLY, and `undeclaredBarcodeShare` is why. Every
# inherited line here warns on a direction and puts error at total failure -- "at 0", "at 1.0" -- which
# is alerting AT failure rather than a further step along the warn direction. That one alerts ABOVE its
# error threshold instead. One direction per measurement collapsed the two cases into one, and a
# fraction whose error sits "at 0" could then never alert.
_COMPARISON: dict[str, tuple[str, str | None]] = {
    # Both ends face the same way on these two: each line has a real gradient rather than an error
    # sitting at total failure, so a run can slide from OK through warn into alert on one direction.
    # Error at total failure (alerting at 0.0): no read on the panel at all. The published pair puts its
    # error at the catastrophe end rather than a further step past warn, which inverts to this.
    "panelAssignedFraction": ("at-least", "alerting-at"),
    "matchedFraction": ("at-least", "at-least"),
    "cellBarcodeValidFraction": ("at-least", "at-least"),
    "readsPerCell": ("at-least", None),
    # Error at total failure (alerting at 1.0) rather than a further step past warn: every
    # inherited share sits at either "at least" or "at most" with error at the catastrophe end, and
    # this is one of the two upward-facing members of that set.
    "aggregateBarcodeFraction": ("at-most", "alerting-at"),
    # Both ends face the same way, unlike the inherited shares: this line alerts ABOVE its error
    # threshold rather than at a catastrophe value, so alerting at failure would fire only at 0.05.
    "undeclaredBarcodeShare": ("at-most", "at-most"),
    # Error at total failure (alerting at 0.0), the downward-facing member of that same set.
    "usableReadFraction": ("at-least", "alerting-at"),
    # Both ends face the same way, like `undeclaredBarcodeShare` and for the same reason: the line has a
    # gradient rather than an error sitting at a catastrophe value, so alerting AT the error threshold
    # would fire only at exactly 0.10.
    "refineRescuedShare": ("at-most", "at-most"),
    # Alerting AT 1 -- the floor of the quantity rather than a step past warn. Every barcode in the
    # population holds at least one counted reading, so no median below 1 can be produced and an
    # "at-least 1" error could never fire.
    "uniqueCountsPerCell": ("at-least", "alerting-at"),
}


def _breaches(value: float, threshold: float, comparison: str) -> bool:
    """Whether a value falls the wrong side of one threshold."""
    if comparison == "at-least":
        return value < threshold
    if comparison == "at-most":
        return value > threshold
    return value == threshold


def _breaches_expr(value: pl.Expr, threshold: float, comparison: str) -> pl.Expr:
    """`_breaches` over a column. Every branch mirrors the scalar above, line for line.

    `test_status_expr_agrees_with_status_for` runs the two against one another over the boundary
    values and every registered measurement, so a branch changed on one side alone fails there.
    """
    if comparison == "at-least":
        return value < threshold
    if comparison == "at-most":
        return value > threshold
    return value == threshold


_ORDINAL = {Status.OK: 0, Status.WARN: 1, Status.ALERT: 2}


def is_computed(value: float | None) -> bool:
    """Whether a number came back at all.

    A non-finite value counts as absent. Every `<` and `>` against NaN is False, so treating it as a
    number let it fall through to the acceptable branch -- corrupt input reading green, the one
    status a reader will not investigate. +inf read green too against an at-least line, and -inf
    happened to alert.
    """
    return value is not None and math.isfinite(value)


def status_for(measurement: str, value: float | None, lines: dict[str, Line]) -> Status | None:
    """How one measurement reads, given the lines in force. None where no line stands behind it.

    Three answers and no fourth. A measurement with no number and a measurement with no line both
    carry no status -- and which of the two happened is read from the value.

    The categorical route is read before `lines`: its fact is not a threshold, so neither
    `DEFAULT_LINES` nor `_COMPARISON` carries an entry for it. Zero alerts; any other finite value
    reads OK and claims nothing about how many cells the sample should have yielded.
    """
    if not is_computed(value):
        return None
    if measurement in _CATEGORICAL:
        return Status.ALERT if value == 0 else Status.OK
    if measurement not in lines:
        return None
    line = lines[measurement]
    warn_comparison, error_comparison = _COMPARISON[measurement]
    # Error first, so a value past both boundaries reads alert rather than warn. Where the line
    # published no error threshold the measurement warns and never alerts, whatever its value.
    if line.error is not None and error_comparison is not None and _breaches(value, line.error, error_comparison):
        return Status.ALERT
    if _breaches(value, line.warn, warn_comparison):
        return Status.WARN
    return Status.OK


def status_expr(measurement: str, value: pl.Expr, lines: dict[str, Line]) -> pl.Expr:
    """`status_for` over a column, returning the status string or null.

    For a per-row status on a frame with no bound on its height. The undeclared-barcode table is the
    caller: its row cap is a parameter that accepts `None`, so a Python loop there is a loop over every
    distinct pre-refine sequence -- 10.2M per sample on a measured 44-sample run -- and materialising
    the column to drive it undoes the memory work this stage carries.

    Thresholds and directions are read from the SAME `lines` and `_COMPARISON` this module's scalar
    reads. Only the evaluator differs, and a test pins the two together.
    """
    null = pl.lit(None, pl.String)
    # `is_computed` over a column: a null, a NaN or an infinity is not a number. Kleene `&` makes a
    # null value read False here rather than propagating a null into the branch below.
    computed = (value.is_not_null() & value.is_finite()).fill_null(False)  # noqa: FBT003
    if measurement in _CATEGORICAL:
        return (
            pl.when(~computed)
            .then(null)
            .when(value == 0)
            .then(pl.lit(Status.ALERT.value, pl.String))
            .otherwise(pl.lit(Status.OK.value, pl.String))
        )
    if measurement not in lines:
        return null
    line = lines[measurement]
    warn_comparison, error_comparison = _COMPARISON[measurement]
    alerts = (
        _breaches_expr(value, line.error, error_comparison)
        if line.error is not None and error_comparison is not None
        else pl.lit(False)  # noqa: FBT003
    )
    # Error first, exactly as the scalar orders it: a value past both boundaries reads alert.
    return (
        pl.when(~computed)
        .then(null)
        .when(computed & alerts)
        .then(pl.lit(Status.ALERT.value, pl.String))
        .when(computed & _breaches_expr(value, line.warn, warn_comparison))
        .then(pl.lit(Status.WARN.value, pl.String))
        .otherwise(pl.lit(Status.OK.value, pl.String))
    )


def roll_up(readings: list[Reading]) -> Coverage:
    """The worst status among those that carry one, plus coverage.

    Coverage stays out of the ordinal because a status and a non-status answer different questions.
    The first says whether something is wrong, the second whether anybody looked.

    A level with nothing judged carries no status. Given one of OK the run would look checked, and
    given one of alert a scientist would chase a problem that does not exist.
    """
    judged = [r.status for r in readings if r.status is not None]
    unjudged = sum(1 for r in readings if r.status is None and is_computed(r.value))
    not_evaluated = sum(1 for r in readings if r.status is None and not is_computed(r.value))
    status = max(judged, key=lambda s: _ORDINAL[s]) if judged else None
    return Coverage(status, len(judged), unjudged, not_evaluated)


# Only the sample rolls up, so `roll_up` above is the only aggregation rule here.
#
# A panel status is gone because it overestimated what could be judged categorically: of the
# per-tag measurements one is categorical and the rest are read only as outliers against the other
# tags in the same panel, which is a comparison rather than a severity. A capture status followed
# the same logic -- the worst of every sample and every panel becomes the worst of every sample.
#
# Nothing hides. A reagent finding states itself on its own per-tag row, keyed by the panel that
# has it, and a sample's own report names the measurement that set it alerting.


def per_antigen_measures(
    counts: pl.DataFrame,
    states: pl.DataFrame,
    declared_tags: Collection[str],
    panel_samples: Collection[str],
    reference_tags: Collection[str] = (),
) -> pl.DataFrame:
    """Per tag: cells with any count, cells called bound, and the median count per cell.

    Grouped by tag, not identity: a tag's own reagent behaviour is the question, and an identity
    built from several tags would let one weak tag hide behind a stronger one.

    Two frames, and which one each column comes from is the whole point. `counts` is the RAW sparse
    frame, before the minimum -- one row per (sampleId, cellId, tag) with `umiCount`, reference tags
    included. `states` is the tag-grain frame after the minimum, with `tag` and `state`.

    `cellsWithCount` and `medianCountPerCell` come from `counts`, `cellsAboveTheLine` from `states`:
    the first measures what the reagent delivered and the second what survived the minimum, so a
    reagent putting two counts into every cell reads as delivering something. A median below the
    minimum is that same finding and not an error.

    The median is taken over every cell holding a count. Taken over bound cells it could only ever
    print a number above the cutoff's floor, so a half-degraded reagent would show a healthy figure
    computed from the few cells that scraped over. It also then depends on no threshold, which
    matters on a first run where the cutoff is still being settled.

    One row per declared tag, whether or not the reads ever show it. A dead reagent is read as a zero
    under cells-with-count, and a tag with no row at all offers nothing to read.

    `samplesSeenIn` counts distinct samples carrying any count of the tag, and `samplesInPanel` is
    the denominator. `panel_samples` is the panel's declared roster, not the samples present in
    `counts`, which omits a sample that contributed no rows.

    `samplesInPanelNames` and `samplesSeenInNames` carry the same two groups as sorted sample ids.
    `samplesSeenInNames` is `[]`, never null, for a tag with `samplesSeenIn == 0`.

    Reference tags keep a row and carry `cellsAboveTheLine` as None. They are held out of the verdict
    read, so no state exists for them, and a blank and a zero are opposite findings here. Their
    median is the run's ambient floor.
    """
    references = sorted(set(reference_tags))
    spine = pl.DataFrame(
        {"tag": sorted(set(declared_tags) | set(references))},
        schema={"tag": pl.Utf8},
    )

    delivered = (
        counts.filter(pl.col("umiCount") > 0)
        .group_by("tag")
        .agg(
            pl.len().alias("cellsWithCount"),
            pl.col("umiCount").median().alias("medianCountPerCell"),
            pl.col("sampleId").n_unique().alias("samplesSeenIn"),
            pl.col("sampleId").unique().sort().alias("samplesSeenInNames"),
        )
    )
    declared_names = sorted(set(panel_samples))
    samples_in_panel = len(declared_names)
    bound = states.group_by("tag").agg((pl.col("state") == "bound").sum().alias("cellsAboveTheLine"))

    is_reference = pl.col("tag").is_in(references) if references else pl.lit(False)  # noqa: FBT003
    return (
        spine.join(delivered, on="tag", how="left")
        .join(bound, on="tag", how="left")
        .with_columns(
            pl.col("cellsWithCount").fill_null(0).cast(pl.Int64),
            pl.col("samplesSeenIn").fill_null(0).cast(pl.Int64),
            pl.col("samplesSeenInNames").fill_null([]),
            pl.lit(samples_in_panel, dtype=pl.Int64).alias("samplesInPanel"),
            pl.lit(declared_names, dtype=pl.List(pl.Utf8)).alias("samplesInPanelNames"),
            pl.when(is_reference)
            .then(pl.lit(None, dtype=pl.Int64))
            .otherwise(pl.col("cellsAboveTheLine").fill_null(0).cast(pl.Int64))
            .alias("cellsAboveTheLine"),
        )
        .sort("tag")
    )


def sibling_disagreement(
    states: pl.DataFrame,
    tags_by_identity: dict[str, list[str]],
) -> dict[str, float | None]:
    """Per tag: the share of its judged cells contradicting the majority of its siblings.

    Siblings are the OTHER tags the same identity carries, and a tag is excluded from the majority
    it is judged against.

    Judged within one cell, over the siblings holding an explicit row there. A tag with no row in a
    cell does not vote: `states` is sparse and carries no silent cell.

    A majority is strict -- more than half the sibling votes. A cell whose siblings reach no strict
    majority does not judge that tag and is not counted. Two siblings need at least three tags on
    the identity, so a two-tag identity always has a majority of one.

    None, never zero, where nothing judged the tag. Three causes reach it and the caller tells them
    apart: an identity carrying one tag, a tag holding no row in any cell, and a tag whose siblings
    reached a majority in no cell it held.
    """
    rates: dict[str, float | None] = {}
    for tags in tags_by_identity.values():
        members = sorted(set(tags))
        if len(members) < 2:
            for tag in members:
                rates[tag] = None
            continue

        # SETTLED only. The rule is to put every tag of the identity in bound or not bound on its own
        # count, and a cell with no comparator or one a gate set aside is in neither. Counting
        # *unreliable* as a state lets it win a sibling majority, so a tag that fitted where its
        # siblings did not reads as differing from all of them -- the panel's worst reagent by the
        # measurement, and its only working one in fact.
        here = states.filter(pl.col("tag").is_in(members) & pl.col("state").is_in(SETTLED)).select(
            "sampleId", "cellId", "tag", "state"
        )
        for tag in members:
            mine = here.filter(pl.col("tag") == tag).select("sampleId", "cellId", "state")
            counted = here.filter(pl.col("tag") != tag).group_by("sampleId", "cellId", "state").agg(pl.len().alias("n"))
            totals = counted.group_by("sampleId", "cellId").agg(pl.col("n").sum().alias("total"))
            # Filtered on the strict majority rather than on the largest count, so at most one state
            # survives per cell and no row ordering decides which. A cell with no sibling row, or with its
            # siblings tied, produces no row here and drops out.
            majority = (
                counted.join(totals, on=["sampleId", "cellId"], how="inner")
                .filter(pl.col("n") * 2 > pl.col("total"))
                .select("sampleId", "cellId", pl.col("state").alias("majority"))
            )
            judged = mine.join(majority, on=["sampleId", "cellId"], how="inner")
            if judged.height == 0:
                rates[tag] = None
                continue
            rates[tag] = judged.filter(pl.col("state") != pl.col("majority")).height / judged.height
    return rates


def reads_per_cell(usable: float, cells_in_list: int) -> float | None:
    """Usable reads over the cells they landed in -- the mean antigen depth of one analysed cell.

    ONE POPULATION, top and bottom: `usable` counts the reads inside the listed cells (see
    `usable_reads`), and `cells_in_list` counts those same cells. That is what makes this a mean.

    It used to divide EVERY matched read in the library by the listed-cell count, which is Cell
    Ranger's `Mean Reads per Cell` with its denominator swapped for an intersection with an external
    dataset. The two then moved independently, and the figure tracked the V(D)J match rate rather than
    this library's depth: holding one antigen library fixed at 2,500 reads in each of 8,000 barcodes,
    it read 40,000 against 500 matched cells and 2,500 against 8,000 -- a sixteen-fold swing, warning
    only where V(D)J recovery was GOOD. Scoping the numerator to the same cells removes the class.

    The vendor's number is not computable here at all: it divides the whole library by the cells that
    library called, and this block calls no cells -- the list arrives with the receptors. So this runs
    LOWER than the vendor's equivalent, by the ambient and off-panel reads it excludes, and the
    5,000 line inherited from it errs toward firing early.

    None when the cell list is empty. A rate over no cells is no number, which is distinct from a
    computed rate that happens to be zero.
    """
    if cells_in_list <= 0:
        return None
    return usable / cells_in_list


def usable_reads(tag_stat: pl.DataFrame, cell_col: str, listed_cells: Collection[str]) -> float:
    """Read weight landing on a listed cell with a panel-recognised antigen barcode.

    The numerator BOTH `usable_read_fraction` and `reads_per_cell` are taken over, computed once here
    so the share and the depth cannot describe different sets of reads. `tag_stat` is the post-refine
    table -- every FEATURE value outside the panel is already gone, which is the recognition condition.

    0.0 for an empty cell list: no read landing on a listed cell is a finding, not a missing input.
    """
    return float(tag_stat.filter(pl.col(cell_col).is_in(list(listed_cells)))["totalWeight"].sum())


def usable_read_fraction(
    tag_stat: pl.DataFrame,
    cell_col: str,
    listed_cells: Collection[str] | None,
    reads_total: int | None,
) -> tuple[float | None, str]:
    """Reads landing on a called cell, recognised against the panel, over `reads_total`.

    Ports Cell Ranger's `frac_feature_reads_usable` (Cell Ranger `main`,
    `_report_genome_agnostic_metrics`): conf-mapped,
    barcoded reads restricted to the called-cell partition, over the whole library's read count. UMI
    validity is that source's separate `good_umi_frac` figure and is not part of this one.

    `tag_stat` is the post-refine tag-stat table, one row per (cell, feature barcode) surviving
    refine-tags -- every FEATURE value outside the panel is already gone, which is the recognition
    condition. `totalWeight` is its read-weight column. `listed_cells` is the sample's own cell list.

    Returns `(None, reason)` where `listed_cells` is None, or where `reads_total` is absent or zero.
    An empty (non-None) cell list still returns 0.0: no read landing on a called cell is a real
    finding, not a missing input.

    The second element is the DETAIL where a number came back and the REASON where none did. On success
    it is the fraction written out as its two counts, because a share alone does not say how much signal
    it is a share of -- and the denominator is `readsTotal`, which no longer takes a row of its own.
    """
    if listed_cells is None:
        return None, "no cell list supplied, so the called-cell condition cannot be evaluated"
    if not reads_total:
        return None, "no total read count to divide by"
    usable = usable_reads(tag_stat, cell_col, listed_cells)
    return usable / reads_total, f"{int(usable):,} out of {int(reads_total):,} reads parsed"


# Cell Ranger's own constants for the ANTIGEN branch of `detect_outlier_umis_bcs`
# a 3x interquartile multiplier over the top
# 100 barcodes by count, and a 1000-UMI floor below which nothing is flagged.
AGGREGATE_BARCODE_IQR_MULTIPLIER: float = 3.0
AGGREGATE_BARCODE_MIN_THRESHOLD: float = 1000.0
AGGREGATE_BARCODE_TOP_N: int = 100


def detect_aggregate_barcodes(
    per_barcode: pl.DataFrame,
    multiplier: float = AGGREGATE_BARCODE_IQR_MULTIPLIER,
    min_umi_threshold: float = AGGREGATE_BARCODE_MIN_THRESHOLD,
    top_n: int = AGGREGATE_BARCODE_TOP_N,
) -> tuple[frozenset[str], float | None]:
    """Barcodes whose antigen UMI count is an outlier, and the threshold that decided it.

    Ports Cell Ranger's `detect_outlier_umis_bcs`, which it calls for the ANTIGEN library
    type while removing antibody and antigen aggregates.

    `per_barcode` has one row per observed barcode, columns `barcode` and `umiCount` -- the whole
    whitelist-corrected barcode universe, not the cell list. q1 and q3 are taken over the top `top_n`
    barcodes by count, and a flagged barcode must be IN that top slice: one outside it is never
    flagged however large its count, which is a property of the source.

    Returns an empty set and the computed threshold where the threshold falls under
    `min_umi_threshold`, the source's own floor. Returns an empty set and `None` where `per_barcode`
    holds no row at all.
    """
    if per_barcode.height == 0:
        return frozenset(), None
    top = per_barcode.sort("umiCount", descending=True).head(top_n)
    counts = top["umiCount"].to_numpy().astype(float)
    q1 = float(np.quantile(counts, 0.25))
    q3 = float(np.quantile(counts, 0.75))
    threshold = q3 + (q3 - q1) * multiplier
    if threshold < min_umi_threshold:
        return frozenset(), threshold
    flagged = top.filter(pl.col("umiCount") >= threshold)["barcode"].to_list()
    return frozenset(flagged), threshold


def aggregate_barcode_fraction(
    per_barcode: pl.DataFrame,
    reads_total: int | None,
    multiplier: float = AGGREGATE_BARCODE_IQR_MULTIPLIER,
    min_umi_threshold: float = AGGREGATE_BARCODE_MIN_THRESHOLD,
    top_n: int = AGGREGATE_BARCODE_TOP_N,
) -> tuple[float | None, str]:
    """Reads in barcodes `detect_aggregate_barcodes` flags, over `reads_total`.

    `per_barcode` carries `barcode`, `umiCount` (what detection runs on) and `readCount` (what the
    fraction's numerator sums) -- the source's ANTIGEN-branch numerator is reads, not UMIs.

    `reads_total` is the whole-library, pre-match read count (mitool's parse-report `total`),
    matching the source's undivided-by-matching denominator. Returns `(None, reason)` where
    `reads_total` is absent or zero.

    Otherwise always returns a number: where the floor in `detect_aggregate_barcodes` suppresses
    every flag, the fraction is 0.0 and the detail states the computed threshold and the floor.
    """
    if not reads_total:
        return None, "no total read count to divide by"
    flagged, threshold = detect_aggregate_barcodes(
        per_barcode.select("barcode", "umiCount"), multiplier, min_umi_threshold, top_n
    )
    tested = min(per_barcode.height, top_n)
    if threshold is None:
        detail = "no antigen barcode observed in this sample"
    elif threshold < min_umi_threshold:
        detail = (
            f"Barcodes tested: {tested:,}|Threshold: {threshold:,.0f} UMIs "
            f"(below the {min_umi_threshold:,.0f}-UMI floor, so no barcode is flagged)"
        )
    else:
        detail = f"Barcodes tested: {tested:,}|Threshold: {threshold:,.0f} UMIs|Barcodes flagged: {len(flagged):,}"
    flagged_reads = per_barcode.filter(pl.col("barcode").is_in(flagged))["readCount"].sum() if flagged else 0
    return flagged_reads / reads_total, detail


# How many buckets the count distributions used to be drawn in, back when their edges were integers.
# Kept only because `linear_bin_edges` uses it as its default; the count distributions do not.
COUNT_BIN_COUNT = 24


# The width of one bar, measured in log1p units. A fixed width is what makes every bar the same size.
#
# The source paper uses 0.075. This is deliberately coarser: at 0.075 a real tag came back with 75 of its
# 97 bins empty, because whole-number counts land on scattered points once you take the log. The paper
# lives with those gaps by drawing a smooth density curve over them, which these plots cannot do. 0.2
# keeps the bars equal and still readable at thumbnail size.
LOG1P_BIN_WIDTH = 0.2


def log1p_bin_edges(top: int, width: float = LOG1P_BIN_WIDTH) -> list[float]:
    """Bin edges spanning 0 to `top` that all draw the SAME WIDTH. `[]` if there is nothing to span.

    Returned as counts, at `expm1(k * width)`, because counts are what the plot takes. The plot's axis is
    log1p, so an edge at `expm1(k * width)` lands at `k * width` on screen -- evenly spaced.

    Edges start at 0 so the zeros the fit ran over get a bar of their own. Those zeros are most of the
    background; drop them and the plot shows one decaying hump whatever the fit found. The last edge is
    the first step above `top`, so the largest count has a bin to land in.

    ONE edge set for the whole run, not one per tag, so a reader can compare a grid of tags side by side.

    The cost: THE EDGES ARE NOT WHOLE NUMBERS. Counts are, so consecutive integers sit further apart than
    one bin until about count 13, and the low end comes out as separated bars with empty gaps between
    them. Whole-number edges avoided that, which is why they were used here before -- but they bought it
    with the per-bar division above. The source paper's figures show the same gaps.

    Nothing needs to be told the bin count: `bin_values`, `per_tag_count_bins` and the UI all read it
    from `len(edges) - 1`.
    """
    if top < 1 or width <= 0.0:
        return []
    return log1p_edges_for(int(np.floor(np.log1p(top) / width)) + 1, width)


def log1p_edges_for(steps: int, width: float = LOG1P_BIN_WIDTH) -> list[float]:
    """The first `steps` bins of the same grid, as counts. `[]` for a non-positive step or width.

    Separate from `log1p_bin_edges` because the edges are ABSOLUTE: they sit at `expm1(k * width)`
    whatever the data holds, so a bin count alone identifies them. Each (sample, tag) bins against its
    own range, and the plot then needs one edge list long enough for the widest of them -- which is a
    bin count, not a frame to re-scan.
    """
    if steps < 1 or width <= 0.0:
        return []
    return [float(np.expm1(k * width)) for k in range(steps + 1)]


def linear_bin_edges(values: np.ndarray, count: int = COUNT_BIN_COUNT) -> list[float]:
    """Evenly spaced bin edges spanning `values`. `[]` where there are none.

    Evenly spaced, unlike the count distributions' log1p edges. A specificity score is a 0-100 scale,
    and a reference reading is judged against a threshold the scientist types in the same units, so a
    log axis would put the number they are choosing somewhere they cannot find it.
    """
    if values.size == 0:
        return []
    low, high = float(np.min(values)), float(np.max(values))
    if high <= low:
        # Every observation identical. One bin wide enough to hold it, rather than a zero-width range
        # that renders as nothing.
        high = low + 1.0
    return [float(x) for x in np.linspace(low, high, count + 1)]


def bin_values(values: np.ndarray, edges: list[float]) -> list[int]:
    """How many observations fall in each bin, in edge order. `[]` where there is nothing to bin."""
    if values.size == 0 or len(edges) < 2:
        return []
    weights, _ = np.histogram(values, bins=np.asarray(edges, dtype=float))
    return [int(w) for w in weights]


def per_tag_count_bins(counts: pl.DataFrame, edges: list[float]) -> dict[str, dict[str, list[int]]]:
    """Per (sample, tag), how many cells hold each binned count. `{sampleId: {tag: weights}}`.

    The caller passes the counts of the CELL LIST where one arrived. A run with no list yields
    observed barcodes here, and its `cellListSource` says so.

    Taken from the RAW counts, before the minimum and with reference tags kept. This is a plot a
    scientist reads in order to SET the minimum, so binning after it would hide exactly the low
    counts the decision is about, and stripping the reference tag would hide the run's own ambient
    floor.

    One list of `len(edges) - 1` weights per tag, in edge order. A tag with no reading in a sample
    gets no entry: an absent tag and a tag whose cells all read zero are different findings.
    """
    if counts.height == 0 or len(edges) < 2:
        return {}
    bounds = np.asarray(edges, dtype=float)
    out: dict[str, dict[str, list[int]]] = {}
    for (sample, tag), frame in counts.group_by(["sampleId", "tag"]):
        values = frame["umiCount"].cast(pl.Float64).to_numpy()
        if values.size == 0:
            continue
        # `np.histogram` closes the last bin on the right, so the maximum count is counted.
        weights, _ = np.histogram(values, bins=bounds)
        out.setdefault(str(sample), {})[str(tag)] = [int(w) for w in weights]
    return out
