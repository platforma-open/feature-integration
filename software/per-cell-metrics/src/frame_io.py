"""Reading the run's input files, and writing a frame out in a fixed row order.

Every reader here reads as strings and strips, because these columns are join keys against
each other. A tag written " AAAA " on one side and "AAAA" on the other joins to nothing.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field

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


# Undeclared sequences kept per sample for the undeclared-barcode table. The table holds one row per
# distinct PRE-refine sequence, which is sequencing-error diversity: 10.2M per sample and 240.7M over
# the run on a measured 44-sample BEAM run, against a panel of 9. Only the share is read as a number,
# and it is computed over every row regardless of this cap.
UNDECLARED_BARCODES_KEPT = 1000

_TALLY_SCHEMA = {"tag": pl.String, "totalWeight": pl.Int64}


@dataclass(frozen=True)
class UndeclaredTally:
    """A sample's pre-refine read weight, split by whether the panel declared the sequence.

    `heaviest` holds at most `UNDECLARED_BARCODES_KEPT` undeclared rows, the heaviest by weight and
    tag-ordered within a tie so the kept set cannot vary between runs on the same input.
    `undeclared_distinct` counts every undeclared sequence, kept or not.
    """

    total_weight: int = 0
    undeclared_weight: int = 0
    undeclared_distinct: int = 0
    heaviest: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=_TALLY_SCHEMA))

    @property
    def share(self) -> float | None:
        """The undeclared share of every row's weight. `None` over zero total weight."""
        if self.total_weight <= 0:
            return None
        return self.undeclared_weight / self.total_weight

    @property
    def elided(self) -> int:
        """Undeclared sequences left out of `heaviest`."""
        return self.undeclared_distinct - self.heaviest.height


def _tally(before: UndeclaredTally, rows: pl.DataFrame, declared: Collection[str], keep: int | None) -> UndeclaredTally:
    """`before` extended by `rows`, one slice of one sample's pre-refine table.

    `rows`: columns `FEATURE` and `totalWeight`, one row per distinct observed sequence.
    `declared`: that sample's panel tag set. `keep`: cap on `heaviest`, or `None` to keep every
    undeclared row.
    """
    ordered = rows.rename({"FEATURE": "tag"}).select("tag", "totalWeight")
    undeclared = ordered.filter(~pl.col("tag").is_in(set(declared)))
    heaviest = pl.concat([before.heaviest, undeclared])
    if keep is not None:
        heaviest = heaviest.sort(["totalWeight", "tag"], descending=[True, False]).head(keep)
        # Rebuilt, not kept as the head of the sorted frame. That head shares the frame's string
        # buffers, so retaining it across batches retains every batch it was cut from: measured 1.50 GB
        # against 0.50 GB over 44M rows, and growing with the file either way. `rechunk` does not
        # release them; only a copy through Python does.
        heaviest = pl.DataFrame({c: heaviest[c].to_list() for c in heaviest.columns}, schema=_TALLY_SCHEMA)
    return UndeclaredTally(
        total_weight=before.total_weight + int(ordered["totalWeight"].sum() or 0),
        undeclared_weight=before.undeclared_weight + int(undeclared["totalWeight"].sum() or 0),
        undeclared_distinct=before.undeclared_distinct + undeclared.height,
        heaviest=heaviest,
    )


def undeclared_feature_counts(raw_counts: pl.DataFrame, declared: Collection[str]) -> tuple[pl.DataFrame, float | None]:
    """Undeclared FEATURE barcodes in a pre-refine tag-stat table, and their read share.

    `raw_counts`: mitool `tag-stat -t FEATURE` (no `-u`) output, columns `FEATURE` and
    `totalWeight`, one row per distinct observed sequence. `declared`: one sample's panel
    tag set.

    Returns the undeclared rows, renamed to `tag` and sorted by it, and the share of
    every row's `totalWeight` they carry. Share is `None` over zero total weight. With
    no undeclared row the frame is empty and the share is `0.0`.

    Uncapped, and the whole frame at once. `raw_feature_summary` is the production path.
    """
    tally = _tally(UndeclaredTally(), raw_counts, declared, keep=None)
    return tally.heaviest.sort("tag"), tally.share


def raw_feature_summary(
    path: str,
    declared_by_sample: dict[str, Collection[str]],
    keep: int | None = UNDECLARED_BARCODES_KEPT,
) -> dict[str, UndeclaredTally]:
    """One batched pass over the gathered pre-refine FEATURE table: a tally per sample.

    Batched rather than read whole. The table carries one row per distinct pre-refine sequence per
    sample and cost 187 B/row measured, so a 240.7M-row run needs ~45 GB to hold it -- more than the
    step is ever granted, and every row of it is read per sample and then dropped.

    A sample absent from the file gets no entry. A sample whose rows span batches accumulates across
    them: every field of the tally is additive, and `heaviest` is re-capped on each extension.
    """
    reader = pl.read_csv_batched(
        path,
        columns=["sampleId", "FEATURE", "totalWeight"],
        schema_overrides={"sampleId": pl.String(), "FEATURE": pl.String(), "totalWeight": pl.Int64()},
        ignore_errors=True,
    )
    tallies: dict[str, UndeclaredTally] = {}
    while (batches := reader.next_batches(4)) is not None:
        for batch in batches:
            if batch["totalWeight"].null_count():
                _name_the_bad_values(path, "raw feature counts file", ["totalWeight"], {})
            stripped = batch.select(
                pl.col("sampleId").str.strip_chars().fill_null(""),
                pl.col("FEATURE").str.strip_chars().fill_null(""),
                pl.col("totalWeight"),
            )
            for (sample,), rows in stripped.group_by("sampleId"):
                tallies[sample] = _tally(
                    tallies.get(sample, UndeclaredTally()),
                    rows,
                    declared_by_sample.get(sample, ()),
                    keep,
                )
    return tallies


def _json_arg(raw: str | None, flag: str):
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag} is not valid JSON: {exc}") from exc
