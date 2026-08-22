"""The quality measurements a run carries.

Every measurement carries what it counts, because the reader who meets it is not
the person who chose it: a fraction with a name and no statement of what went
into the numerator gets read as whatever the name suggests, and several of these
names suggest more than they carry.

Where a line can be defended, it also carries what a bad value implies. Where
none can, it carries nothing about what a bad value would mean -- nothing is
known, so the number and its distribution are shown and the reader judges.

None carries what to do about it. Advice depends on the run, the study and what
else is available, none of which this readout knows.

A measurement this module cannot compute is declared anyway, with the reason it
cannot, so a reader never mistakes "nothing computed this yet" for "this was
checked and found fine." Most of the set is computed elsewhere in this package
and only declared here -- undeclared barcodes and declared-but-unseen tags in
``panel.py``, the floor's counts and the high-reference-cell count in
``verdict.py``, per-tag self-disagreement in ``combine.py``, and the read
and per-cell totals in ``qc_report.py``. This module declares the full set and
computes only what none of those already do.
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
        # No line. Exactly four numbers are inherited from the field, and the
        # matched share is not one of them: the usable antigen-read fraction
        # (published warn below 0.20), the undeclared-barcode fraction (warn
        # above 0.50), the aggregate-barcode read fraction (warn above 0.05)
        # and barcode validity (warn below 0.75). Nothing published says what a
        # low matched share means, so nothing here claims to.
        "Every read the parser saw, and the share matching the tag pattern.",
    ),
    # The label names the recognized fraction rather than the spec row's
    # "usable" fraction, because that is the quantity this block computes and
    # has always computed: `qc_report._refine_assigned_fraction` returns the
    # refine-tags step's outputCount/inputCount, the share of reads kept after
    # correcting the barcode against the panel. The field's "usable" fraction
    # additionally requires a cell-associated barcode and a valid UMI, and
    # carries a different published line (0.20 against this one's 0.50). The
    # shipped p-column's own description already says the recognized fraction;
    # only this label had drifted. The id is a p-column name and must not be
    # renamed -- a p-column's identity is its name, domain and axes.
    Measurement(
        "panelAssignedFraction",
        "Fraction of antigen reads matching the panel",
        "sample",
        "Reads whose corrected barcode is on the panel, over reads matched.",
        "A low share means most reads carry barcodes the panel never declared.",
        "inherited",
    ),
    # Saturation is deliberately NOT measured. The vendor's own report carries it,
    # which is why it was here, and nothing hangs on it: a scientist cannot act on
    # it for the run already collected, and whether the run was deep enough is
    # answered by reads per cell against a stated recommendation, below. A number
    # nobody acts on competes for attention with numbers they do.
    #
    # Per *cell*, not per observed barcode. The vendor's five thousand is a
    # per-cell recommendation, and in droplet data the observed-barcode count
    # exceeds the called-cell count by one to two orders of magnitude, because
    # ambient antigen reads land on most barcodes -- so dividing by it would
    # alert on a healthy library. The cell list is an input that arrives later
    # than this module, which is why the division happens in the entrypoint.
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
    # No line, and this one is worth spelling out because the spec looks like it
    # supplies one. Atom 315 lists "the fraction of undeclared barcodes" among
    # its four inherited numbers, and the field does publish 0.50 -- but for one
    # aggregate library fraction. This measurement is per sequence at tag level,
    # which is the improvement the spec set asks for, and a fraction's line does
    # not transfer to a list of sequences. Given a count instead, any at-most
    # line collapses into "alerting if a single undeclared barcode exists" -- a
    # categorical predicate wearing an inherited number. So it ships unjudged,
    # with its sequences and their counts, and says nothing about what a bad
    # value would mean.
    Measurement(
        "undeclaredBarcodes",
        "Undeclared barcodes, and which sequences",
        "tag",
        "Barcodes the reads carry that the sample's panel does not declare, and which sequences they are.",
    ),
    Measurement(
        "declaredNeverSeen",
        "Declared tags the reads never show",
        "tag",
        "Tags on the sample's panel with no reads at all.",
        "A reagent that produced nothing did not work in this run.",
        "categorical",
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
        # No line: the observation line is the per-cell threshold this
        # measurement uses to decide which cells to count, not a defended line on
        # the share it reports. Nothing in the field publishes a share of cells
        # that is too high, so the share is shown and the reader judges.
        "Cells whose reference reading is at or above the observation line.",
    ),
    Measurement(
        "perAntigen",
        "Per antigen: signal, above the line, and the median",
        "tag",
        "Per tag: cells with any reading, cells whose reading was bound, and the median count among those.",
    ),
    # Self-disagreement at an IDENTITY is deliberately not measured, and keeping
    # the tag-level figure while dropping this one rests on which confound cancels.
    # Marginal binding inflates disagreement everywhere, so comparing one tag
    # against its siblings under the same cells, the same run and the same line
    # leaves a tag that stands clear standing clear for a reason that is not
    # biology. The identity-level figure has nothing to compare against and so
    # cannot separate a faulty reagent from a panel full of weak binders: cells of
    # one clonotype only all agree where the reading sits clear of the line, so for
    # anything marginal disagreement is close to certain and the rate measures how
    # many clonotypes sit near the line. Whatever it would say about one
    # clonotype's answer is already on that verdict, in the cells that could answer
    # and the cells that bound, where a scientist is deciding about that clone.
    Measurement(
        "tagDisagreement",
        "Clonotype self-disagreement at a single tag",
        "tag",
        "The same, computed at a single tag rather than an identity. Diagnostic only: it rests on comparing "
        "each tag against the reference separately, which no verdict is built from.",
        "A tag standing clear of the others in its panel is misbehaving, whether or not the identities it feeds are.",
        "against-the-run",
    ),
    # Whether a clonotype of known specificity came back correctly is deliberately
    # NOT measured. It would be the only end-to-end check of the pipeline there is,
    # and nothing computes it because nothing declares it: no surface asks a
    # scientist which clonotype they already know the answer for. What they do
    # instead is find that clonotype in the readout and read its row, which the
    # readout already supports. Building the measurement means building the
    # declaration first.
)

# The four routes a line can be defended by, and no others. `Measurement.line`
# names one of these or None, and it is the *only* declaration of which
# measurements carry a line -- the tables below are derived facts about the
# route, never a second opinion on whether a line exists. A test asserts the
# correspondence in both directions.
LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed", "against-the-run"})

# Three of the four routes put an absolute number on the measurement. The
# fourth compares the run against itself and carries no number at all. See
# `outlier_status`.
NUMERIC_LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed"})

# Every line is a parameter with a shipped default, and the operator may
# override any of them. No line is invented -- where none of the four routes
# applies the measurement stays unjudged rather than being given a number with
# nothing behind it.
DEFAULT_LINES: dict[str, float] = {
    "panelAssignedFraction": 0.5,  # inherited: complement of the field's 0.50 unrecognized line
    "declaredNeverSeen": 0,  # categorical: alerting at zero reads
    "readsPerCell": 5_000,  # recommended-and-observed: the vendor's per-cell depth
}

# How each line is read. Deliberately *not* overridable: an operator moves a
# number, never a direction. Three comparisons rather than one flag, because a
# floor and a categorical fact disagree at the boundary -- `readsPerCell` alerts
# strictly *below* the recommendation, while `declaredNeverSeen` alerts *at*
# zero. One `<=` cannot serve both.
#
#   at-least    acceptable at or above the line, alerting strictly below
#   at-most     acceptable at or below the line, alerting strictly above
#   alerting-at alerting where the value equals the line
#
# In every case the named value satisfies the condition it names.
#
# `at-most` currently has no member. It is kept because it is one of the three
# readings a line can have, not because something uses it: the only candidate
# was the undeclared-barcode fraction, which ships unjudged for want of a
# defensible line rather than for want of a direction.
_COMPARISON: dict[str, str] = {
    "panelAssignedFraction": "at-least",
    "declaredNeverSeen": "alerting-at",
    "readsPerCell": "at-least",
}

_ORDINAL = {Status.ACCEPTABLE: 0, Status.ALERTING: 1}

_DEFERRED: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.deferred_reason)

_AGAINST_THE_RUN: frozenset[str] = frozenset(m.id for m in MEASUREMENTS if m.line == "against-the-run")


def status_for(measurement: str, value: float | None, lines: dict[str, float]) -> Status:
    """How one measurement reads, given the lines in force.

    A deferred measurement is not evaluated whatever it is handed: nothing
    computes it, so a value reaching here is a caller's mistake and must not be
    laundered into a judgement about the run.

    A measurement on the against-the-run route is refused outright rather than
    answered. It does carry a status -- one this function cannot compute, since
    the comparison is against the measurement's peers in the same panel and no
    peers are passed here. Returning `unjudged` instead would be the worst
    available answer: unjudged never enters a rollup, so an outlying reagent
    would leave its panel reading clean, which is the exact failure the rollup
    exists to invert. Call `outlier_status` for these.
    """
    if measurement in _AGAINST_THE_RUN:
        raise ValueError(
            f"{measurement!r} is judged against the run itself, not against a line: "
            "call outlier_status(value, peers) with the measurement's peers in the same panel"
        )
    # A non-finite value is treated exactly as an absent one. Every `<` and `>` against
    # NaN is False, so without this a NaN fell through to `bad = False` and the
    # measurement read ACCEPTABLE -- corrupt input reading green, which is the one status
    # a reader will not investigate. +inf read green too, against an at-least line; -inf
    # happened to alert. One rule for "not a finite number" is easier to defend than a
    # rule whose answer depends on the sign, and neither is a measurement.
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


# The interquartile fence a value must clear to count as standing apart from its
# peers. A parameter like every other line, and visible for the same reason.
DEFAULT_OUTLIER_FENCE: float = 3.0

MIN_PEERS_TO_COMPARE = 3


def outlier_status(
    value: float | None,
    peers: list[float],
    fence: float = DEFAULT_OUTLIER_FENCE,
) -> Status:
    """The fourth route: a value standing clear of its peers in the same panel.

    Needs no published number to be valid, which is the same ground the
    self-disagreement measure stands on in the first place.

    `peers` **excludes** `value` -- the other tags in the same panel, not all of
    them. Including it would let a single extreme reading inflate the upper
    quartile it is then measured against, so the one case the measure exists to
    catch is the one it would miss.

    Only high values are flagged. A disagreement rate below its peers is a tag
    behaving better than the panel, which is not a finding.

    Unjudged below `MIN_PEERS_TO_COMPARE` peers, where a quartile is not a
    distribution but an arithmetic accident of two or three numbers.
    """
    # Same rule as `status_for` for the value itself: not a finite number is not a
    # measurement, and a NaN compared against any fence is False, which read ACCEPTABLE.
    if value is None or not math.isfinite(value):
        return Status.NOT_EVALUATED
    if len(peers) < MIN_PEERS_TO_COMPARE:
        return Status.UNJUDGED
    q1, q3 = (float(q) for q in np.quantile(peers, [0.25, 0.75]))
    # A non-finite fence is a different failure from a non-finite value, and reads
    # differently. One NaN among the peers makes np.quantile return NaN quartiles, so
    # every comparison went False and the tag read ACCEPTABLE. The value here is a real
    # number and the measurement WAS computed -- what cannot be defended is the
    # distribution it would be measured against, which is what unjudged says.
    if not (math.isfinite(q1) and math.isfinite(q3)):
        return Status.UNJUDGED
    return Status.ALERTING if value > q3 + (q3 - q1) * fence else Status.ACCEPTABLE


def roll_up(statuses: list[Status]) -> Coverage:
    """The worst status among those that carry one, plus coverage.

    Coverage stays out of the ordinal because acceptable/alerting and
    not-evaluated answer different questions. The first says whether something
    is wrong; the second says whether anybody looked. Ranked on one scale, an
    unchecked run becomes indistinguishable from a checked one.
    """
    judged = [s for s in statuses if s in _ORDINAL]
    unjudged = sum(1 for s in statuses if s is Status.UNJUDGED)
    not_evaluated = sum(1 for s in statuses if s is Status.NOT_EVALUATED)
    status = max(judged, key=lambda s: _ORDINAL[s]) if judged else Status.NOT_EVALUATED
    return Coverage(status, len(judged), unjudged, not_evaluated)


# Only the sample rolls up, so `roll_up` above is the only aggregation rule here.
#
# A panel status is gone because it overestimated what could be judged
# categorically: of the per-tag measurements one is categorical and the rest are
# read only as outliers against the other tags in the same panel, which is a
# comparison rather than a severity and cannot be rolled into one without
# discarding the comparison that made it a finding. A capture status followed the
# same logic and lost its content with it -- the worst of every sample and every
# panel becomes the worst of every sample, which the samples already say.
#
# Nothing hides. A reagent finding states itself on its own per-tag row, keyed by
# the panel that has it, and a sample's own report names the measurement that set
# the sample alerting.


def measurement_row(m: Measurement) -> dict:
    """One declared measurement, rendered for a reader who never opens this module.

    A deferred measurement renders with its own reason attached and keeps its
    place in the set -- the difference between "checked and fine" and "never
    checked" is lost the moment a deferred id simply has no row.
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

    Grouped by tag, not by identity: a tag's own reagent behaviour is the
    question this answers, and an identity built from several tags would let
    one weak tag hide behind a stronger one in the same combined figure.

    `states` is the tag-grain shape -- one row per (cell, tag) with an
    explicit reading, columns `tag`, `umiCount` and `state` -- the same sparse
    frame a tag-level self-disagreement count is taken from, not the frame
    tags have already been combined into an identity on.

    The sparse frame is the right input here: "cells with any reading" means
    cells with an observed reading, and a cell silent for this tag has no
    count to contribute. There is no asked population to complete this
    against, unlike a per-cell total or a reads-per-cell rate, both of which
    are asked-cell questions and use a densified or a whole-run count instead.
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

    The denominator is the **cell list**, not the barcodes the reads happened to
    touch. The vendor's five-thousand recommendation this is judged against is
    per called cell, and in droplet data the observed-barcode count runs one to
    two orders of magnitude higher, because ambient antigen reads land on most
    barcodes. Dividing by observed barcodes would make a healthy library alert,
    which is worse than not judging depth at all: a status that fires on good
    runs teaches a reader to ignore it.

    `reads_matched` already exists in the per-sample QC row; the cell list is a
    separate input that arrives with gene expression or with the receptors, so
    the caller supplies its size. Deliberately not `cellsDetected` from that
    same row -- that is the observed-barcode count this docstring exists to
    warn against.

    None when the cell list is empty. A rate over no cells is not a small
    number, it is no number, and returning None keeps that distinct from a rate
    that was computed and happens to be zero.
    """
    if cells_in_list <= 0:
        return None
    return reads_matched / cells_in_list


# The extremes are included alongside the interior deciles so the distribution's
# edges are visible, not only its middle: eleven points, 0 through 100 by 10.
DECILE_POINTS: tuple[int, ...] = tuple(range(0, 101, 10))


def antigen_count_deciles(counts: pl.DataFrame) -> pl.DataFrame:
    """Deciles of the total antigen count per cell barcode.

    `counts` is the sparse per-(cell, tag) frame -- one row per observed
    reading, columns sampleId, cellId, umiCount -- taken before flooring or
    identity-combining, the same shape the floor itself works on. A cell's
    total sums every tag it shows any reading for; a cell with no row at all
    contributes no total, since crediting it a total of zero would read as a
    reading rather than as the absence it is.

    Returns one row per decile point, columns `decile` and `value`. An empty
    input still returns all eleven decile rows, `value` null throughout: no
    cells observed is eleven declared, unanswered points, never an empty
    frame -- the same "declared, not absent" rule the deferred measurements
    follow.
    """
    if counts.height == 0:
        return pl.DataFrame(
            {"decile": list(DECILE_POINTS), "value": [None] * len(DECILE_POINTS)},
            schema={"decile": pl.Int64, "value": pl.Float64},
        )

    totals = counts.group_by(["sampleId", "cellId"]).agg(pl.col("umiCount").sum().alias("total"))["total"].to_numpy()
    values = [float(np.quantile(totals, p / 100)) for p in DECILE_POINTS]
    return pl.DataFrame({"decile": list(DECILE_POINTS), "value": values})
