"""The quality measurements a run carries.

Every measurement carries what it counts, because the reader who meets it is not the person
who chose it. Where a line can be defended, it also carries what a bad value implies. Where
none can, it carries nothing -- the number and its distribution are shown and the reader
judges. None carries what to do about it, because advice depends on the run, the study and
what else is available.

A measurement this module cannot compute is declared anyway, with the reason, so a reader
never mistakes "nothing computed this yet" for "checked and found fine". Most of the set is
computed elsewhere and only declared here: undeclared barcodes and declared-but-unseen tags
in ``panel.py``, the floor's counts and the high-reference-cell count in ``verdict.py``,
per-tag self-disagreement in ``combine.py``, read and per-cell totals in ``qc_report.py``.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

import numpy as np
import polars as pl


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
    id: str
    label: str
    level: str  # "sample" | "tag" | "identity" | "run"
    counts: str  # what went into it
    implies: str | None = None  # what a bad value means, where a line exists
    line: str | None = None  # which defence route backs `implies`, if any
    deferred_reason: str | None = None  # set only when nothing computes this yet
    # Whether this measurement's status reaches its level's rollup. False only where the
    # measurement is a property of a reagent rather than of the sample it was measured on, which
    # keeps a reagent's failure off every sample.
    rolls_up: bool = True


MEASUREMENTS: tuple[Measurement, ...] = (
    Measurement(
        "readsTotal",
        "Reads total and fraction matched",
        "sample",
        # No line. Exactly four numbers are inherited from the field, and the matched share is not
        # one of them: usable antigen-read fraction (warn below 0.20), undeclared-barcode fraction
        # (warn above 0.50), aggregate-barcode read fraction (warn above 0.05), barcode validity
        # (warn below 0.75). Nothing published says what a low matched share means.
        "Every read the parser saw, and the share matching the tag pattern.",
    ),
    # `qc_report._refine_kept_fraction` returns the FEATURE step's outputCount/inputCount -- the share
    # of matched reads whose barcode corrects onto a panel entry. Its complement is the share landing
    # in barcodes the panel never declared, which is a property of a barcode and not of a sample. The
    # line that used to sit here now backs the undeclared-barcode table's own row
    # (`undeclaredBarcodeShare` below), keyed by sequence and computed on the pre-refine counts. This
    # row keeps the number and carries no status.
    #
    # The usable row is a different quantity: Cell Ranger `main`,
    # `lib/python/cellranger/rna/report_matrix.py`, `_report_genome_agnostic_metrics`, defines
    # `frac_feature_reads_usable` as conf-mapped, barcoded reads restricted to the called-cell
    # partition (`cell_bcs_union`), over the whole library's read count. UMI validity is that source's
    # separate `good_umi_frac` figure. It is declared below as `usableReadFraction`.
    #
    # The id is a value on the `measurement` axis and a p-column name in the per-sample QC frame.
    # Renaming it breaks both.
    Measurement(
        "panelAssignedFraction",
        "Fraction of reads in undeclared barcodes (as its complement)",
        "sample",
        "Reads whose corrected barcode is on the panel, over reads matched.",
    ),
    # Ported from Cell Ranger `main`, `lib/python/cellranger/rna/report_matrix.py`,
    # `_report_genome_agnostic_metrics::frac_feature_reads_usable`: conf-mapped, barcoded reads
    # restricted to the called-cell partition, over the whole library's read count.
    # `usable_read_fraction` sums the post-refine tag-stat's `totalWeight` over rows whose cell barcode
    # is in the cell list, divided by readsTotal. Every row of that table already carries a
    # panel-recognised FEATURE value, so restricting to the cell list is the only condition left.
    Measurement(
        "usableReadFraction",
        "Fraction of antigen reads usable",
        "sample",
        "Reads whose corrected barcode is on the panel and whose cell barcode is in the cell list, over readsTotal.",
        "A low share means most of the library's reads are lost before reaching a called cell "
        "with a panel-recognised barcode.",
        "inherited",
    ),
    # The fourth inherited line, and the only one whose thresholds step the same way twice rather than
    # putting error at total failure. The refine-tags report already carries the CELL step this reads.
    Measurement(
        "cellBarcodeValidFraction",
        "Fraction of reads whose cell barcode the chemistry could have produced",
        "sample",
        "Reads whose cell barcode corrects onto the chemistry's whitelist, over reads entering correction.",
        "A low share means the reads carry cell barcodes this chemistry does not produce, "
        "which points at the wrong whitelist or the wrong read geometry.",
        "inherited",
    ),
    # The categorical route's first member. The alerting condition is a fact -- no cell barcode observed
    # at all -- rather than a quantity with a published threshold. Computed in `qc_report.py` as the
    # count of distinct cell barcodes the tag-stat table carries, before any cell-calling step.
    #
    # `implies` names only the zero case: nothing downstream can be computed for a sample with no cells.
    # Above zero nothing is claimed, because how many cells a sample should yield depends on the
    # experiment.
    Measurement(
        "cellsDetected",
        "Cell barcodes detected",
        "sample",
        "Distinct cell barcodes in the tag-stat table, before any cell-calling step.",
        "Zero cells means nothing downstream can be computed for this sample.",
        "categorical",
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
        "Reads per cell",
        "sample",
        "Reads matched, over cells in the cell list.",
        "Below the vendor's recommended minimum the library is undersequenced.",
        "recommended-and-observed",
    ),
    Measurement(
        "antigenCountDistribution",
        "Distribution of antigen count per barcode",
        "sample",
        "Deciles of the total antigen count per cell barcode.",
    ),
    # Ports `detect_outlier_umis_bcs` from Cell Ranger `main`,
    # `lib/python/cellranger/feature/antibody/analysis.py`, called for the ANTIGEN library type from
    # `cell_calling_helpers.py::remove_antibody_antigen_aggregates`. That function's antibody sibling
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
        "Fraction of reads in aggregate barcodes",
        "sample",
        "Reads in barcodes flagged as aggregates by the top-100 IQR rule, over readsTotal.",
        "A high share means much of the run's antigen signal comes from a small number of "
        "clumped droplets rather than single cells.",
        "inherited",
    ),
    # No line, and worth spelling out because the spec looks like it supplies one. The field publishes
    # 0.50, but for one aggregate library fraction. This measurement is per sequence at tag level, and
    # a fraction's line does not transfer to a list of sequences. Given a count instead, any at-most
    # line collapses into "alerting if a single undeclared barcode exists".
    Measurement(
        "undeclaredBarcodes",
        "Undeclared barcodes, and which sequences",
        "tag",
        "Barcodes the reads carry that the sample's panel does not declare, and which sequences they are.",
    ),
    # No status. A fact on the tag's row, reported for the reagent's sake rather than the answer's:
    # cells in a sample where the tag returned nothing do not count toward what could answer there, so
    # the verdict already reads *never asked*.
    Measurement(
        "declaredNeverSeen",
        "Declared tags the reads never show",
        "tag",
        "Tags on the sample's panel with no reads at all.",
    ),
    Measurement(
        "floorRemoved",
        "Counts removed as below the minimum, and cells left with none",
        "sample",
        "Readings the minimum zeroed, and cells whose every non-reference reading was removed.",
    ),
    Measurement(
        "uniqueCountsPerCell",
        "Reads and unique counts per cell",
        "sample",
        "Reads and distinct UMIs per cell barcode.",
    ),
    # Two forms, and the gate decides which: how many are high needs a high, and only a declared gate
    # supplies one. With a gate the value counts the cells it set aside; with none it is the median of
    # the readings and the detail carries their deciles.
    #
    # No line either way. Nothing published says what share of cells is too high, nor what a background
    # reading of any size means.
    Measurement(
        "highReferenceCells",
        "Sticky cells, or the spread of the readings where no gate is declared",
        "sample",
        "Cells whose reference reading exceeded the declared gate, or the spread of those readings.",
    ),
    # The id is a value on the `measurement` axis, so renaming it does not break the column -- it
    # splits the rows, and a table holding old and new runs reads as two measurements.
    #
    # Its three figures: cells-with-count is read before the minimum, the median is taken over every
    # cell holding a count rather than over the bound ones, and every declared tag keeps a row so a
    # dead reagent reads as a zero rather than as an absence.
    Measurement(
        "perAntigen",
        "Per antigen: cells with a count, cells called bound, and the median",
        "tag",
        "Per tag: cells with any count and their median count, both before the minimum, "
        "and cells called bound after it.",
    ),
    # One figure for the run rather than per sample, because the cutoff is one number for the run. Only
    # the declared rung produces a score at all: a population baseline yields a probability.
    #
    # No line. A scientist may move the cutoff to where their own run's scores separate, and that
    # licence is unusable unless the scores are in front of them.
    Measurement(
        "scoreDistribution",
        "Distribution of the run's scores",
        "run",
        "Deciles of the score over every cell and identity the declared rule scored.",
    ),
    # Only a population baseline fits one, so under a declared baseline this carries no value and says
    # so. It is the only way to see whether a tag's counts separated at all, which a scientist reads
    # BEFORE settling the baseline, so it must not depend on any cutoff.
    #
    # No line. A background is read against the signal mean beside it and against the other tags of the
    # panel, which is a comparison rather than a boundary.
    #
    # The FIT runs per (sample, tag); this row is keyed (tag, panel), so its value is the median over
    # the panel's samples that fitted and the detail carries the spread and the unfitted count. The
    # `counts` text says so. The per-(sample, tag) reading is the fitted-background GRID on the
    # run-quality page, drawn from the binned counts.
    Measurement(
        "fittedBackground",
        "Fitted background, where a population baseline served",
        "tag",
        "The background component's mean count, its share of cells, and the signal mean beside it -- "
        "each the median over this panel's samples that fitted, since the fit runs per sample. The "
        "detail carries the spread and how many samples fitted; the run-quality grid draws each fit "
        "on its own.",
    ),
    # Self-disagreement at an IDENTITY is deliberately not measured. Marginal binding inflates
    # disagreement everywhere, so comparing one tag against its siblings under the same cells, run and
    # line leaves a tag that stands clear standing clear for a reason that is not biology. The
    # identity-level figure has nothing to compare against: cells of one clonotype all agree only where
    # the reading sits clear of the line, so for anything marginal disagreement is near certain and the
    # rate measures how many clonotypes sit near it.
    #
    # No line, so it reads unjudged beside its siblings. Applying a threshold would need a multiplier
    # nobody published.
    Measurement(
        "tagDisagreement",
        "Clonotype self-disagreement at a single tag",
        "tag",
        "Of the cells whose set had another cell to compare against, the share reading the opposite way "
        "from the rest of their own set, by this tag's count alone. Two states cap it at half.",
    ),
    # Available only where an identity carries more than one tag. Distinct from tagDisagreement:
    # cross-tag within one cell, not within-tag across cells. No line: nothing published says what
    # share is too high.
    Measurement(
        "siblingDisagreement",
        "Disagreement with the tags sharing its identity",
        "tag",
        "Of the cells whose siblings reached a majority, the share reading the opposite way from it. The "
        "tag casts no vote in the majority it is judged against, and tied siblings judge nothing.",
    ),
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

# The three places a line can come from, and nowhere else. `Measurement.line` names one or None,
# and it is the *only* declaration of which measurements carry a line -- the tables below are
# derived facts about the route. A test asserts the correspondence in both directions.
#
# A comparison against the other tags in a panel is NOT a line. It yields no boundary, and a status
# derived from it would need a multiplier nobody has published. Such a measurement reads unjudged
# beside its siblings. The cost is real and accepted: a barcoded reagent binding something other
# than the receptor no longer announces itself.
#
# The categorical route has one member: `cellsDetected`. A declared tag the reads never show was its
# earlier example until the verdict took the job.
LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed"})

# The categorical route's member ids. Derived from `Measurement.line`, never a second declaration.
# A categorical fact carries no numeric threshold, so its id is deliberately absent from
# `DEFAULT_LINES` and `_COMPARISON` -- `status_for` answers it before either table is consulted.
_CATEGORICAL: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.line == "categorical")

# Every line is a parameter with a shipped default, and the operator may override any of them. No
# line is invented -- where none of the three routes applies the measurement carries no status.
#
# `undeclaredBarcodeShare` backs the undeclared-barcode table's own row rather than a declared
# `Measurement`: that status is the barcode's, never a sample's, so it is computed and carried where
# the barcode rows are, in emit_verdicts.py, and reaches `status_for` under this id. It is the one
# exception to "every line backs a declared measurement", and a test names it.
DEFAULT_LINES: dict[str, Line] = {
    # Both thresholds step the same way. This is the line with a real gradient at the far end.
    "cellBarcodeValidFraction": Line(warn=0.75, error=0.50),
    # One published number gives one boundary, so depth warns and never alerts.
    "readsPerCell": Line(warn=5_000),
    # Published values for the aggregate-barcode read fraction: warn above 0.05, error at total
    # failure (1.0).
    "aggregateBarcodeFraction": Line(warn=0.05, error=1.0),
    # Published values for the undeclared-barcode read fraction, read direct rather than as a
    # complement: warn above 0.50, error at total failure (1.0).
    "undeclaredBarcodeShare": Line(warn=0.5, error=1.0),
    # Published values for the usable antigen-read fraction: warn below 0.20, error at total
    # failure (0.0).
    "usableReadFraction": Line(warn=0.20, error=0.0),
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
# The two thresholds of one line are read INDEPENDENTLY. Three of the four inherited lines warn on
# a direction and put error at total failure -- "at 0", "at 1.0" -- which is `alerting-at` and not
# a further step along the warn direction. Only barcode validity steps the same way twice: warn
# below 0.75, error below 0.50. One direction per measurement collapsed those into one, and a
# fraction whose error sits "at 0" could then never alert.
_COMPARISON: dict[str, tuple[str, str | None]] = {
    "cellBarcodeValidFraction": ("at-least", "at-least"),
    "readsPerCell": ("at-least", None),
    # Error at total failure (`alerting-at` 1.0) rather than a further step past warn: every
    # inherited share sits at either "at least" or "at most" with error at the catastrophe end, and
    # this is one of the two upward-facing members of that set.
    "aggregateBarcodeFraction": ("at-most", "alerting-at"),
    "undeclaredBarcodeShare": ("at-most", "alerting-at"),
    # Error at total failure (`alerting-at` 0.0), the downward-facing member of that same set.
    "usableReadFraction": ("at-least", "alerting-at"),
}


def _breaches(value: float, threshold: float, comparison: str) -> bool:
    """Whether a value falls the wrong side of one threshold."""
    if comparison == "at-least":
        return value < threshold
    if comparison == "at-most":
        return value > threshold
    return value == threshold


_ORDINAL = {Status.OK: 0, Status.WARN: 1, Status.ALERT: 2}

_DEFERRED: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.deferred_reason)


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

    Three answers and no fourth. A deferred measurement, a measurement with no number, and a
    measurement with no line all carry no status -- and which of those happened is read from the
    value.

    A deferred measurement carries none whatever it is handed: nothing computes it, so a value
    reaching here is a caller's mistake and must not be laundered into a judgement about the run.

    The categorical route is read before `lines`: its fact is not a threshold, so neither
    `DEFAULT_LINES` nor `_COMPARISON` carries an entry for it. Zero alerts; any other finite value
    reads OK and claims nothing about how many cells the sample should have yielded.
    """
    if measurement in _DEFERRED or not is_computed(value):
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


def measurement_row(m: Measurement) -> dict:
    """One declared measurement, rendered for a reader who never opens this module.

    A deferred measurement renders with its reason attached and keeps its place in the set. The
    difference between "checked and fine" and "never checked" is lost the moment a deferred id
    simply has no row.
    """
    return {
        "id": m.id,
        "label": m.label,
        "level": m.level,
        "counts": m.counts,
        "implies": m.implies,
        # No status, ever. A declaration is not a reading, and a deferred measurement carries its
        # reason in place of a number rather than a fourth status word.
        "status": None,
        "reason": m.deferred_reason,
    }


def measurement_rows() -> list[dict]:
    """Every declared measurement, deferred ones included, in declaration order."""
    return [measurement_row(m) for m in MEASUREMENTS]


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

        here = states.filter(pl.col("tag").is_in(members)).select("sampleId", "cellId", "tag", "state")
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


def reads_per_cell(reads_matched: int, cells_in_list: int) -> float | None:
    """Reads matched, over cells in the cell list.

    The denominator is the **cell list**, not the barcodes the reads happened to touch. The vendor's
    five-thousand recommendation is per called cell, and in droplet data the observed-barcode count
    runs one to two orders of magnitude higher, so dividing by observed barcodes would make a
    healthy library alert.

    `reads_matched` already exists in the per-sample QC row. The cell list arrives with gene
    expression or with the receptors, so the caller supplies its size. Deliberately not
    `cellsDetected` from that row -- that is the observed-barcode count warned against here.

    None when the cell list is empty. A rate over no cells is no number, which is distinct from a
    computed rate that happens to be zero.
    """
    if cells_in_list <= 0:
        return None
    return reads_matched / cells_in_list


def usable_read_fraction(
    tag_stat: pl.DataFrame,
    cell_col: str,
    listed_cells: Collection[str] | None,
    reads_total: int | None,
) -> tuple[float | None, str]:
    """Reads landing on a called cell, recognised against the panel, over `reads_total`.

    Ports Cell Ranger's `frac_feature_reads_usable` (Cell Ranger `main`,
    `lib/python/cellranger/rna/report_matrix.py`, `_report_genome_agnostic_metrics`): conf-mapped,
    barcoded reads restricted to the called-cell partition, over the whole library's read count. UMI
    validity is that source's separate `good_umi_frac` figure and is not part of this one.

    `tag_stat` is the post-refine tag-stat table, one row per (cell, feature barcode) surviving
    refine-tags -- every FEATURE value outside the panel is already gone, which is the recognition
    condition. `totalWeight` is its read-weight column. `listed_cells` is the sample's own cell list.

    Returns `(None, reason)` where `listed_cells` is None, or where `reads_total` is absent or zero.
    An empty (non-None) cell list still returns 0.0: no read landing on a called cell is a real
    finding, not a missing input.
    """
    if listed_cells is None:
        return None, "no cell list supplied, so the called-cell condition cannot be evaluated"
    if not reads_total:
        return None, "no total read count to divide by"
    usable = float(tag_stat.filter(pl.col(cell_col).is_in(list(listed_cells)))["totalWeight"].sum())
    return usable / reads_total, f"cellsInList={len(listed_cells)}"


# Cell Ranger's own constants for the ANTIGEN branch of `detect_outlier_umis_bcs`
# (`lib/python/cellranger/feature/antibody/analysis.py`): a 3x interquartile multiplier over the top
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

    Ports `detect_outlier_umis_bcs` (Cell Ranger `main`,
    `lib/python/cellranger/feature/antibody/analysis.py`), called for the ANTIGEN library type from
    `cell_calling_helpers.py::remove_antibody_antigen_aggregates`.

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
            f"barcodesTested={tested}|threshold={threshold:.1f} "
            f"(below the {min_umi_threshold:.0f}-UMI floor, no barcode flagged)"
        )
    else:
        detail = f"barcodesTested={tested}|threshold={threshold:.1f}|barcodesFlagged={len(flagged)}"
    flagged_reads = per_barcode.filter(pl.col("barcode").is_in(flagged))["readCount"].sum() if flagged else 0
    return flagged_reads / reads_total, detail


# The extremes are included alongside the interior deciles so the distribution's edges are visible,
# not only its middle: eleven points, 0 through 100 by 10.
DECILE_POINTS: tuple[int, ...] = tuple(range(0, 101, 10))


def deciles_of(values: np.ndarray) -> pl.DataFrame:
    """The eleven decile points of `values`, or eleven unanswered points where there are none.

    Split out of `antigen_count_deciles` so a second spread reports the same shape. An empty input
    still returns all eleven rows with a null value.
    """
    if values.size == 0:
        return pl.DataFrame(
            {"decile": list(DECILE_POINTS), "value": [None] * len(DECILE_POINTS)},
            schema={"decile": pl.Int64, "value": pl.Float64},
        )
    return pl.DataFrame(
        {"decile": list(DECILE_POINTS), "value": [float(np.quantile(values, p / 100)) for p in DECILE_POINTS]}
    )


# How many log-spaced buckets a count distribution is drawn in. Enough bars for two humps to read
# apart at thumbnail size, few enough that a sparse tag does not dissolve into single-cell spikes.
COUNT_BIN_COUNT = 24


def count_bin_edges(counts: pl.DataFrame) -> list[float]:
    """Log-spaced bin edges spanning every count in the frame, shared by every plot drawn from it.

    The caller passes the counts of the CELL LIST where one arrived, so the domain ends at the
    highest count among cells. Observed barcodes outnumber cells by one to two orders of magnitude.

    ONE edge set for the whole run, not one per tag. A reader judges whether a tag's counts fall into
    two separated humps by scanning a grid of tags side by side, and per-tag edges would rescale
    every panel to its own range, so a tag whose counts span 1-4 and one spanning 1-4000 would draw
    identical pictures.

    Log-spaced because UMI counts per cell span orders of magnitude: on a linear axis the ambient
    population occupies one bar and everything above it is empty.

    Edges start at 1, the smallest count that exists -- a row is only written for an observed
    reading, so zero never appears. `[]` where the frame holds no counts at all.
    """
    if counts.height == 0:
        return []
    top = float(counts["umiCount"].max() or 0)
    if top < 1:
        return []
    # `top` lands on the last edge, so the largest count falls inside the last bin rather than
    # outside every bin.
    return [float(x) for x in np.geomspace(1.0, max(top, 2.0), COUNT_BIN_COUNT + 1)]


def linear_bin_edges(values: np.ndarray, count: int = COUNT_BIN_COUNT) -> list[float]:
    """Evenly spaced bin edges spanning `values`. `[]` where there are none.

    Linear, unlike `count_bin_edges`: a specificity score is a 0-100 scale and a reference reading is
    read against a gate a scientist types in the same units, so a log axis would put the number they
    are choosing somewhere they cannot find it.
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


def antigen_count_deciles(counts: pl.DataFrame) -> pl.DataFrame:
    """Deciles of the total antigen count per cell barcode.

    `counts` is the sparse per-(cell, tag) frame -- one row per observed reading, columns sampleId,
    cellId, umiCount -- taken before flooring or identity-combining. A cell's total sums every tag it
    shows any reading for. A cell with no row contributes no total, since crediting it zero would
    read as a reading rather than the absence it is.

    Returns one row per decile point, columns `decile` and `value`. An empty input still returns all
    eleven rows with `value` null.
    """
    if counts.height == 0:
        return deciles_of(np.empty(0))
    totals = counts.group_by(["sampleId", "cellId"]).agg(pl.col("umiCount").sum().alias("total"))["total"].to_numpy()
    return deciles_of(totals)
