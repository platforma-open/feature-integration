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

    Every frame reaching here is built with an explicit schema, so an empty one still carries its
    columns and writes a header. A consumer meeting a header-only frame knows the step ran and found
    nothing. One meeting an empty file cannot tell that from a step that never ran.
    """
    frame.sort(by).write_csv(path)


def _header(path: str) -> list[str]:
    """The CSV's column names, without reading a data row."""
    return pl.read_csv(path, infer_schema_length=0, n_rows=0).columns


def _name_the_bad_values(path: str, what: str, columns: list[str], tails: dict[str, str]) -> None:
    """Exit naming the file, the column and up to five values that are not whole numbers.

    Re-reads the named columns as strings. Off the fast path: reached only once a null has been seen
    in a column declared as a number, and it always exits.
    """
    raw = pl.read_csv(path, columns=columns, infer_schema_length=0)
    for column in columns:
        values = raw[column].fill_null("")
        bad = values.filter(values.cast(pl.Int64, strict=False).is_null())
        if bad.len():
            shown = ", ".join(repr(v) for v in bad.head(5).to_list())
            raise SystemExit(
                f"{what} {path!r} has {bad.len()} {column} value(s) that are not whole numbers: "
                f"{shown}{tails.get(column, '')}"
            )


def _read_columns(path: str, columns: tuple[str, ...], what: str) -> pl.DataFrame:
    """Read a CSV as strings, keeping the named columns and stripping them.

    Stripped because these columns are join keys against the panel, whose reader strips
    `tag` and `sample` for the same reason. A tag written " AAAA " on one side and "AAAA"
    on the other joins to nothing, and reports the barcode as both undeclared and never
    seen.
    """
    return _read_typed(path, what, columns, ())


def _read_typed(
    path: str,
    what: str,
    keys: tuple[str, ...],
    numbers: tuple[str, ...],
    tails: dict[str, str] | None = None,
) -> pl.DataFrame:
    """The named columns in one pass: `keys` as stripped strings, `numbers` as whole numbers.

    Projected and typed at parse time. Reading every column as a string and selecting afterwards
    costs the file's whole width in transient buffers, and stripping a numeric column costs a second
    copy of it -- together 5.5x the retained frame on a 1.8 GB counts file, against 3.2x here.

    `ignore_errors` turns a value that is not a whole number into a null rather than a polars
    traceback naming neither the file nor the column. Only `numbers` can produce one: `keys` are
    pinned to String, which never fails to parse.
    """
    present = _header(path)
    missing = [c for c in (*keys, *numbers) if c not in present]
    if missing:
        raise SystemExit(f"{what} {path!r} has no column(s) {missing}; columns are {present}")
    types: dict[str, pl.DataType] = {c: pl.String() for c in keys}
    types.update({c: pl.Int64() for c in numbers})
    frame = pl.read_csv(path, columns=[*keys, *numbers], schema_overrides=types, ignore_errors=True)
    unparsed = [c for c in numbers if frame[c].null_count()]
    if unparsed:
        _name_the_bad_values(path, what, unparsed, tails or {})
    return frame.select(
        *(pl.col(c).str.strip_chars().fill_null("") for c in keys),
        *(pl.col(c) for c in numbers),
    )


_UMI_COUNT_TAIL = ". A UMI count is a count of observations; a blank or a decimal is not one."


def _read_counts(path: str) -> pl.DataFrame:
    """The counts frame, with umiCount as an integer, or a curated exit naming the bad value.

    `totalWeight` -- the post-refine tag-stat's read-weight column -- is read when the file carries it
    and left off the returned frame otherwise. Its absence means the run predates this column, not a
    bad file.
    """
    numbers = ("umiCount", "totalWeight") if "totalWeight" in _header(path) else ("umiCount",)
    return _read_typed(
        path,
        "counts file",
        ("sampleId", "cellId", "tag"),
        numbers,
        {"umiCount": _UMI_COUNT_TAIL},
    )


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

    Columns `sampleId`, `FEATURE`, `totalWeight` -- the workflow's per-sample gather step injects
    `sampleId` from the resource-map key. Read as strings and stripped, same join-safety reason as
    `_read_columns`, then `totalWeight` cast to a whole number.
    """
    return _read_typed(path, "raw feature counts file", ("sampleId", "FEATURE"), ("totalWeight",))


def _json_arg(raw: str | None, flag: str):
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag} is not valid JSON: {exc}") from exc
