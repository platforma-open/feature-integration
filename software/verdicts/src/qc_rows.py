"""The QC measurement set as rows, and the two report shapes read off them.

`QcRow` is the one carrier: every measurement enters through `_add`, which is what attaches the
line's verdict. `sample_report_rows` and `sample_summary_rows` are the two reads over those rows --
one sample's own report, and the across-samples table -- and neither recomputes a status.

A row with no number always carries a reason, and the reason is not the detail: a detail rides
alongside a number, a reason stands in place of one.
"""

from __future__ import annotations

from typing import NamedTuple

import polars as pl
from qc_measures import (
    DEFAULT_LINES,
    MEASUREMENTS,
    Coverage,
    Line,
    Measurement,
    Reading,
    Status,
    is_computed,
    roll_up,
    status_for,
)


class QcRow(NamedTuple):
    """One measurement at one level entity, before its declaration is attached.

    `reason` is set only where `value` is None, and it is separate from `detail`: a detail is carried
    alongside a number, a reason stands in place of one.
    """

    level: str
    entity: str
    measurement: str
    value: float | None
    detail: str
    status: Status | None
    reason: str = ""


def _add(
    rows: list[QcRow],
    level: str,
    entity: str,
    measurement: str,
    value,
    detail: str = "",
    reason: str = "",
    lines: dict[str, Line] = DEFAULT_LINES,
):
    """Append one measurement row, taking its status from the lines in force.

    Every declared measurement goes through here. One with no line in force carries no status, which
    is honest rather than a refusal: it was computed, no line stands behind it, so its number is shown
    and nothing is claimed.

    `lines` defaults to the shipped set; a caller threading an operator override passes its own dict.

    `reason` belongs on a row with no number and is ignored on any other. A value that is not a finite
    number is not a number the caller's reason describes, so it takes its own.
    """
    rows.append(
        QcRow(
            level,
            entity,
            measurement,
            value,
            detail,
            status_for(measurement, value, lines),
            "" if is_computed(value) else (reason if value is None else NOT_A_NUMBER_REASON),
        )
    )


# The two standing reasons, for the cases no call site accounts for. Every call site that can go
# valueless states its own, so `UNSUPPLIED_REASON` covers only a declared measurement with no call
# site at all.
UNSUPPLIED_REASON = "nothing in this run supplied a value for this measurement"
# A number arrived and was not finite. Distinct from the above, which is nothing arriving: the
# caller's reason describes an input that is missing and would misname this.
NOT_A_NUMBER_REASON = "this run computed a value for this measurement that is not a finite number"


def sample_report_rows(
    sample: str, rows: list[QcRow], omit: frozenset[str] = frozenset()
) -> tuple[list[dict], Coverage]:
    """One sample's report: every applicable sample-level measurement, and the rollup over them.

    The walk is over `MEASUREMENTS` rather than over `rows`, so the report is the declared set: a
    measurement this run never reached takes its place carrying a reason.

    `omit` is the exception, and it is a narrow one: measurements this run cannot ask AT ALL, as
    opposed to ones it asked and could not answer. The control-tag rows are the case -- with no
    control tag declared there is no such thing as a control reading, so a row carrying a reason
    would be answering a question nobody put. A row with a reason says "we looked and could not
    tell"; that is not what happened. Omitted rows leave the coverage triple untouched too: they are
    not unevaluated, they are not part of this run.

    A value that is not a finite number counts as no value, by `is_computed`, which is the rule the
    coverage triple counts by.
    """
    by_id = {r.measurement: r for r in rows if r.level == "sample" and r.entity == sample}
    entries: list[dict] = []
    readings: list[Reading] = []
    for m in MEASUREMENTS:
        if m.level != "sample" or m.id in omit:
            continue
        row = by_id.get(m.id)
        raw = None if row is None else row.value
        value = raw if is_computed(raw) else None
        status = None if row is None or value is None else row.status
        reason = None
        if value is None:
            # The row's own reason, which `_add` has already replaced where a non-finite number
            # arrived. `UNSUPPLIED_REASON` is left for a declared measurement with no call site.
            reason = (row.reason if row is not None else "") or UNSUPPLIED_REASON
        entries.append(
            {
                "id": m.id,
                "value": value,
                "detail": (row.detail if row is not None else "") or None,
                "reason": reason,
                "status": None if status is None else status.value,
            }
        )
        readings.append(Reading(status, value))
    return entries, roll_up(readings)


# No decile schemas. Two p-columns of eleven quantile points each used to sit here -- one pooled over
# the run, one per sample -- and nothing ever plotted either: every distribution on the run-quality page
# is drawn from the BINNED counts in `result_qc_tag_bins.json`. Eleven points suggest a shape and cannot
# show where it separates, which is the one thing those plots are read for.
_BACKGROUND_SCHEMA = {
    "sampleId": pl.String,
    "tag": pl.String,
    "backgroundMean": pl.Float64,
    "signalMean": pl.Float64,
    "backgroundWeight": pl.Float64,
}

# One row per (panelId, tag, identity). Per-tag figures repeat across a tag's identities: the frame
# is not a summary. `reason` names each figure that has no value and why, pipe-separated.
#
# Three fields carry what the quality view prints, beside the figures they are printed from: `seenIn`
# is the ratio shown in place of the two counts, and the two `...Shown` fields carry a rate or, where
# none exists, the words for why. A blank cell reads as a figure that failed to load, and single-tag
# identities are the common case. The numeric fields stay, because sorting a rate against its
# neighbours is the whole use of these two columns and a string does not sort.
_REAGENT_SCHEMA = {
    "panelId": pl.String,
    "tag": pl.String,
    "identity": pl.String,
    "samplesSeenIn": pl.Int64,
    "samplesInPanel": pl.Int64,
    "seenIn": pl.String,
    "samplesSeenInNames": pl.String,
    "samplesInPanelNames": pl.String,
    "cellsWithCount": pl.Int64,
    "cellsAboveTheLine": pl.Float64,
    "medianCountPerCell": pl.Float64,
    "siblingDisagreement": pl.Float64,
    "siblingDisagreementShown": pl.String,
    "selfDisagreement": pl.Float64,
    "selfDisagreementShown": pl.String,
    "reason": pl.String,
}

# One row per (sampleId, tag) the pre-refine pass saw and the sample's panel does not declare.
# Two shares, at two levels, and neither substitutes for the other. `barcodeShare` is this one
# sequence's weight over every pre-refine read of its sample. `readShare` and `status` are the
# SAMPLE's undeclared-read share, repeated on every one of that sample's rows and carrying no status.
# `status` reads `barcodeShare`, so it is the row's own and differs down the table. That status is the
# barcode's, never the sample's, and never rolls into a sample's. Usually there are no rows for a
# sample at all, which is the wanted outcome.
_UNDECLARED_BARCODE_SCHEMA = {
    "sampleId": pl.String,
    "tag": pl.String,
    "totalWeight": pl.Int64,
    "barcodeShare": pl.Float64,
    "readShare": pl.Float64,
    "status": pl.String,
}


def _cells_set_aside(
    readings: dict[tuple[str, str], int], gate: int | None, no_control: str
) -> tuple[float | None, str, str]:
    """How many of one sample's cells the admissibility gate set aside: (value, detail, reason).

    Only a declared gate supplies a *high*, so with none there is no number rather than a zero -- a zero
    reads as a sample checked and found free of sticky cells, on a question nobody asked. The two
    no-number cases take different reasons, because they call for different actions: declare a gate, or
    declare a control tag.
    """
    if not readings:
        return None, "", no_control
    if gate is None:
        return None, "", "no admissibility gate is declared, so no cell is set aside"
    return float(sum(1 for v in readings.values() if v > gate)), f"Gate set at {gate:,} UMIs", ""


def _median_control_reading(readings: dict[tuple[str, str], int], no_control: str) -> tuple[float | None, str, str]:
    """The middle control-tag count across one sample's cells: (value, detail, reason).

    Reported whether or not a gate is declared -- it is the number a gate is CHOSEN from, so withholding
    it until one exists withholds it from everyone who has not chosen yet.

    Every analysed cell is in `readings`, zero-filled, so the detail counts the cells carrying a NON-ZERO
    control count. That is the figure that says whether the control reagent worked at all; a count of the
    keys would just be the sample's cell count under a name that promised more.
    """
    if not readings:
        return None, "", no_control
    values = sorted(readings.values())
    nonzero = sum(1 for v in values if v > 0)
    detail = f"{nonzero:,} of {len(values):,} cells carry a non-zero control count"
    return _median([float(v) for v in values]), detail, ""


def _median(values: list[float]) -> float | None:
    return float(pl.Series(values).median()) if values else None


def _number(row: dict, column: str) -> float | None:
    """One field of a read-QC row, as a float, or None where it is absent or blank."""
    raw = row.get(column)
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


# Float rounding on a subtraction of two shares, and nothing more. This guard used to absorb a UNIT
# ERROR: the subtrahend was a share of one refine-tags step's own input while the minuend was a share
# of matched reads, so the difference came out systematically low and could go negative, and this
# returned None while the docstring blamed "the two figures come from different files". Both terms are
# over matched reads now, so a negative here really is rounding.
_RESCUE_TOLERANCE = -1e-9


def rescued_share(undeclared: float | None, feature_dropped: float | None) -> float | None:
    """The share of a sample's matched reads that correction moved from off the panel onto it.

    Both arguments are shares of the SAME denominator -- the reads that matched the read pattern --
    which is the whole reason this can be a subtraction:

    - `undeclared` is the pre-refine tag-stat's undeclared read weight over every parsed read.
    - `feature_dropped` is what the antigen-barcode step could not place on the panel, over the same
      matched-read count (`qc_report._refine_step_counts`, not that step's own input).

    An undeclared read either got corrected onto a panel entry or was dropped, so the difference is
    what was rescued. None where either side is absent: without both, the difference is not a
    quantity.
    """
    if undeclared is None or feature_dropped is None:
        return None
    value = undeclared - feature_dropped
    if value < _RESCUE_TOLERANCE:
        return None
    return max(value, 0.0)


# The sample-level measurements, in MEASUREMENTS' own declaration order -- the same walk
# `sample_report_rows` makes, so the wide pivot below reads off one declared set.
_SAMPLE_MEASUREMENTS: tuple[Measurement, ...] = tuple(m for m in MEASUREMENTS if m.level == "sample")

# Read-QC figures with no declared measurement behind them. `readsMatched`, `panelAssignedFraction` and
# `cellsDetected` are declared measurements already carrying these same mitool figures, so they are
# excluded here and read from the pivot instead -- one column per figure, not two agreeing ones under two
# names.
#
# `readsTotal` and `readsMatched` are here BECAUSE they are no longer declared: each is a COUNT that a
# declared share already carries as its numerator or denominator, and comparing read depth between
# samples is this table's job even though a per-sample row of either said nothing.
_MITOOL_ONLY_COLUMNS: tuple[str, ...] = (
    "readsTotal",
    "readsMatched",
    # The subtrahend `rescued_share` is computed from, over matched reads like every read share here. No
    # declared measurement: it is the arithmetic between the panel-assigned and rescued rows rather than
    # a figure a reader opens the report for.
    "featureDroppedShare",
    "featuresDetected",
    "totalUniqueUmis",
    "medianUmisPerCell",
)


def sample_summary_rows(
    samples: list[str],
    sample_report: dict[str, dict],
    read_qc: dict[str, dict],
) -> pl.DataFrame:
    """The across-samples QC table: one row per sample, one column per sample-level measurement.

    Pivots `sample_report` -- the same dict `main` writes to `result_qc_by_sample.json` -- rather than
    walking `MEASUREMENTS` a second time, so this table and a sample's own report cannot disagree.

    NO rollup column. `roll_up`'s result travels in `result_qc_by_sample.json` and reaches a reader as
    the Main grid's Quality tag and the heading of a sample's own Quality Checks tab. A third copy here
    said nothing those two had not already said, and it needed its own cell renderer to say it.

    Every id in `samples` gets a row, a sample absent from `sample_report` included: its measurement
    columns come back null, which reads as nothing having been computed rather than as a passing sample.
    """
    built = []
    for sample in samples:
        report = sample_report.get(sample, {})
        entries = {e["id"]: e["value"] for e in report.get("measurements", [])}
        qc = read_qc.get(sample, {})
        row = {"sampleId": sample}
        for col in _MITOOL_ONLY_COLUMNS:
            row[col] = _number(qc, col)
        for m in _SAMPLE_MEASUREMENTS:
            row[m.id] = entries.get(m.id)
        built.append(row)
    schema = {
        "sampleId": pl.String,
        **{col: pl.Float64 for col in _MITOOL_ONLY_COLUMNS},
        **{m.id: pl.Float64 for m in _SAMPLE_MEASUREMENTS},
    }
    return pl.DataFrame(built, schema=schema)
