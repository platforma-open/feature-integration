"""Reading the run's input files, and writing a frame out in a fixed row order.

Every reader here reads as strings and strips, because these columns are join keys against
each other. A tag written " AAAA " on one side and "AAAA" on the other joins to nothing.
"""

from __future__ import annotations

import json
from collections.abc import Collection

import polars as pl


def _write_sorted(frame: pl.DataFrame, path: str, by: list[str]) -> None:
    """Write a frame in a fixed row order, header-only when it has no rows.

    Every frame reaching here is built with an explicit schema, so an empty one still
    carries its columns and writes a header. A consumer meeting a header-only frame knows
    the step ran and found nothing. One meeting an empty file cannot tell that from a step
    that never ran.
    """
    frame.sort(by).write_csv(path)


def _read_columns(path: str, columns: tuple[str, ...], what: str) -> pl.DataFrame:
    """Read a CSV as strings, keeping the named columns and stripping them.

    Stripped because these columns are join keys against the panel, whose reader strips
    `tag` and `sample` for the same reason. A tag written " AAAA " on one side and "AAAA"
    on the other joins to nothing, and reports the barcode as both undeclared and never
    seen.
    """
    frame = pl.read_csv(path, infer_schema_length=0)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise SystemExit(f"{what} {path!r} has no column(s) {missing}; columns are {frame.columns}")
    return frame.select([pl.col(c).str.strip_chars().fill_null("") for c in columns])


def _read_counts(path: str) -> pl.DataFrame:
    """The counts frame, with umiCount as an integer, or a curated exit naming the bad value.

    `_read_columns` reads every column as a string and fills nulls with "", so a blank cell
    and a decimal both survive to the cast. A bare `.cast` dies there as a raw polars
    traceback naming neither the file nor the column, the one thing a reader needs.

    `totalWeight` -- the post-refine tag-stat's read-weight column, gathered by
    gather-counts.tpl.tengo alongside the distinct-UMI count -- is read when the file carries
    it and left off the returned frame otherwise. Its absence means the run predates this
    column, not a bad file: `usable_read_fraction`'s caller checks for the column rather than
    crashing on it.
    """
    counts = _read_columns(path, ("sampleId", "cellId", "tag", "umiCount"), "counts file")
    umi = counts["umiCount"].cast(pl.Int64, strict=False)
    offenders = [raw for raw, cast in zip(counts["umiCount"], umi, strict=True) if cast is None]
    if offenders:
        shown = ", ".join(repr(v) for v in offenders[:5])
        raise SystemExit(
            f"counts file {path!r} has {len(offenders)} umiCount value(s) that are not whole numbers: "
            f"{shown}. A UMI count is a count of observations; a blank or a decimal is not one."
        )
    counts = counts.with_columns(umi.alias("umiCount"))
    header = pl.read_csv(path, infer_schema_length=0, n_rows=0).columns
    if "totalWeight" in header:
        weight_raw = _read_columns(path, ("totalWeight",), "counts file")["totalWeight"]
        weight = weight_raw.cast(pl.Int64, strict=False)
        weight_offenders = [raw for raw, cast in zip(weight_raw, weight, strict=True) if cast is None]
        if weight_offenders:
            shown = ", ".join(repr(v) for v in weight_offenders[:5])
            raise SystemExit(
                f"counts file {path!r} has {len(weight_offenders)} totalWeight value(s) that are not "
                f"whole numbers: {shown}"
            )
        counts = counts.with_columns(weight.alias("totalWeight"))
    return counts


def undeclared_feature_counts(raw_counts: pl.DataFrame, declared: Collection[str]) -> tuple[pl.DataFrame, float | None]:
    """Undeclared FEATURE barcodes in a pre-refine tag-stat table, and their read share.

    `raw_counts`: mitool `tag-stat -t FEATURE` (no `-u`) output, columns `FEATURE` and
    `totalWeight`, one row per distinct observed sequence. `declared`: one sample's panel
    tag set.

    Returns the undeclared rows, renamed to `tag` and sorted by it, and the share of
    every row's `totalWeight` they carry. Share is `None` over zero total weight. With
    no undeclared row the frame is empty and the share is `0.0`.
    """
    ordered = raw_counts.rename({"FEATURE": "tag"}).select("tag", "totalWeight").sort("tag")
    undeclared = ordered.filter(~pl.col("tag").is_in(set(declared)))
    total_weight = float(ordered["totalWeight"].sum()) if ordered.height else 0.0
    if total_weight <= 0:
        return undeclared, None
    undeclared_weight = float(undeclared["totalWeight"].sum()) if undeclared.height else 0.0
    return undeclared, undeclared_weight / total_weight


def _read_raw_feature_counts(path: str) -> pl.DataFrame:
    """The gathered pre-refine FEATURE tag-stat table, across every sample.

    Columns `sampleId`, `FEATURE`, `totalWeight` -- the workflow's per-sample gather step
    injects `sampleId` from the resource-map key the same way `_read_counts` documents for the
    (cell, tag) counts. Read as strings and stripped, same join-safety reason as
    `_read_columns`, then `totalWeight` cast to a whole number.
    """
    frame = _read_columns(path, ("sampleId", "FEATURE", "totalWeight"), "raw feature counts file")
    weight = frame["totalWeight"].cast(pl.Int64, strict=False)
    offenders = [raw for raw, cast in zip(frame["totalWeight"], weight, strict=True) if cast is None]
    if offenders:
        shown = ", ".join(repr(v) for v in offenders[:5])
        raise SystemExit(
            f"raw feature counts file {path!r} has {len(offenders)} totalWeight value(s) that are not "
            f"whole numbers: {shown}"
        )
    return frame.with_columns(weight.alias("totalWeight"))


def _json_arg(raw: str | None, flag: str):
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag} is not valid JSON: {exc}") from exc
