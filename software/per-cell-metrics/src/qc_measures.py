"""The quality measurements a run carries.

Every measurement carries what it counts, because the reader who meets it is not the
person who chose it: a fraction with a name and no statement of what went into the
numerator gets read as whatever the name suggests, and several of these names suggest
more than they carry.

Where a line can be defended, it also carries what a bad value implies. Where none
can, it carries nothing -- the number and its distribution are shown and the reader
judges. None carries what to do about it, because advice depends on the run, the study
and what else is available, none of which this readout knows.

A measurement this module cannot compute is declared anyway, with the reason, so a
reader never mistakes "nothing computed this yet" for "checked and found fine". Most of
the set is computed elsewhere and only declared here: undeclared barcodes and
declared-but-unseen tags in ``panel.py``, the floor's counts and the high-reference-cell
count in ``verdict.py``, per-tag self-disagreement in ``combine.py``, read and per-cell
totals in ``qc_report.py``. This module declares the full set and computes the rest.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

import numpy as np
import polars as pl


# Three values and no fourth. `310-qc-status-and-rollup` refuses a fourth and a fifth on the
# grounds that a reader meeting five words in one column reads them as a scale. The two cases
# a fourth word covered are read from the VALUE instead: a computed measurement shows its
# number, and one the run could not supply the inputs for shows the reason in place of one.
#
# So a measurement with no line behind it carries no status -- `status_for` returns None --
# and the row is still there. The strings are the atom's own, casing included, so a reader
# checking the column against the spec finds the same words.
class Status(str, Enum):
    OK = "OK"
    WARN = "warn"
    ALERT = "alert"


class Line(NamedTuple):
    """Where a measurement's boundaries sit.

    Two thresholds, because all four inherited lines arrive with both and collapsing them
    loses a distinction somebody calibrated. `315-where-the-lines-come-from` keeps the
    field's word *error* for the second threshold while the status it produces is *alert*.

    `error` is None where only one boundary was published. A stated recommendation gives one
    number, so sequencing depth warns and never alerts.
    """

    warn: float
    error: float | None = None


class Reading(NamedTuple):
    """How one measurement came back, as `roll_up` needs to count it.

    The status alone is no longer enough. Both no-status cases return None from
    `status_for`, and the coverage triple still separates them, so the value has to travel
    with the status -- a number means computed-but-unjudged, its absence means nothing
    computed it.
    """

    status: Status | None
    value: float | None


@dataclass(frozen=True)
class Coverage:
    """A level's status, and how much of it was actually checked.

    `status` is None where nothing at this level carried one. A level with nothing judged
    makes no claim, which is the same refusal one level up.
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
    # measurement is a property of a reagent rather than of the sample it was measured on --
    # see `310-qc-status-and-rollup`, which keeps a reagent's failure off every sample.
    rolls_up: bool = True


MEASUREMENTS: tuple[Measurement, ...] = (
    Measurement(
        "readsTotal",
        "Reads total and fraction matched",
        "sample",
        # No line. Exactly four numbers are inherited from the field, and the matched
        # share is not one of them: usable antigen-read fraction (warn below 0.20),
        # undeclared-barcode fraction (warn above 0.50), aggregate-barcode read fraction
        # (warn above 0.05), barcode validity (warn below 0.75). Nothing published says
        # what a low matched share means, so nothing here claims to.
        "Every read the parser saw, and the share matching the tag pattern.",
    ),
    # This satisfies the spec's UNDECLARED-BARCODE row, not its "fraction of antigen reads
    # usable" row, and it was filed against the wrong one. `qc_report._refine_kept_fraction`
    # returns the FEATURE step's outputCount/inputCount -- the share of matched reads whose
    # barcode corrects onto a panel entry. Its complement is the share landing in barcodes the
    # panel never declared, which is the field's quantity exactly, and the line transfers with
    # it: warn above 0.50 undeclared is warn below 0.50 assigned, error at 1.0 is error at 0.0.
    #
    # The usable row needs matched reads that also carry a cell-associated barcode and a valid
    # UMI. Nothing here computes that, and it stays unimplemented rather than borrowing this.
    #
    # `rolls_up=False` per `310`: the field publishes this line on a sample's share, but the
    # finding belongs to a reagent, and a reagent belongs to the run rather than to any one
    # sample. Rolled into the sample it would mark a sample for a panel's failure.
    #
    # The id is a value on the `measurement` axis and a p-column name in the per-sample QC
    # frame. Renaming it breaks both. Only the label and the wording moved.
    Measurement(
        "panelAssignedFraction",
        "Fraction of reads in undeclared barcodes (as its complement)",
        "sample",
        "Reads whose corrected barcode is on the panel, over reads matched.",
        "A low share means most reads carry barcodes the panel never declared.",
        "inherited",
        rolls_up=False,
    ),
    # The fourth inherited line, and the one `315` says the third status level exists for: the
    # only one whose thresholds step the same way twice rather than putting error at total
    # failure. The refine-tags report already carries the CELL step this reads, beside the
    # FEATURE step above.
    Measurement(
        "cellBarcodeValidFraction",
        "Fraction of reads whose cell barcode the chemistry could have produced",
        "sample",
        "Reads whose cell barcode corrects onto the chemistry's whitelist, over reads entering correction.",
        "A low share means the reads carry cell barcodes this chemistry does not produce, "
        "which points at the wrong whitelist or the wrong read geometry.",
        "inherited",
    ),
    # Saturation is deliberately NOT measured. The vendor's own report carries it, and
    # nothing hangs on it: a scientist cannot act on it for the run already collected, and
    # whether the run was deep enough is answered by reads per cell below. A number nobody
    # acts on competes for attention with numbers they do.
    #
    # Per *cell*, not per observed barcode. The vendor's five thousand is per-cell, and in
    # droplet data the observed-barcode count exceeds the called-cell count by one to two
    # orders of magnitude, because ambient antigen reads land on most barcodes -- dividing
    # by it would alert on a healthy library. The cell list arrives later than this module,
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
    Measurement(
        "aggregateBarcodeFraction",
        "Fraction of reads in aggregate barcodes",
        "sample",
        "Reads in barcodes flagged as aggregates, over reads matched.",
        deferred_reason="no aggregate-barcode detection exists in this block",
    ),
    # No line, and worth spelling out because the spec looks like it supplies one. The
    # field publishes 0.50, but for one aggregate library fraction. This measurement is
    # per sequence at tag level, and a fraction's line does not transfer to a list of
    # sequences. Given a count instead, any at-most line collapses into "alerting if a
    # single undeclared barcode exists" -- a categorical predicate wearing an inherited
    # number. So it ships unjudged, with its sequences and their counts.
    Measurement(
        "undeclaredBarcodes",
        "Undeclared barcodes, and which sequences",
        "tag",
        "Barcodes the reads carry that the sample's panel does not declare, and which sequences they are.",
    ),
    # No status. A fact on the tag's row, reported for the reagent's sake rather than the
    # answer's, because the answer already carries it: cells in a sample where the tag
    # returned nothing do not count toward what could answer there, so the verdict reads
    # *never asked*. Warning a reader off an answer that already says so would be a second
    # voice on one fact.
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
    # Two forms, and the gate decides which. `290-reference-two-roles`: how many are high
    # needs a high, and only a declared gate supplies one. With a gate the value counts the
    # cells it set aside; with none it is the median of the readings and the detail carries
    # their deciles, which is what a scientist reads in order to declare a gate.
    #
    # No line either way. Nothing published says what share of cells is too high, nor what a
    # background reading of any size means -- both are read against the run's own spread.
    Measurement(
        "highReferenceCells",
        "Sticky cells, or the spread of the readings where no gate is declared",
        "sample",
        "Cells whose reference reading reached the declared gate, or the spread of those readings.",
    ),
    # The id is a value on the `measurement` axis, so renaming it does not break the column --
    # it splits the rows. Old runs would carry one measurement name and new runs another, and a
    # table holding both reads as two measurements. Keep it.
    #
    # Its three figures changed meaning in 330-the-quality-readout: cells-with-count is now read
    # before the minimum, the median is taken over every cell holding a count rather than over
    # the bound ones, and every declared tag keeps a row so a dead reagent reads as a zero
    # rather than as an absence.
    Measurement(
        "perAntigen",
        "Per antigen: cells with a count, cells called bound, and the median",
        "tag",
        "Per tag: cells with any count and their median count, both before the minimum, "
        "and cells called bound after it.",
    ),
    # One figure for the run rather than per sample, because the cutoff is one number for the
    # run. Only the declared rung produces a score at all: a population baseline yields a
    # probability, so under it this measurement does not exist and says so.
    #
    # No line. `320-qc-measurement-set` carries it so a scientist can move the cutoff to where
    # their own run's scores separate, and that licence is unusable unless the scores are in
    # front of them. A line here would be the block placing the cutoff instead.
    Measurement(
        "scoreDistribution",
        "Distribution of the run's scores",
        "run",
        "Deciles of the score over every cell and identity the declared rule scored.",
    ),
    # Only a population baseline fits one, so under a declared baseline this carries no value
    # and says so. `330-the-quality-readout` asks for it as a plot, and it is the only way to
    # see whether a tag's counts separated at all -- which a scientist reads BEFORE settling the
    # baseline, so it must not depend on any cutoff.
    #
    # No line. Nothing published says what a background of any size means: it is read against
    # the signal mean beside it and against the other tags of the panel, which is a comparison
    # rather than a boundary.
    Measurement(
        "fittedBackground",
        "Fitted background, where a population baseline served",
        "tag",
        "The background component's mean count, its share of cells, and the signal mean beside it.",
    ),
    # Self-disagreement at an IDENTITY is deliberately not measured. Keeping the tag-level
    # figure while dropping this one rests on which confound cancels: marginal binding
    # inflates disagreement everywhere, so comparing one tag against its siblings under the
    # same cells, run and line leaves a tag that stands clear standing clear for a reason
    # that is not biology. The identity-level figure has nothing to compare against, so it
    # cannot separate a faulty reagent from a panel of weak binders -- cells of one clonotype
    # all agree only where the reading sits clear of the line, so for anything marginal
    # disagreement is near certain and the rate measures how many clonotypes sit near it.
    # Whatever it would say about one clonotype is already on that verdict.
    #
    # No line, so it reads unjudged beside its siblings. A tag standing clear of the others
    # in its panel is misbehaving whatever the absolute rate -- a real finding, but one a
    # reader makes by looking. Applying a threshold would need a multiplier nobody
    # published.
    Measurement(
        "tagDisagreement",
        "Clonotype self-disagreement at a single tag",
        "tag",
        "Of the cells whose set had another cell to compare against, the share reading the opposite way "
        "from the rest of their own set, by this tag's count alone. Two states cap it at half.",
    ),
    # Available only where an identity carries more than one tag, and the cleanest reagent
    # check on this surface: the scientist declared those tags to be one thing, so nothing
    # biological explains a difference between them in one cell.
    #
    # It earns a place beside `tagDisagreement` rather than replacing it. Self-disagreement
    # asks only whether one tag's own cells are consistent, so a weak reagent whose siblings
    # agree with each other and not with it sits among the lowest there while standing right
    # out here.
    #
    # No line. Nothing published says what share is too high, so nothing here claims to.
    Measurement(
        "siblingDisagreement",
        "Disagreement with the tags sharing its identity",
        "tag",
        "Of the cells holding this tag beside a sibling, the share reading the opposite way from the "
        "majority of the identity's tags in that cell. A cell splitting evenly convicts nobody.",
    ),
    # Whether a clonotype of known specificity came back correctly is deliberately NOT
    # measured. It would be the only end-to-end check of the pipeline, and nothing computes
    # it because nothing declares it: no surface asks a scientist which clonotype they
    # already know the answer for. What they do instead is find that clonotype in the
    # readout and read its row, which the readout already supports.
)

# The three places a line can come from, and nowhere else. `Measurement.line` names one
# or None, and it is the *only* declaration of which measurements carry a line -- the
# tables below are derived facts about the route, never a second opinion. A test asserts
# the correspondence in both directions.
#
# A comparison against the other tags in a panel is NOT a line. It yields no boundary, and
# a status derived from it would need a multiplier -- an interquartile multiple, a
# median-absolute-deviation cut -- that nobody has published, which moves the invention up
# a level rather than removing it. Such a measurement reads unjudged beside its siblings,
# where the comparison is free for a reader to make. The cost is real and accepted: a
# barcoded reagent binding something other than the receptor no longer announces itself.
#
# The categorical route has no member. A declared tag the reads never show was its one
# example until the verdict took the job: that condition now removes the tag's cells from
# what could answer, so the answer carries the finding. The route stays because it is one
# of the three places a line can come from, not because something uses it.
LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed"})

# Every line is a parameter with a shipped default, and the operator may override any of
# them. No line is invented -- where none of the three routes applies the measurement carries
# no status rather than being given a number with nothing behind it.
#
# Three of `315`'s five lines are in force. The two missing ones are missing because their
# measurement is: the aggregate-barcode fraction is deferred, and nothing computes the usable
# fraction, which needs a cell-associated barcode and a valid UMI that this block never sees.
DEFAULT_LINES: dict[str, Line] = {
    # `error` at total failure, which is where three of the four inherited lines put it: a
    # catastrophe rather than a degree. `warn` is 0.5 rather than the field's 0.20 because the
    # quantity differs -- see the drift recorded on the measurement above. Resolving that is
    # what moves this number.
    "panelAssignedFraction": Line(warn=0.5, error=0.0),
    # Both thresholds step the same way. This is the line with a real gradient at the far end.
    "cellBarcodeValidFraction": Line(warn=0.75, error=0.50),
    # One published number gives one boundary, so depth warns and never alerts.
    "readsPerCell": Line(warn=5_000),
}

# How each line is read. Deliberately *not* overridable: an operator moves a number, never
# a direction.
#
#   at-least    acceptable at or above the line, alerting strictly below
#   at-most     acceptable at or below the line, alerting strictly above
#   alerting-at alerting where the value equals the line
#
# In every case the named value satisfies the condition it names.
#
# How each line's thresholds are read. Deliberately *not* in DEFAULT_LINES: an operator moves
# a number, never a direction.
#
#   at-least    OK at or above the threshold, bad strictly below
#   at-most     OK at or below the threshold, bad strictly above
#   alerting-at bad where the value equals the threshold
#
# In every case the named value satisfies the condition it names.
#
# The two thresholds of one line are read INDEPENDENTLY, because `315-where-the-lines-come-from`
# reads them that way. Three of the four inherited lines warn on a direction and put error at
# total failure -- "at 0", "at 1.0" -- which is `alerting-at` and not a further step along the
# warn direction. Only the fourth, barcode validity, steps the same way twice: warn below 0.75,
# error below 0.50. One direction per measurement collapsed those into one, and a fraction whose
# error sits "at 0" could then never alert, since nothing is below zero.
#
# The second entry is None where the line published no error threshold.
#
# `at-most` has no member. The field's two upward lines are the undeclared-barcode fraction,
# which is registered above as its complement and so reads at-least, and the aggregate-barcode
# fraction, which is deferred. The reading stays because a line can bound from above.
_COMPARISON: dict[str, tuple[str, str | None]] = {
    "panelAssignedFraction": ("at-least", "alerting-at"),
    "cellBarcodeValidFraction": ("at-least", "at-least"),
    "readsPerCell": ("at-least", None),
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


def _computed(value: float | None) -> bool:
    """Whether a number came back at all.

    A non-finite value counts as absent. Every `<` and `>` against NaN is False, so treating
    it as a number let it fall through to the acceptable branch -- corrupt input reading
    green, the one status a reader will not investigate. +inf read green too against an
    at-least line, and -inf happened to alert. One rule for "not a finite number" is easier
    to defend than a rule whose answer depends on the sign.
    """
    return value is not None and math.isfinite(value)


def status_for(measurement: str, value: float | None, lines: dict[str, Line]) -> Status | None:
    """How one measurement reads, given the lines in force. None where no line stands behind it.

    Three answers and no fourth. A deferred measurement, a measurement with no number, and a
    measurement with no line all carry no status -- and which of those happened is read from
    the value, not from a fourth word in this column.

    A deferred measurement carries none whatever it is handed: nothing computes it, so a
    value reaching here is a caller's mistake and must not be laundered into a judgement
    about the run.
    """
    if measurement in _DEFERRED or not _computed(value):
        return None
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

    Coverage stays out of the ordinal because a status and a non-status answer different
    questions. The first says whether something is wrong, the second whether anybody looked.
    Ranked on one scale, an unchecked run becomes indistinguishable from a checked one.

    A level with nothing judged carries no status. Given one of OK the run would look
    checked, and given one of alert a scientist would chase a problem that does not exist.
    """
    judged = [r.status for r in readings if r.status is not None]
    unjudged = sum(1 for r in readings if r.status is None and _computed(r.value))
    not_evaluated = sum(1 for r in readings if r.status is None and not _computed(r.value))
    status = max(judged, key=lambda s: _ORDINAL[s]) if judged else None
    return Coverage(status, len(judged), unjudged, not_evaluated)


# Only the sample rolls up, so `roll_up` above is the only aggregation rule here.
#
# A panel status is gone because it overestimated what could be judged categorically: of
# the per-tag measurements one is categorical and the rest are read only as outliers
# against the other tags in the same panel, which is a comparison rather than a severity
# and cannot be rolled into one without discarding what made it a finding. A capture status
# followed the same logic -- the worst of every sample and every panel becomes the worst of
# every sample, which the samples already say.
#
# Nothing hides. A reagent finding states itself on its own per-tag row, keyed by the panel
# that has it, and a sample's own report names the measurement that set it alerting.


def measurement_row(m: Measurement) -> dict:
    """One declared measurement, rendered for a reader who never opens this module.

    A deferred measurement renders with its reason attached and keeps its place in the set.
    The difference between "checked and fine" and "never checked" is lost the moment a
    deferred id simply has no row.
    """
    return {
        "id": m.id,
        "label": m.label,
        "level": m.level,
        "counts": m.counts,
        "implies": m.implies,
        # No status, ever. A declaration is not a reading, and a deferred measurement carries
        # its reason in place of a number rather than a fourth status word.
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

    Grouped by tag, not identity: a tag's own reagent behaviour is the question, and an
    identity built from several tags would let one weak tag hide behind a stronger one.

    Two frames, and which one each column comes from is the whole point.

    `counts` is the RAW sparse frame, before the minimum -- one row per (sampleId, cellId,
    tag) with `umiCount`, reference tags included. `states` is the tag-grain frame after the
    minimum, with `tag` and `state`.

    `cellsWithCount` and `medianCountPerCell` come from `counts`, `cellsAboveTheLine` from
    `states`. 330-the-quality-readout fixes that split: the first measures what the reagent
    delivered and the second what survived the minimum, so a reagent putting two counts into
    every cell reads as delivering something rather than as delivering nothing. A median
    below the minimum is that same finding and not an error.

    The median is taken over every cell holding a count. Taken over bound cells it could only
    ever print a number above the cutoff's floor, because clearing the cutoff is what bound
    means, so a half-degraded reagent would show a healthy figure computed from the few cells
    that scraped over. It also then depends on no threshold, which matters on a first run
    where the cutoff is still being settled -- this is a page read in order to settle it.

    One row per declared tag, whether or not the reads ever show it. A dead reagent is read
    as a zero under cells-with-count, and a tag with no row at all offers nothing to read.

    `samplesSeenIn` counts distinct samples carrying any count of the tag, and `samplesInPanel`
    is the denominator. A tag absent from every sample reads 0.

    `panel_samples` is the panel's declared roster and supplies the seen-in denominator. It is the
    roster rather than the samples present in `counts`, which omits a sample that contributed no
    rows.

    Reference tags keep a row and carry `cellsAboveTheLine` as None. They are held out of the
    verdict read, so no state exists for them -- and a blank and a zero are opposite findings
    here. Their median is the run's ambient floor, which is why they belong in this table.
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
        )
    )
    samples_in_panel = len(set(panel_samples))
    bound = states.group_by("tag").agg((pl.col("state") == "bound").sum().alias("cellsAboveTheLine"))

    is_reference = pl.col("tag").is_in(references) if references else pl.lit(False)  # noqa: FBT003
    return (
        spine.join(delivered, on="tag", how="left")
        .join(bound, on="tag", how="left")
        .with_columns(
            pl.col("cellsWithCount").fill_null(0).cast(pl.Int64),
            pl.col("samplesSeenIn").fill_null(0).cast(pl.Int64),
            pl.lit(samples_in_panel, dtype=pl.Int64).alias("samplesInPanel"),
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
    """Per tag: the share of its cells that contradict the majority of its identity's tags.

    Siblings are the other tags the same identity carries. The scientist declared those tags
    to be one thing, so nothing biological separates them in one cell.

    Judged within one cell, over the identity's tags holding an explicit row there. A tag
    with no row in a cell does not vote: `states` is sparse and carries no silent cell.

    A majority is strict -- more than half the votes. A cell splitting evenly convicts
    nobody, and still counts as a cell compared.

    None, never zero, where no comparison exists: an identity carrying one tag, or a tag no
    cell holds beside a sibling.

    `states` is the tag-grain frame after the minimum, with `sampleId`, `cellId`, `tag` and
    `state`.
    """
    rates: dict[str, float | None] = {}
    for tags in tags_by_identity.values():
        members = sorted(set(tags))
        if len(members) < 2:
            for tag in members:
                rates[tag] = None
            continue

        here = states.filter(pl.col("tag").is_in(members)).select("sampleId", "cellId", "tag", "state")
        counted = here.group_by("sampleId", "cellId", "state").agg(pl.len().alias("n"))
        totals = counted.group_by("sampleId", "cellId").agg(pl.col("n").sum().alias("total"))
        # Filtered on the strict majority rather than on the largest count, so at most one
        # state survives per cell and no row ordering decides which.
        majority = (
            counted.join(totals, on=["sampleId", "cellId"], how="inner")
            .filter(pl.col("n") * 2 > pl.col("total"))
            .select("sampleId", "cellId", pl.col("state").alias("majority"))
        )
        present = here.group_by("sampleId", "cellId").agg(pl.col("tag").n_unique().alias("tagsHere"))
        compared = (
            here.join(present, on=["sampleId", "cellId"], how="inner")
            .filter(pl.col("tagsHere") > 1)
            .join(majority, on=["sampleId", "cellId"], how="left")
        )
        for tag in members:
            mine = compared.filter(pl.col("tag") == tag)
            if mine.height == 0:
                rates[tag] = None
                continue
            against = mine.filter(pl.col("majority").is_not_null() & (pl.col("state") != pl.col("majority")))
            rates[tag] = against.height / mine.height
    return rates


def reads_per_cell(reads_matched: int, cells_in_list: int) -> float | None:
    """Reads matched, over cells in the cell list.

    The denominator is the **cell list**, not the barcodes the reads happened to touch. The
    vendor's five-thousand recommendation is per called cell, and in droplet data the
    observed-barcode count runs one to two orders of magnitude higher, because ambient
    antigen reads land on most barcodes. Dividing by observed barcodes would make a healthy
    library alert -- worse than not judging depth at all, since a status that fires on good
    runs teaches a reader to ignore it.

    `reads_matched` already exists in the per-sample QC row. The cell list arrives with gene
    expression or with the receptors, so the caller supplies its size. Deliberately not
    `cellsDetected` from that row -- that is the observed-barcode count warned against here.

    None when the cell list is empty. A rate over no cells is not a small number, it is no
    number, and None keeps that distinct from a computed rate that happens to be zero.
    """
    if cells_in_list <= 0:
        return None
    return reads_matched / cells_in_list


# The extremes are included alongside the interior deciles so the distribution's edges are
# visible, not only its middle: eleven points, 0 through 100 by 10.
DECILE_POINTS: tuple[int, ...] = tuple(range(0, 101, 10))


def deciles_of(values: np.ndarray) -> pl.DataFrame:
    """The eleven decile points of `values`, or eleven unanswered points where there are none.

    Split out of `antigen_count_deciles` so a second spread reports the same shape. An empty
    input still returns all eleven rows with a null value: no observations is eleven declared,
    unanswered points, never an empty frame.
    """
    if values.size == 0:
        return pl.DataFrame(
            {"decile": list(DECILE_POINTS), "value": [None] * len(DECILE_POINTS)},
            schema={"decile": pl.Int64, "value": pl.Float64},
        )
    return pl.DataFrame(
        {"decile": list(DECILE_POINTS), "value": [float(np.quantile(values, p / 100)) for p in DECILE_POINTS]}
    )


def antigen_count_deciles(counts: pl.DataFrame) -> pl.DataFrame:
    """Deciles of the total antigen count per cell barcode.

    `counts` is the sparse per-(cell, tag) frame -- one row per observed reading, columns
    sampleId, cellId, umiCount -- taken before flooring or identity-combining. A cell's
    total sums every tag it shows any reading for. A cell with no row contributes no total,
    since crediting it zero would read as a reading rather than the absence it is.

    Returns one row per decile point, columns `decile` and `value`. An empty input still
    returns all eleven rows with `value` null: no cells observed is eleven declared,
    unanswered points, never an empty frame -- the same "declared, not absent" rule the
    deferred measurements follow.
    """
    if counts.height == 0:
        return deciles_of(np.empty(0))
    totals = counts.group_by(["sampleId", "cellId"]).agg(pl.col("umiCount").sum().alias("total"))["total"].to_numpy()
    return deciles_of(totals)
