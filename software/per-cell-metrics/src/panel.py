"""The panel file as a (tag, sample) table.

The panel is authoritative and cannot be checked against its subject, so it is
checked against the reads in both directions instead — per sample, because the
same barcode can carry a different antigen in a different sample's panel and a
global check would let a barcode undeclared in one sample pass on another's
declaration.

A tag is the barcode sequence. The feature name is a declared property, not an
identity: a name only travels where every row for that tag agrees on it.
"""

from __future__ import annotations

import polars as pl

# Stands for "every sample" when the panel carries no sample column. The unkeyed
# case is this rule with the sample component constant, not a separate rule.
ANY_SAMPLE = "*"


def read_panel(csv_path: str, roles: dict[str, str]) -> tuple[pl.DataFrame, list[int]]:
    raw = pl.read_csv(csv_path, infer_schema_length=0)
    barcode_col, sample_col = roles["barcode"], roles.get("sample") or ""

    for name, col in (("barcode", barcode_col), ("feature", roles["feature"])):
        if col not in raw.columns:
            raise SystemExit(f"panel file has no {name} column {col!r}; columns are {raw.columns}")
    if sample_col and sample_col not in raw.columns:
        raise SystemExit(f"panel file has no sample column {sample_col!r}; columns are {raw.columns}")

    # "_row" joins "tag" and "sample" as names this function owns; colliding
    # with one is a raw polars DuplicateError ten lines later otherwise. But a
    # ROLE column may legitimately be called "tag" or "sample" — emit_panel.py
    # in this same package defaults --tag-col to "tag" — and it cannot collide,
    # because alias() replaces a same-named source column and the role columns
    # are excluded from the carry-through below. "_row" stays unconditional: it
    # is injected, so any source column of that name really does collide.
    role_cols = {barcode_col, sample_col} - {""}
    reserved = ({"tag", "sample"} & (set(raw.columns) - role_cols)) | ({"_row"} & set(raw.columns))
    if reserved:
        raise SystemExit(
            f"panel file uses reserved column name(s) {sorted(reserved)}; rename them. "
            "'tag' and 'sample' are what this reader produces."
        )

    # fill_null is load-bearing, not defensive: under infer_schema_length=0 a
    # bare empty field parses to null while a quoted one parses to "", so the
    # two spellings of blank would otherwise take different branches below.
    panel = raw.with_row_index("_row").with_columns(pl.col(barcode_col).str.strip_chars().fill_null("").alias("tag"))
    panel = panel.with_columns(
        pl.col(sample_col).str.strip_chars().fill_null("").alias("sample")
        if sample_col
        else pl.lit(ANY_SAMPLE).alias("sample")
    )

    # Blank barcodes are separated FIRST, and the order is the whole point.
    # polars materializes a trailing blank line as a real all-null row, so a
    # panel whose only flaw is a stray newline at EOF would otherwise die on
    # the blank-sample check below — telling the user to remove a sample column
    # that is not the problem. An empty line is a dropped row, not an ambiguous
    # cell. Blank barcodes are returned rather than filtered away: dropping a
    # malformed row silently is the failure the no-silent-drop rule exists to
    # prevent, and worse, because nothing downstream can tell the panel was short.
    dropped = [r + 2 for r in panel.filter(pl.col("tag") == "")["_row"]]
    panel = panel.filter(pl.col("tag") != "")

    # A blank sample cell on a row that IS otherwise real is fatal, never
    # ANY_SAMPLE. "*" means the panel declares no sample dimension at all;
    # reading an empty cell that way would widen one malformed row into a claim
    # over every sample in the run.
    if sample_col:
        blank_sample = panel.filter(pl.col("sample") == "")
        if blank_sample.height:
            rows = ", ".join(str(r + 2) for r in blank_sample["_row"])
            raise SystemExit(
                f"panel file has a blank {sample_col!r} on line(s) {rows}. Leave the column out "
                "entirely to declare one panel over every sample; a blank cell is ambiguous."
            )

    panel = panel.drop("_row")

    dupes = panel.group_by(["tag", "sample"]).len().filter(pl.col("len") > 1).sort(["tag", "sample"])
    if dupes.height:
        offenders = ", ".join(f"{t}/{s}" for t, s in zip(dupes["tag"], dupes["sample"], strict=True))
        raise SystemExit(
            f"panel file declares the same barcode twice for one sample: {offenders}. "
            "Each (barcode, sample) pair must appear once."
        )

    drop = {barcode_col} | ({sample_col} if sample_col else set())
    kept = panel.select(["tag", "sample"] + [c for c in raw.columns if c not in drop])
    return kept, dropped


def property_columns(panel: pl.DataFrame) -> list[str]:
    return [c for c in panel.columns if c not in ("tag", "sample")]


def consistent_properties(
    panel: pl.DataFrame, columns: list[str]
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str, list[str]]]]:
    """Per tag, the properties holding one value across all its rows.

    Disagreements are returned rather than dropped. With barcode reuse across
    panels an inconsistent declaration is the expected case, and dropping it
    silently would break the panel file's own no-silent-drop rule.
    """
    props: dict[str, dict[str, str]] = {}
    inconsistent: list[tuple[str, str, list[str]]] = []
    for tag, rows in panel.group_by("tag", maintain_order=True):
        name = tag[0] if isinstance(tag, tuple) else tag
        props[name] = {}
        for col in columns:
            values = sorted({v.strip() for v in rows[col].to_list() if v and v.strip()})
            if len(values) == 1:
                props[name][col] = values[0]
            elif len(values) > 1:
                inconsistent.append((name, col, values))
    return props, inconsistent
