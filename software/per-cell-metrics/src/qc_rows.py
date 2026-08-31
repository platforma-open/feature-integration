"""The QC measurement set as rows, and the two report shapes read off them.

`QcRow` is the one carrier: every measurement enters through `_add`, which is what attaches
the line's verdict. `_qc_frame` is the long form keyed (level, entity, measurement).
`sample_report_rows` and `sample_summary_rows` are the two narrower reads over the same rows,
and neither recomputes a status.

A row with no number always carries a reason, and the reason is not the detail: a detail rides
alongside a number, a reason stands in place of one.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import NamedTuple

import numpy as np
import polars as pl
from qc_measures import (
    DEFAULT_LINES,
    MEASUREMENTS,
    Coverage,
    Line,
    Measurement,
    Reading,
    Status,
    deciles_of,
    is_computed,
    roll_up,
    status_for,
)
from tag_distribution import TagFits
from verdict import ReferenceChoice, specificity_score

# A rollup is reported in the same frame as the measurements it aggregates, as a row whose
# measurement is the rollup itself. A measurement is an axis value here, so a level's summary costs
# a row rather than a column.
ROLLUP = "rollup"
ROLLUP_COUNTS = "The worst status among this level's measurements, and how much of it was checked."
# The rollup has no declaration to borrow a readable name from, and a row reading `rollup` beside
# rows reading `readsPerCell` leaves the reader guessing which is which.
ROLLUP_LABEL = "Worst status at this level"

MEASUREMENT_BY_ID = {m.id: m for m in MEASUREMENTS}


class QcRow(NamedTuple):
    """One measurement at one level entity, before its declaration is attached.

    `status` and `coverage` are both carried because a measurement's own status is not recoverable
    from a coverage triple: `roll_up` reports *not evaluated* for a level with nothing judgeable in
    it, so a row computed and left unjudged would come back saying nobody looked.

    `panel_id` is set on tag-level and identity-level rows and left empty on the rest. A panel carries
    the worst status among those measurements, so those rows have to say which panel they belong to.

    `reason` is set only where `value` is None, and it is separate from `detail`: a detail is carried
    alongside a number, a reason stands in place of one.
    """

    level: str
    entity: str
    measurement: str
    value: float | None
    detail: str
    panel_id: str
    status: Status | None
    coverage: Coverage
    reason: str = ""


def _leaf(level, entity, measurement, value, detail, panel_id, status: Status | None, reason: str = "") -> QcRow:
    """One measurement's row: its own status, and the coverage of that one status.

    The triple comes from `roll_up`, so a leaf and a rollup are counted by one rule, and the
    row keeps the status `roll_up` would have flattened.
    """
    reading = Reading(status, value)
    return QcRow(level, entity, measurement, value, detail, panel_id, status, roll_up([reading]), reason)


def _qc_frame(rows: list[QcRow], lines: dict[str, Line] = DEFAULT_LINES) -> pl.DataFrame:
    """The measurement set as a frame keyed (level, entity, measurement).

    Every declared measurement keeps its place whether or not this run could compute it, and
    a measurement nothing computed reads *not evaluated* with its reason rather than being
    absent. A reader must never mistake "nothing computed this yet" for "checked and found
    fine". A field with nothing in it is written null rather than as an empty string: polars
    quotes an empty string to keep it apart from a null, and a quoted empty cell is a value a
    downstream import would carry as one.

    `lineWarn` and `lineAlert` are read from `lines`, the same dict `status_for` was given to
    produce `row.status` -- so a reader who sees `warn` can see the threshold it warned
    against. `route` is read from `Measurement.line` alone, never a second declaration of which
    measurement stands on a line. A measurement with no route gets three null fields; the
    categorical route has a route but no numeric threshold, so it gets a route and two nulls.
    """
    built = []
    for row in rows:
        declared = MEASUREMENT_BY_ID.get(row.measurement)
        route = None if declared is None else declared.line
        line = None if declared is None else lines.get(row.measurement)
        built.append(
            {
                "level": row.level,
                "entity": row.entity,
                "panelId": row.panel_id,  # "" not None: this is an AXIS key, and a null is not a usable one
                "measurement": row.measurement,
                # The readable name, carried beside the id rather than instead of it. The id
                # is a p-column axis value and must stay stable. The label is what a reader
                # who never opened this module sees.
                "label": ROLLUP_LABEL if declared is None else declared.label,
                "value": row.value,
                "detail": row.detail or None,
                # Null where no line stands behind the measurement. The reason is read from the value,
                # which is where a reader looks next anyway.
                "status": None if row.status is None else row.status.value,
                "judged": row.coverage.judged,
                "unjudged": row.coverage.unjudged,
                "notEvaluated": row.coverage.not_evaluated,
                "counts": ROLLUP_COUNTS if declared is None else declared.counts,
                "implies": None if declared is None else declared.implies,
                "lineWarn": None if line is None else line.warn,
                "lineAlert": None if line is None else line.error,
                "route": route,
                # Why this row has no number. The declaration wins: a deferred measurement's reason is the
                # same on every run, and a call site cannot restate it. Same precedence as
                # `sample_report_rows`.
                "reason": (None if declared is None else declared.deferred_reason) or row.reason or None,
            }
        )
    return pl.DataFrame(
        built,
        schema={
            "level": pl.String,
            "entity": pl.String,
            "panelId": pl.String,
            "measurement": pl.String,
            "label": pl.String,
            "value": pl.Float64,
            "detail": pl.String,
            "status": pl.String,
            "judged": pl.Int64,
            "unjudged": pl.Int64,
            "notEvaluated": pl.Int64,
            "counts": pl.String,
            "implies": pl.String,
            "lineWarn": pl.Float64,
            "lineAlert": pl.Float64,
            "route": pl.String,
            "reason": pl.String,
        },
    )


def _add(
    rows: list[QcRow],
    level: str,
    entity: str,
    measurement: str,
    value,
    detail: str = "",
    panel_id: str = "",
    reason: str = "",
    lines: dict[str, Line] = DEFAULT_LINES,
):
    """Append one measurement row, taking its status from the lines in force.

    Every declared measurement goes through here. One with no line in force carries no status, which
    is honest rather than a refusal: it was computed, no line stands behind it, so its number is shown
    and nothing is claimed.

    `lines` defaults to the shipped set; a caller threading an operator override passes its own dict,
    which then also governs what `_qc_frame` renders as `lineWarn` / `lineAlert`.

    `reason` belongs on a row with no number and is ignored on any other. A value that is not a finite
    number is not a number the caller's reason describes, so it takes its own.
    """
    rows.append(
        _leaf(
            level,
            entity,
            measurement,
            value,
            detail,
            panel_id,
            status_for(measurement, value, lines),
            "" if is_computed(value) else (reason if value is None else NOT_A_NUMBER_REASON),
        )
    )


# The two standing reasons, for the cases no call site accounts for. A deferred measurement carries
# its declaration's reason and needs neither. Of the rest, every call site that can go valueless
# states its own, so `UNSUPPLIED_REASON` covers only a declared measurement with no call site.
UNSUPPLIED_REASON = "nothing in this run supplied a value for this measurement"
# A number arrived and was not finite. Distinct from the above, which is nothing arriving: the
# caller's reason describes an input that is missing and would misname this.
NOT_A_NUMBER_REASON = "this run computed a value for this measurement that is not a finite number"


def sample_report_rows(sample: str, rows: list[QcRow]) -> tuple[list[dict], Coverage]:
    """One sample's report: every sample-level measurement, and the rollup over them.

    The walk is over `MEASUREMENTS` rather than over `rows`, so the report is the declared set: a
    measurement this run never reached takes its place carrying a reason.

    A value that is not a finite number counts as no value, by `is_computed`, which is the rule the
    coverage triple counts by.

    A measurement declaring `rolls_up=False` is listed with its own status and left out of the rollup.
    The entry carries `rollsUp`, which separates a status shown here from the tag beside the list.
    """
    by_id = {r.measurement: r for r in rows if r.level == "sample" and r.entity == sample}
    entries: list[dict] = []
    readings: list[Reading] = []
    for m in MEASUREMENTS:
        if m.level != "sample":
            continue
        row = by_id.get(m.id)
        raw = None if row is None else row.value
        value = raw if is_computed(raw) else None
        status = None if row is None or value is None else row.status
        reason = None
        if value is None:
            # Declaration first, so a call site cannot restate a deferred measurement's reason. Then the
            # row's own, which `_add` has already replaced where a non-finite number arrived.
            # `UNSUPPLIED_REASON` is left for a declared measurement with no call site.
            reason = m.deferred_reason or (row.reason if row is not None else "") or UNSUPPLIED_REASON
        entries.append(
            {
                "id": m.id,
                "label": m.label,
                "value": value,
                "detail": (row.detail if row is not None else "") or None,
                "reason": reason,
                "status": None if status is None else status.value,
                "counts": m.counts,
                "implies": m.implies,
                "rollsUp": m.rolls_up,
            }
        )
        if m.rolls_up:
            readings.append(Reading(status, value))
    return entries, roll_up(readings)


_DECILE_SCHEMA = {"distribution": pl.String, "decile": pl.Int64, "value": pl.Float64}
# Deciles of the total antigen count per cell barcode, kept PER SAMPLE rather than pooled into
# `_DECILE_SCHEMA`: this shape is one sample's own plot, and pooling it would answer a different
# question. A separate schema mints a separate p-column rather than adding a sample axis to the
# existing one.
_SAMPLE_DECILE_SCHEMA = {"sampleId": pl.String, "decile": pl.Int64, "value": pl.Float64}
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


def _decile_rows(distribution: str, deciles: pl.DataFrame) -> list[dict]:
    """One distribution's decile points as rows. A point with no value contributes none."""
    return [
        {"distribution": distribution, "decile": int(d), "value": float(v)}
        for d, v in zip(deciles["decile"], deciles["value"], strict=True)
        if v is not None
    ]


def _sample_decile_rows(sample: str, deciles: pl.DataFrame) -> list[dict]:
    """One sample's antigen-count decile points as rows. A point with no value contributes none.

    A sample with no counted reading gets all-null points from `antigen_count_deciles`, so this
    returns no rows for it -- a sample absent from the frame, not a flat line at zero.
    """
    return [
        {"sampleId": sample, "decile": int(d), "value": float(v)}
        for d, v in zip(deciles["decile"], deciles["value"], strict=True)
        if v is not None
    ]


def _sticky_measure(readings: dict[tuple[str, str], int], gate: int | None) -> tuple[float | None, str]:
    """One sample's sticky exposure, in whichever form the gate allows.

    A declared gate supplies a *high*, so the measurement is a count of the cells above it. With no gate
    there is no high, and a count against a line nobody drew would assert a boundary; the spread of the
    readings goes out instead. The value is then the median, matching the other spreads.

    Both forms are taken over the cells' own baseline readings, which only a declared baseline tag
    supplies. Over none, a gated count is 0.0 and reports a sample as checked and clean on a question the
    run never asked, while the run record reports None for the same condition. So neither form returns a
    number there, and the caller's reason goes out in place of one.
    """
    comparator_detail = f"cellsWithAComparator={len(readings)}"
    if not readings:
        return None, comparator_detail
    if gate is not None:
        return float(sum(1 for v in readings.values() if v > gate)), f"{comparator_detail}|gate={gate}"
    deciles = deciles_of(np.asarray(list(readings.values()), dtype=float))
    points = "|".join(
        f"{d}:{'' if v is None else round(v, 3)}" for d, v in zip(deciles["decile"], deciles["value"], strict=True)
    )
    middle = deciles.filter(pl.col("decile") == 50)["value"].to_list()
    return (middle[0] if middle else None), f"{comparator_detail}|noGateDeclared|{points}"


def _score_spread(states: pl.DataFrame, served: ReferenceChoice) -> tuple[float | None, str]:
    """The run's scores as deciles, or why there are none.

    Only the declared rung scores. A population baseline yields a probability, which is not on the
    same scale and cannot be pooled with a score.

    Cells carrying an `unreliableReason` are left out. Such a cell still has a number here -- the score
    is computed before the state is called -- but it answers a comparison that never happened.
    """
    if served is not ReferenceChoice.DECLARED:
        return None, f"the {served.value} baseline yields no score, so a run resting on it has no spread"
    scored = states.filter(pl.col("unreliableReason").is_null())
    if scored.height == 0:
        return None, "no cell was scored"
    values = specificity_score(
        scored["umiCount"].to_numpy(),
        np.nan_to_num(scored["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0),
    )
    deciles = deciles_of(np.asarray(values, dtype=float))
    detail = "|".join(
        f"{d}:{'' if v is None else round(v, 3)}" for d, v in zip(deciles["decile"], deciles["value"], strict=True)
    )
    middle = deciles.filter(pl.col("decile") == 50)["value"].to_list()
    return (middle[0] if middle else None), detail


def _fitted_background(tag_fits: TagFits | None, samples: Collection[str], tag: str) -> tuple[float | None, str]:
    """One tag's fitted background across a panel's samples, as a value and its detail."""
    if tag_fits is None:
        return None, "no population baseline served this run, so nothing was fitted"
    fitted = [tag_fits.backgrounds[(s, tag)] for s in sorted(samples) if (s, tag) in tag_fits.backgrounds]
    missed = [s for s in sorted(samples) if (s, tag) in tag_fits.reasons]
    if not fitted:
        why = tag_fits.reasons.get((missed[0], tag), "") if missed else "this tag was not fitted in any sample"
        return None, f"fitted in no sample of this panel: {why}"
    means = [b.mean for b in fitted]
    detail = "|".join(
        [
            f"samplesFitted={len(fitted)}",
            f"samplesUnfitted={len(missed)}",
            f"backgroundRange={min(means):.4g}..{max(means):.4g}",
            f"medianSignalMean={_median([b.signal_mean for b in fitted]):.4g}",
            f"medianBackgroundWeight={_median([b.weight for b in fitted]):.4g}",
        ]
    )
    return _median(means), detail


def _median(values: list[float]) -> float | None:
    return float(pl.Series(values).median()) if values else None


def _number(row: dict, column: str) -> float | None:
    """One field of a read-QC row, as a float, or None where it is absent or blank."""
    raw = row.get(column)
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


# A rescued share below this reads as the two sources disagreeing rather than as a quantity.
_RESCUE_TOLERANCE = -1e-9


def rescued_share(undeclared: float | None, panel_assigned: float | None) -> float | None:
    """The share of a sample's reads that correction moved from off the panel onto it.

    `undeclared` is the pre-refine tag-stat's undeclared weight over that sample's whole weight.
    `panel_assigned` is the refine-tags report's FEATURE `outputCount / inputCount`, so its
    complement is the reads that step dropped. Both are over reads matched, and the difference is
    the reads a sequence the panel never declared carried that correction snapped onto a panel entry.

    None where either side is absent. Also None where the difference comes out below
    `_RESCUE_TOLERANCE`: the two figures come from different files, and a drop count exceeding the
    undeclared count is those files disagreeing, not a negative quantity of reads.
    """
    if undeclared is None or panel_assigned is None:
        return None
    value = undeclared - (1.0 - panel_assigned)
    if value < _RESCUE_TOLERANCE:
        return None
    return max(value, 0.0)


# The sample-level measurements, in MEASUREMENTS' own declaration order -- the same walk
# `sample_report_rows` makes, so the wide pivot below reads off one declared set.
_SAMPLE_MEASUREMENTS: tuple[Measurement, ...] = tuple(m for m in MEASUREMENTS if m.level == "sample")

# Read-QC figures with no declared measurement behind them. `readsTotal`, `panelAssignedFraction`,
# `cellBarcodeValidFraction` and `cellsDetected` are declared measurements already carrying these same
# mitool figures, so they are excluded here and read from the pivot instead -- one column per figure,
# not two agreeing ones under two names.
_MITOOL_ONLY_COLUMNS: tuple[str, ...] = (
    "readsMatched",
    "matchedFraction",
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
    `status` is `sample_report`'s own rollup, from `roll_up`, never recomputed here.

    Every id in `samples` gets a row, a sample absent from `sample_report` included: its measurement
    columns and its status come back null, which reads as nothing having rolled up rather than as a
    passing sample.
    """
    built = []
    for sample in samples:
        report = sample_report.get(sample, {})
        entries = {e["id"]: e["value"] for e in report.get("measurements", [])}
        qc = read_qc.get(sample, {})
        row = {"sampleId": sample, "status": report.get("status")}
        for col in _MITOOL_ONLY_COLUMNS:
            row[col] = _number(qc, col)
        for m in _SAMPLE_MEASUREMENTS:
            row[m.id] = entries.get(m.id)
        built.append(row)
    schema = {
        "sampleId": pl.String,
        "status": pl.String,
        **{col: pl.Float64 for col in _MITOOL_ONLY_COLUMNS},
        **{m.id: pl.Float64 for m in _SAMPLE_MEASUREMENTS},
    }
    return pl.DataFrame(built, schema=schema)
