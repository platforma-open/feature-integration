"""The panel file as a (tag, sample) table.

The panel is authoritative and cannot be checked against its subject, so it is
checked against the reads in both directions instead — per sample, because the
same barcode can carry a different antigen in a different sample's panel and a
global check would let a barcode undeclared in one sample pass on another's
declaration.

A tag is the barcode sequence. The feature name is a declared property, not an
identity: a name only travels where every row for that tag agrees on it.

An identity is a group of tags asked as one question. The universe is every
identity a verdict row exists at; offered is the subset a given set of cells
was actually presented. The universe always contains offered — and that gap is
where "never asked" lives.
"""

from __future__ import annotations

from typing import NamedTuple

import polars as pl

# Stands for "every sample" when the panel carries no sample column. The unkeyed
# case is this rule with the sample component constant, not a separate rule.
ANY_SAMPLE = "*"


class Panel(NamedTuple):
    frame: pl.DataFrame
    dropped_lines: list[int]


def _csv_line(row_index: int) -> int:
    """1-based CSV record ordinal, header counted. Not the physical line number:
    the two differ when a quoted field contains a newline."""
    return row_index + 2


def read_panel(csv_path: str, roles: dict[str, str]) -> Panel:
    """Read the panel CSV into a (tag, sample) table.

    Returns the table and the CSV lines dropped for having a blank barcode.
    Those line numbers are 1-based record ordinals with the header counted;
    they are not physical line numbers when a quoted field contains a newline.

    Normalisation is asymmetric on purpose: "tag" and "sample" are stripped,
    because they are keys; property columns are carried through exactly as
    written. consistent_properties() is the accessor that normalises them, so
    reading a property column directly can yield " AgA " and "AgA" as two
    distinct values.

    Compare emit_feature_properties.py, which consolidates the same file's
    properties by feature NAME with first-non-empty-wins and no sample
    dimension. That is the global-check failure this module exists to avoid;
    the two rules coexist today and a caller must choose knowingly.
    """
    raw = pl.read_csv(csv_path, infer_schema_length=0)
    barcode_col, sample_col = roles["barcode"], roles.get("sample") or ""

    for name, col in (("barcode", barcode_col), ("feature", roles["feature"])):
        if col not in raw.columns:
            raise SystemExit(f"panel file has no {name} column {col!r}; columns are {raw.columns}")
    if sample_col and sample_col not in raw.columns:
        raise SystemExit(f"panel file has no sample column {sample_col!r}; columns are {raw.columns}")

    # Two roles on one column silently makes "sample" a copy of "tag" — the
    # barcode alias below runs first and overwrites it, so the sample
    # expression then reads barcodes: per-sample keying gone, with no error and
    # no duplicate to catch it. The name-vs-role guard further down does not
    # catch this either, because it is name-independent and reproduces with
    # any column name. Reachable from the UI today — the Sample-column
    # dropdown is unfiltered.
    bound = [("barcode", barcode_col), ("feature", roles["feature"])]
    if sample_col:
        bound.append(("sample", sample_col))
    seen: dict[str, str] = {}
    for role, col in bound:
        if col in seen:
            raise SystemExit(
                f"panel file roles {seen[col]!r} and {role!r} both name column {col!r}; "
                "each role needs a column of its own."
            )
        seen[col] = role

    # A role column may be named after the column IT ITSELF produces, and after
    # nothing else. emit_panel.py in this package defaults --tag-col to "tag",
    # so a barcode column called "tag" must stay legal — alias() replaces the
    # same-named source column rather than duplicating it.
    #
    # The exclusion cannot be widened to "bound to any role". A SAMPLE column
    # named "tag" is fatal precisely because the barcode alias runs first and
    # overwrites it, so the sample expression then reads barcodes and "sample"
    # silently becomes a copy of "tag" — per-sample keying, the load-bearing
    # property of this whole design, collapsing with no error and no duplicate
    # to catch it. The mirror (a barcode column named "sample") happens to
    # produce correct output today, but only because the barcode alias is
    # applied before the sample alias; it is refused rather than left resting
    # on that.
    reserved = set()
    if "tag" in raw.columns and barcode_col != "tag":
        reserved.add("tag")
    if "sample" in raw.columns and sample_col != "sample":
        reserved.add("sample")
    if "_row" in raw.columns:  # injected, so any source column of that name collides
        reserved.add("_row")
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
    dropped = [_csv_line(r) for r in panel.filter(pl.col("tag") == "")["_row"]]
    panel = panel.filter(pl.col("tag") != "")

    # A blank sample cell on a row that IS otherwise real is fatal, never
    # ANY_SAMPLE. "*" means the panel declares no sample dimension at all;
    # reading an empty cell that way would widen one malformed row into a claim
    # over every sample in the run.
    if sample_col:
        blank_sample = panel.filter(pl.col("sample") == "")
        if blank_sample.height:
            rows = ", ".join(str(_csv_line(r)) for r in blank_sample["_row"])
            raise SystemExit(
                f"panel file has a blank {sample_col!r} on line(s) {rows}. Leave the column out "
                "entirely to declare one panel over every sample; a blank cell is ambiguous."
            )

        # ANY_SAMPLE is what this reader writes when there is no sample column,
        # not a sample name a caller can declare. Accepting it in an explicit
        # sample column would let one row claim every sample; downstream, a
        # frame mixing "*" with real sample names is exactly what turns the
        # panel-versus-reads check blind.
        star = panel.filter(pl.col("sample") == ANY_SAMPLE)
        if star.height:
            rows = ", ".join(str(_csv_line(r)) for r in star["_row"])
            raise SystemExit(
                f"panel file has the literal {ANY_SAMPLE!r} in column {sample_col!r} on line(s) "
                f"{rows}. Leave the column out entirely to declare one panel over every sample; "
                f"{ANY_SAMPLE!r} is what this reader writes when there is no sample column, not a "
                "sample name you can use."
            )

    panel = panel.drop("_row")

    dupes = panel.group_by(["tag", "sample"]).len().filter(pl.col("len") > 1).sort(["tag", "sample"])
    if dupes.height:
        offenders = ", ".join(f"{t}/{s}" for t, s in zip(dupes["tag"], dupes["sample"], strict=True))
        raise SystemExit(
            f"panel file declares the same barcode twice for one sample: {offenders}. "
            "Each (barcode, sample) pair must appear once."
        )

    role_cols = {barcode_col} | ({sample_col} if sample_col else set())
    kept = panel.select(["tag", "sample"] + [c for c in raw.columns if c not in role_cols])
    return Panel(kept, dropped)


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
        (name,) = tag
        props[name] = {}
        for col in columns:
            values = sorted({v.strip() for v in rows[col].to_list() if v and v.strip()})
            if len(values) == 1:
                props[name][col] = values[0]
            elif len(values) > 1:
                inconsistent.append((name, col, values))
    return props, inconsistent


# tag -> the identity that tag belongs to. Many tags may share one identity.
# A tag absent from the mapping gets no verdict row, which is why every builder
# must leave the reference tags out: the comparator has nothing to be compared
# against. identity_universe() takes no reference_tags of its own, deliberately —
# one place decides, so the two cannot drift.
Grouping = dict[str, str]


def default_grouping(panel: pl.DataFrame, reference_tags: set[str]) -> Grouping:
    """One identity per tag, over non-reference tags.

    The feature name cannot key an identity: the same barcode carries a
    different name in a different sample's panel, so name-keying splits one
    reagent and can merge two. The reference is a comparator and never an
    identity — a verdict is a reading against the reference, so asking one of
    the reference would compare it with itself.
    """
    return {t: t for t in panel["tag"].unique().to_list() if t not in reference_tags}


def identity_universe(panel: pl.DataFrame, grouping: Grouping) -> set[str]:
    """Every identity a question is asked at — the row set for every set's verdicts.

    A verdict exists at every identity, including ones a given set was never
    offered: that is precisely where *never asked* lives. Using the offered set
    as the row set instead makes an unoffered identity vanish from the answer.
    """
    return {grouping[t] for t in panel["tag"].unique().to_list() if t in grouping}


def offered_identities(panel: pl.DataFrame, grouping: Grouping, samples: list[str]) -> set[str]:
    """Which identities a set was offered, given the samples its cells came from.

    An identity was offered when any one of its tags was on any of those
    samples' panels. The `any` is deliberate: an identity is a group of tags,
    and that group can span several panels.

    A sample the panel never mentions is offered nothing, so every identity
    reads *never asked* for a set drawn from it. That is the honest reading of
    a panel that does not cover the run, and the panel-versus-reads check is
    what makes the gap visible rather than leaving it to be inferred.
    """
    rows = panel.filter((pl.col("sample") == ANY_SAMPLE) | pl.col("sample").is_in(samples))
    return {grouping[t] for t in rows["tag"].unique().to_list() if t in grouping}


def panel_read_mismatch(panel: pl.DataFrame, seen: pl.DataFrame) -> pl.DataFrame:
    """Both directions of the panel-versus-reads check, per sample.

    Neither direction can be known before the reads are processed, so by the
    time either is known the reading exists; withholding it then would turn a
    partial answer into none. This reports and never raises.

    Per sample, because the same barcode can carry a different antigen in a
    different sample's panel: a global check lets a barcode undeclared in one
    sample pass on another sample's declaration.
    """
    # A row with no sample or no barcode cannot be placed on either side of the
    # comparison, and a null key is not a usable p-column key. Dropping them
    # keeps the promise that this check never raises.
    seen = seen.filter(pl.col("sampleId").is_not_null() & pl.col("tag").is_not_null())

    rows = []
    global_panel = panel.filter(pl.col("sample") == ANY_SAMPLE)

    # All rows, not any: a frame mixing "*" with real sample names must not take
    # the global branch, which would discard every named row and report a
    # per-sample disagreement as agreement. The reader refuses such a frame, so
    # this is the second line of defence for a caller that builds one directly.
    if panel.height and global_panel.height == panel.height:
        declared = set(global_panel["tag"].to_list())
        observed = set(seen["tag"].to_list())
        for tag in sorted(declared - observed):
            rows.append({"sample": ANY_SAMPLE, "tag": tag, "direction": "declared-never-seen"})
        for tag in sorted(observed - declared):
            rows.append({"sample": ANY_SAMPLE, "tag": tag, "direction": "undeclared-in-panel"})
    else:
        samples = sorted(set(panel["sample"].to_list()) | set(seen["sampleId"].to_list()))
        for sample in samples:
            declared = set(panel.filter(pl.col("sample") == sample)["tag"].to_list())
            observed = set(seen.filter(pl.col("sampleId") == sample)["tag"].to_list())
            for tag in sorted(declared - observed):
                rows.append({"sample": sample, "tag": tag, "direction": "declared-never-seen"})
            for tag in sorted(observed - declared):
                rows.append({"sample": sample, "tag": tag, "direction": "undeclared-in-panel"})

    return pl.DataFrame(rows, schema={"sample": pl.String, "tag": pl.String, "direction": pl.String}).sort(
        ["sample", "direction", "tag"]
    )
