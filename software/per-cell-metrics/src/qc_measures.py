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
``verdict.py``, both levels of self-disagreement in ``combine.py``, and the read
and per-cell totals in ``qc_report.py``. This module declares the full set and
computes only what none of those already do.
"""

from __future__ import annotations

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
        # No line: the four inherited numbers atom 315 names are the usable
        # antigen-read fraction, the undeclared-barcode fraction, the aggregate-
        # barcode read fraction and barcode validity. The matched share is on
        # none of them, so nothing here says what a low one would mean.
        "Every read the parser saw, and the share matching the tag pattern.",
    ),
    Measurement(
        "panelAssignedFraction",
        "Fraction of antigen reads usable",
        "sample",
        "Reads whose corrected barcode is on the panel, over reads matched.",
        "A low share means most reads carry barcodes the panel never declared.",
        "inherited",
    ),
    # The spec's one row for saturation and reads-per-barcode covers two figures
    # with different fates in this build: reads-per-barcode can be derived from
    # counts this package already has, saturation cannot. One Measurement could
    # not carry "computed" and "deferred" at once, so the row becomes two ids
    # here, both at the row's declared level.
    Measurement(
        "sequencingSaturation",
        "Sequencing saturation",
        "sample",
        "Duplicate reads over total reads.",
        deferred_reason="needs read-level data the per-sample fan-out discards",
    ),
    Measurement(
        "readsPerBarcode",
        "Reads per barcode",
        "sample",
        "Reads matched, over barcodes observed.",
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
    Measurement(
        "undeclaredBarcodes",
        "Undeclared barcodes, and which sequences",
        "tag",
        "Barcodes the reads carry that the sample's panel does not declare, and which sequences they are.",
        "A declared-nothing barcode carrying reads means the panel file is incomplete.",
        "inherited",
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
        "Counts the floor removed, and cells left with none",
        "sample",
        "Readings the floor zeroed, and cells whose every non-reference reading was floored.",
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
    Measurement(
        "identityDisagreement",
        "Clonotype self-disagreement at an identity",
        "identity",
        "Clonotypes whose evaluable cells did not all agree, over clonotypes with two or more evaluable "
        "cells, at an identity.",
        "A high rate means the answer a scientist acts on is unstable.",
        "against-the-run",
    ),
    Measurement(
        "tagDisagreement",
        "Clonotype self-disagreement at a single tag",
        "tag",
        "The same, computed at a single tag rather than an identity. Diagnostic only: it rests on comparing "
        "each tag against the reference separately, which no verdict is built from.",
        "A tag standing clear of the others in its panel is misbehaving, whether or not the identities it feeds are.",
        "against-the-run",
    ),
    Measurement(
        "knownAnswerRecovered",
        "Whether a declared known answer came back",
        "sample",
        "The quantity recovered for a clonotype declared in advance, against what was intended.",
        deferred_reason="no input declares a known answer",
    ),
)

# The four routes a line can be defended by, and no others. `Measurement.line`
# names one of these or None, and it is the *only* declaration of which
# measurements carry a line -- the tables below are derived facts about the
# route, never a second opinion on whether a line exists. A test asserts the
# correspondence in both directions.
LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed", "against-the-run"})

# Three of the four routes put an absolute number on the measurement. The
# fourth compares the run against itself and carries no number at all; see
# `outlier_status`.
NUMERIC_LINE_ROUTES: frozenset[str] = frozenset({"inherited", "categorical", "recommended-and-observed"})

# Every line is a parameter with a shipped default, and the operator may
# override any of them. No line is invented -- where none of the four routes
# applies the measurement stays unjudged rather than being given a number with
# nothing behind it.
DEFAULT_LINES: dict[str, float] = {
    "panelAssignedFraction": 0.5,  # inherited
    "undeclaredBarcodes": 0.1,  # inherited
    "declaredNeverSeen": 0,  # categorical: alerting at zero reads
    "readsPerBarcode": 5_000,  # recommended-and-observed
}

# How each line is read. Deliberately *not* overridable: an operator moves a
# number, never a direction. Three comparisons rather than one flag, because a
# floor and a categorical fact disagree at the boundary -- `readsPerBarcode`
# alerts strictly *below* the recommendation, while `declaredNeverSeen` alerts
# *at* zero. One `<=` cannot serve both.
#
#   at-least    acceptable at or above the line, alerting strictly below
#   at-most     acceptable at or below the line, alerting strictly above
#   alerting-at alerting where the value equals the line
#
# In every case the named value satisfies the condition it names.
_COMPARISON: dict[str, str] = {
    "panelAssignedFraction": "at-least",
    "undeclaredBarcodes": "at-most",
    "declaredNeverSeen": "alerting-at",
    "readsPerBarcode": "at-least",
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
    if measurement in _DEFERRED or value is None:
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
    if value is None:
        return Status.NOT_EVALUATED
    if len(peers) < MIN_PEERS_TO_COMPARE:
        return Status.UNJUDGED
    q1, q3 = (float(q) for q in np.quantile(peers, [0.25, 0.75]))
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


def roll_up_panel(tag_statuses: list[Status], identity_statuses: list[Status]) -> Coverage:
    """A panel carries the worst status among its per-tag and per-identity measurements."""
    return roll_up([*tag_statuses, *identity_statuses])


def roll_up_capture(sample_statuses: list[Status], panel_statuses: list[Status]) -> Coverage:
    """A capture carries the worst status among every sample and every panel within it.

    Sample and panel are separate axes rather than nested: a per-tag failure is
    usually a property of the reagent across the whole run rather than of any one
    sample, and a dead reagent would otherwise mark every sample alerting. The
    two call for different actions, and nothing hides because the capture rolls
    up both.
    """
    return roll_up([*sample_statuses, *panel_statuses])


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
    against, unlike a per-cell total or a reads-per-barcode rate, both of
    which are asked-cell questions and use a densified or a whole-run count
    instead.
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


def reads_per_barcode(reads_matched: int, barcodes_observed: int) -> float | None:
    """Reads matched, over barcodes observed.

    Both counts already exist in the per-sample QC row -- `readsMatched` and
    `cellsDetected` -- so this only divides them; neither is recounted here.

    None when no barcode was observed. A rate over zero barcodes is not a
    small number, it is no number, and returning None keeps that distinct
    from a rate that was computed and happens to be zero.
    """
    if barcodes_observed <= 0:
        return None
    return reads_matched / barcodes_observed


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


def attach_alerting_identities(
    identity_measures: pl.DataFrame,
    grouping: dict[str, set[str]],
    alerting: set[str],
) -> pl.DataFrame:
    """Beside each alerting tag, the identity figures for the identities it feeds.

    Deciding which tags alert is a threshold call made elsewhere, not here;
    `alerting` names the tags a caller has already flagged. `grouping` maps a
    tag to every identity it feeds -- ordinarily one, since one tag combines
    into one identity, but kept as a set rather than a single value so a tag
    feeding more than one identity attaches beside all of them rather than
    arbitrarily one.

    A noisy reagent whose identities read steady is a reagent to replace, not
    a run to distrust; this attachment is what lets a reader tell the two
    apart, by showing both figures rather than only the tag's own.

    `identity_measures` is self-disagreement's own identity-level output: a
    `key` column holding the identity, plus its measures. Returns one row per
    (alerting tag, identity it feeds) -- so a tag feeding two identities
    produces two rows, neither dropped -- with columns `tag`, `identity`, and
    every column `identity_measures` carries besides `key`. A tag in
    `alerting` that feeds no known identity contributes no row, since there is
    no identity figure to attach beside it.
    """
    identity_columns = [c for c in identity_measures.columns if c != "key"]
    pairs = [(tag, identity) for tag in sorted(alerting) for identity in sorted(grouping.get(tag, ()))]

    if not pairs:
        return pl.DataFrame(
            schema={
                "tag": pl.String,
                "identity": pl.String,
                **{c: identity_measures.schema[c] for c in identity_columns},
            }
        )

    pair_frame = pl.DataFrame(pairs, orient="row", schema={"tag": pl.String, "identity": pl.String})
    return pair_frame.join(identity_measures.rename({"key": "identity"}), on="identity", how="left").sort(
        ["tag", "identity"]
    )
