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
from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl


class Status(str, Enum):
    ACCEPTABLE = "acceptable"
    ALERTING = "alerting"
    UNJUDGED = "unjudged"
    NOT_EVALUATED = "not evaluated"


@dataclass(frozen=True)
class Coverage:
    """A level's status, and how much of it was actually checked."""

    status: Status
    judged: int
    unjudged: int
    not_evaluated: int


@dataclass(frozen=True)
class Measurement:
    id: str
    label: str
    level: str  # "sample" | "tag" | "identity"
    counts: str  # what went into it
    implies: str | None = None  # what a bad value means, where a line exists
    line: str | None = None  # which defence route backs `implies`, if any
    deferred_reason: str | None = None  # set only when nothing computes this yet


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
    # The label names the recognized fraction rather than the spec row's "usable"
    # fraction, because that is what this block computes: `qc_report._refine_assigned_fraction`
    # returns refine-tags' outputCount/inputCount, the share of reads kept after correcting
    # the barcode against the panel. The field's "usable" fraction also requires a
    # cell-associated barcode and a valid UMI, and carries a different line (0.20 against
    # this one's 0.50). The shipped p-column's description already says recognized -- only
    # this label had drifted. The id is a p-column name and must NOT be renamed.
    Measurement(
        "panelAssignedFraction",
        "Fraction of antigen reads matching the panel",
        "sample",
        "Reads whose corrected barcode is on the panel, over reads matched.",
        "A low share means most reads carry barcodes the panel never declared.",
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
    Measurement(
        "highReferenceCells",
        "Cells carrying a high reference reading",
        "sample",
        # No line: the observation line is the per-cell threshold deciding which cells to
        # count, not a defended line on the share reported. Nothing published says what
        # share of cells is too high, so the share is shown and the reader judges.
        "Cells whose reference reading is at or above the observation line.",
    ),
    Measurement(
        "perAntigen",
        "Per antigen: signal, above the line, and the median",
        "tag",
        "Per tag: cells with any reading, cells whose reading was bound, and the median count among those.",
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
# them. No line is invented -- where none of the three routes applies the measurement stays
# unjudged rather than being given a number with nothing behind it.
DEFAULT_LINES: dict[str, float] = {
    "panelAssignedFraction": 0.5,  # inherited: complement of the field's 0.50 unrecognized line
    "readsPerCell": 5_000,  # recommended-and-observed: the vendor's per-cell depth
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
# `at-most` and `alerting-at` have no member. Both are kept because each is one of the
# three readings a line can have. `alerting-at` was `declaredNeverSeen`, which now carries
# no status. The only `at-most` candidate was the undeclared-barcode fraction, which ships
# unjudged for want of a defensible line rather than a direction.
_COMPARISON: dict[str, str] = {
    "panelAssignedFraction": "at-least",
    "readsPerCell": "at-least",
}

_ORDINAL = {Status.ACCEPTABLE: 0, Status.ALERTING: 1}

_DEFERRED: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.deferred_reason)


def status_for(measurement: str, value: float | None, lines: dict[str, float]) -> Status:
    """How one measurement reads, given the lines in force.

    A deferred measurement is not evaluated whatever it is handed: nothing computes it, so
    a value reaching here is a caller's mistake and must not be laundered into a judgement
    about the run.

    Every declared measurement gets an answer. One with no line in force reads unjudged,
    which is honest: it was computed, no line stands behind it, so nothing is claimed.
    """
    # A non-finite value is treated exactly as an absent one. Every `<` and `>` against NaN
    # is False, so without this a NaN fell through to `bad = False` and read ACCEPTABLE --
    # corrupt input reading green, the one status a reader will not investigate. +inf read
    # green too, against an at-least line, and -inf happened to alert. One rule for "not a
    # finite number" is easier to defend than a rule whose answer depends on the sign.
    if measurement in _DEFERRED or value is None or not math.isfinite(value):
        return Status.NOT_EVALUATED
    if measurement not in lines:
        return Status.UNJUDGED
    line = lines[measurement]
    comparison = _COMPARISON[measurement]
    if comparison == "at-least":
        bad = value < line
    elif comparison == "at-most":
        bad = value > line
    else:
        bad = value == line
    return Status.ALERTING if bad else Status.ACCEPTABLE


def roll_up(statuses: list[Status]) -> Coverage:
    """The worst status among those that carry one, plus coverage.

    Coverage stays out of the ordinal because acceptable/alerting and not-evaluated answer
    different questions. The first says whether something is wrong, the second whether
    anybody looked. Ranked on one scale, an unchecked run becomes indistinguishable from a
    checked one.
    """
    judged = [s for s in statuses if s in _ORDINAL]
    unjudged = sum(1 for s in statuses if s is Status.UNJUDGED)
    not_evaluated = sum(1 for s in statuses if s is Status.NOT_EVALUATED)
    status = max(judged, key=lambda s: _ORDINAL[s]) if judged else Status.NOT_EVALUATED
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
        "status": Status.NOT_EVALUATED if m.deferred_reason else None,
        "reason": m.deferred_reason,
    }


def measurement_rows() -> list[dict]:
    """Every declared measurement, deferred ones included, in declaration order."""
    return [measurement_row(m) for m in MEASUREMENTS]


def per_antigen_measures(states: pl.DataFrame) -> pl.DataFrame:
    """Per tag: cells with any reading, cells whose reading was bound, and the median count among those.

    Grouped by tag, not identity: a tag's own reagent behaviour is the question, and an
    identity built from several tags would let one weak tag hide behind a stronger one.

    `states` is the tag-grain shape -- one row per (cell, tag) with an explicit reading,
    columns `tag`, `umiCount`, `state` -- not the frame tags have been combined on.

    The sparse frame is the right input: "cells with any reading" means cells with an
    observed reading, and a cell silent for this tag has no count to contribute. There is
    no asked population to complete this against, unlike a per-cell total or a
    reads-per-cell rate.
    """
    return (
        states.group_by("tag")
        .agg(
            (pl.col("umiCount") > 0).sum().alias("cellsWithSignal"),
            (pl.col("state") == "bound").sum().alias("cellsAboveTheLine"),
            pl.col("umiCount").filter(pl.col("state") == "bound").median().alias("medianAboveTheLine"),
        )
        .sort("tag")
    )


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
        return pl.DataFrame(
            {"decile": list(DECILE_POINTS), "value": [None] * len(DECILE_POINTS)},
            schema={"decile": pl.Int64, "value": pl.Float64},
        )

    totals = counts.group_by(["sampleId", "cellId"]).agg(pl.col("umiCount").sum().alias("total"))["total"].to_numpy()
    values = [float(np.quantile(totals, p / 100)) for p in DECILE_POINTS]
    return pl.DataFrame({"decile": list(DECILE_POINTS), "value": values})
