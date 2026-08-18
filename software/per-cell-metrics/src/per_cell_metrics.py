"""Per-cell feature metrics for the Feature Integration block.

Collapses mitool tag-stat output into a (cell x feature) UMI matrix, then computes within-cell
fractions and a per-cell summary of that matrix.

The math functions are pure and unit-tested; the CLI wires them to CSV I/O. Every output is sorted
before writing: stable row order makes the CLI deterministic and keeps the workflow's pure-template
dedup canonical.
"""

import argparse
import csv
import sys

import polars as pl


def combine_barcode_counts(
    barcode_umi: dict[str, float],
    barcode_to_feature: dict[str, str],
    feature_barcodes: dict[str, set[str]],
    feature_modes: dict[str, str],
    min_umi: float = 1.0,
) -> dict[str, float]:
    """Collapse ONE cell's per-barcode UMI counts into per-feature counts, honouring each feature's
    combine mode. This is the pure rule the vectorized ``_load`` path mirrors (an oracle test pins them).

    An antigen may be read out by more than one feature barcode (e.g. a dual-labeled probe). Two modes:

    - ``"sum"`` (OR, the default): the feature's UMI is the sum of its member barcodes present in the
      cell; the feature is called whenever at least one member barcode has signal. This is the historical
      behaviour (barcodes sharing a feature name are summed).
    - ``"all"`` (AND): the feature is called ONLY when EVERY member barcode fired — each is present with
      ``umi >= min_umi`` in this cell — and its UMI is then the sum of the members. If any member is
      missing or below ``min_umi`` the feature is absent for this cell (omitted, not zero), so it does not
      take a fraction of that cell's signal. This expresses the LIBRA-seq / dual-probe design where a cell
      is antigen-specific only when both probe barcodes fire.

    ``barcode_umi`` holds only the barcodes with signal in this cell (mitool tag-stat emits count>0 rows).
    Off-panel barcodes (absent from ``barcode_to_feature``) are ignored, mirroring the inner join.
    Returns ``{feature: umi}`` for the features called present in this cell.
    """
    present: dict[str, dict[str, float]] = {}
    for bc, umi in barcode_umi.items():
        feat = barcode_to_feature.get(bc)
        if feat is None:
            continue  # off-panel barcode — ignored, mirrors the tag->feature inner join
        present.setdefault(feat, {})[bc] = umi
    out: dict[str, float] = {}
    for feat, bc_umis in present.items():
        if feature_modes.get(feat, "sum") == "all":
            members = feature_barcodes[feat]
            # Every member must be PRESENT in this cell and clear min_umi. Testing presence explicitly
            # (rather than bc_umis.get(bc, 0.0) >= min_umi) keeps AND correct at min_umi == 0, where a
            # 0.0 default would otherwise let an absent barcode "fire" — matching the vectorized _load
            # path, which never sees absent barcodes because they drop out of the inner join.
            if all(bc in bc_umis and bc_umis[bc] >= min_umi for bc in members):
                out[feat] = sum(bc_umis.values())
            # else: not every member fired -> feature not called in this cell (omitted)
        else:  # "sum" / OR
            out[feat] = sum(bc_umis.values())
    return out


def with_fraction(counts: pl.DataFrame) -> pl.DataFrame:
    """Add the within-cell UMI ``fraction`` (each feature's share of its cell's total; sums to 1 per
    cell) to the (sampleId, cellId, feature, umiCount) long frame. An empty frame carries its schema
    through. Computed once in main() and reused for both the exported fractions CSV and the per-cell
    summary so the two never diverge or recompute the window.

    A cell whose every count is zero has a zero total, and the bare division makes its fractions NaN.
    That NaN is not contained: it reaches the exported fractions CSV as a float nothing downstream
    expects, and in `per_cell_summary` it falls past the "<1%" guard -- which requires umiCount > 0 --
    into a cast to Int64 that raises and kills the whole CLI with a raw traceback. Zero is the honest
    reading: a feature holding none of a cell's signal holds none of it whatever the total.

    Real input cannot produce the case: `mitool tag-stat` emits count>0 rows only, and both combine
    modes sum over barcodes that are present, so every emitted feature has a positive count. The guard
    is here for the hand-fed CSV -- this CLI is driven by hand during verification -- and for any future
    counts source that does not share tag-stat's guarantee."""
    total = pl.col("umiCount").sum().over(["sampleId", "cellId"])
    return counts.with_columns(pl.when(total > 0).then(pl.col("umiCount") / total).otherwise(0.0).alias("fraction"))


def per_cell_summary(per_cell: pl.DataFrame) -> pl.DataFrame:
    """One row per (sampleId, cellId): the cell's max feature UMI count and max feature fraction, plus a
    ``featureSummary`` string that lists every feature the cell has signal for as
    ``feature (fraction%, umiCount UMI)``, bullet-separated and sorted by descending fraction (largest
    share first, feature name as tie-break). Fractions display as whole percents, with "<1%" for a
    nonzero feature that rounds below 1%.

    This is a TABLE-ONLY collapse of the (cell x feature) matrix -- the per-feature abundance and
    fractions outputs (the per-cell export contract) are unaffected. ``per_cell`` is the (sampleId,
    cellId, feature, umiCount) long frame ALREADY carrying the ``fraction`` column that main() computed
    once for the exported CSVs -- so the per-cell maxima can never diverge from the exported columns, and
    the fraction window is not recomputed here. An empty frame carries its schema through to a
    header-only summary.
    """
    # Whole-percent display of the fraction, with "<1%" for a nonzero feature that rounds below 1% (so a
    # real-but-tiny signal never reads as "0%"). Full-precision fractions stay in the exported columns.
    pct = (pl.col("fraction") * 100).round(0)
    pct_str = (
        pl.when((pct == 0) & (pl.col("umiCount") > 0))
        .then(pl.lit("<1%"))
        .otherwise(pct.cast(pl.Int64).cast(pl.Utf8) + pl.lit("%"))
    )
    per_cell = per_cell.with_columns(
        pl.format(
            "{} ({}, {} UMI)",
            pl.col("feature"),
            pct_str,
            pl.col("umiCount"),
        ).alias("_entry")
    )
    aggs = [
        pl.col("umiCount").max().alias("maxUmiCount"),
        pl.col("fraction").max().alias("maxFraction"),
        pl.col("_entry")
        .sort_by(["fraction", "feature"], descending=[True, False])
        # comma-separated, largest share first.
        .str.join(", ")
        .alias("featureSummary"),
    ]
    out_cols = ["sampleId", "cellId", "maxUmiCount", "maxFraction", "featureSummary"]
    return per_cell.group_by(["sampleId", "cellId"]).agg(aggs).select(out_cols).sort(["sampleId", "cellId"])


def _load(
    tag_stat_tsv: str,
    tag_feature_csv: str,
    cell_col: str,
    feature_tag_col: str,
    umi_count_col: str,
    csv_barcode_col: str = "tag",
    csv_feature_col: str = "feature",
    combine_col: str | None = None,
    min_umi: float = 1.0,
) -> pl.DataFrame:
    """Aggregated mitool ``tag-stat -u`` rows -> (cellId, feature, umiCount) long frame.

    ``mitool tag-stat -t CELL -t FEATURE -u UMI`` emits one row per (cell, feature-barcode) group:
    columns ``CELL FEATURE count totalWeight unique_UMI``. ``unique_UMI`` is the distinct-UMI
    (molecule) count for the group -- mitool does the deduplication, so we take that column directly
    rather than counting raw UMI rows ourselves. The tag->feature CSV maps the feature barcode to its
    feature/antigen name; ``csv_barcode_col``/``csv_feature_col`` let the user
    map arbitrary CSV header names to that barcode/feature role. Barcodes that map to the same feature
    are collapsed per that feature's combine mode (``combine_col``): ``"sum"``/absent sums the
    distinct-UMI counts (OR — the default), ``"all"`` emits the feature only in cells where every member
    barcode fired (>= ``min_umi``; AND — see ``combine_barcode_counts``). The output column is always
    named ``feature`` regardless of the source CSV's header, since downstream Xsv import depends on it.
    """
    stat = pl.read_csv(tag_stat_tsv, separator="\t")
    # A header-only tag-stat (a sample whose reads were all dropped -- e.g. every read off-panel) has no
    # data rows, so polars infers every column as String. Coerce the UMI-count column to a numeric type
    # up front, otherwise the downstream fraction division fails on String arithmetic. On a populated
    # file the column is already integer and this cast is a no-op.
    stat = stat.with_columns(pl.col(umi_count_col).cast(pl.Int64))
    mapping = pl.read_csv(tag_feature_csv)  # columns: csv_barcode_col (feature barcode), csv_feature_col
    # Normalize the join key + feature name like mitool.
    mapping = mapping.with_columns(
        pl.col(csv_barcode_col).cast(pl.Utf8).str.strip_chars(),
        pl.col(csv_feature_col).cast(pl.Utf8).str.strip_chars(),
    )
    print(
        f"[per-cell-metrics] tag-stat: {stat.height} rows, columns={stat.columns}",
        file=sys.stderr,
    )
    print(
        f"[per-cell-metrics] tag->feature CSV: {mapping.height} rows, columns={mapping.columns}",
        file=sys.stderr,
    )
    # A barcode must appear on exactly one CSV row. A barcode repeated across rows would fan the inner
    # join below out once per copy, and the group_by(...).sum() that follows would then multiply that
    # barcode's molecule counts silently DOUBLING counts.
    dup_barcodes = (
        mapping.group_by(csv_barcode_col).agg(pl.len().alias("_n")).filter(pl.col("_n") > 1)[csv_barcode_col].to_list()
    )
    if dup_barcodes:
        raise SystemExit(
            f"tag->feature CSV has {len(dup_barcodes)} barcode(s) on more than one row "
            f"(column {csv_barcode_col!r}); each feature barcode must map to exactly one feature. "
            f"Remove the duplicate rows: {dup_barcodes[:8]}"
        )
    # Per-feature combine mode + member-barcode set, parsed once from the (small) mapping. Default is
    # "sum" (OR). A combine column lets a feature request "all" (AND) — see combine_barcode_counts. A
    # blank cell means unset (defaults to "sum"); the non-blank rows of one feature must agree.
    feature_barcodes: dict[str, set[str]] = {}
    feature_modes_raw: dict[str, set[str]] = {}
    map_cols = [csv_barcode_col, csv_feature_col] + ([combine_col] if combine_col else [])
    for row in mapping.select(map_cols).iter_rows(named=True):
        feat = row[csv_feature_col]
        feature_barcodes.setdefault(feat, set()).add(row[csv_barcode_col])
        if combine_col:
            raw = row[combine_col]
            mode = ("" if raw is None else str(raw)).strip().lower()
            if mode:
                if mode not in ("sum", "all"):
                    raise SystemExit(f"invalid {combine_col!r} value {mode!r} for feature {feat!r}; allowed: sum, all")
                feature_modes_raw.setdefault(feat, set()).add(mode)
    feature_modes: dict[str, str] = {}
    for feat, vals in feature_modes_raw.items():
        if len(vals) > 1:
            raise SystemExit(
                f"feature {feat!r} has conflicting {combine_col!r} values {sorted(vals)}; "
                f"every row of a feature must request the same combine mode"
            )
        feature_modes[feat] = next(iter(vals))

    joined = stat.join(mapping, left_on=feature_tag_col, right_on=csv_barcode_col, how="inner")
    print(
        f"[per-cell-metrics] inner-join {feature_tag_col}={csv_barcode_col} -> {joined.height} rows",
        file=sys.stderr,
    )
    if joined.height == 0 and stat.height > 0:
        print(
            f"[per-cell-metrics] JOIN EMPTY: sample {feature_tag_col}="
            f"{stat[feature_tag_col].unique().to_list()[:8]} ; CSV {csv_barcode_col}="
            f"{mapping[csv_barcode_col].unique().to_list()[:8]}",
            file=sys.stderr,
        )
    rename = {cell_col: "cellId"}
    if csv_feature_col != "feature":
        rename[csv_feature_col] = "feature"

    # Per-feature mode + expected member-barcode count, as a frame to join onto the aggregate. n_expected
    # is how many DISTINCT barcodes map to the feature; the AND gate keeps a (cell, feature) group only
    # when that many member barcodes fired in the cell. With no combine column every feature is "sum",
    # so the filter is a no-op and the result is identical to the historical sum-only behaviour.
    mode_df = pl.DataFrame(
        {
            csv_feature_col: list(feature_barcodes.keys()),
            "_mode": [feature_modes.get(f, "sum") for f in feature_barcodes],
            "_nExpected": [len(feature_barcodes[f]) for f in feature_barcodes],
        },
        schema={csv_feature_col: pl.Utf8, "_mode": pl.Utf8, "_nExpected": pl.UInt32},
    )
    counts = (
        joined.with_columns((pl.col(umi_count_col) >= min_umi).alias("_fired"))
        .group_by([cell_col, csv_feature_col])
        .agg(
            pl.col(umi_count_col).sum().alias("umiCount"),
            # distinct member barcodes that fired in this cell (each tag-stat row is one barcode)
            pl.col("_fired").sum().cast(pl.UInt32).alias("_nFired"),
        )
        .join(mode_df, on=csv_feature_col, how="left")
        # sum-mode features always survive; "all"-mode only when every member barcode fired
        .filter((pl.col("_mode") != "all") | (pl.col("_nFired") == pl.col("_nExpected")))
        .select([cell_col, csv_feature_col, "umiCount"])
        .rename(rename)
    )
    print(
        f"[per-cell-metrics] counts (cell x feature): {counts.height} rows",
        file=sys.stderr,
    )
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_stat_tsv")
    p.add_argument("tag_feature_csv")
    p.add_argument("--sample-id", required=True)
    p.add_argument("--cell-col", default="CELL")
    p.add_argument("--feature-tag-col", default="FEATURE")
    p.add_argument(
        "--umi-count-col",
        default="unique_UMI",
        help="mitool tag-stat -u distinct-UMI column (unique_<umiTag>, default UMI)",
    )
    p.add_argument(
        "--csv-barcode-col",
        default="tag",
        help="CSV column holding the feature barcode (join key)",
    )
    p.add_argument(
        "--csv-feature-col",
        default="feature",
        help="CSV column holding the feature/antigen name",
    )
    p.add_argument(
        "--combine-col",
        default=None,
        help="optional CSV column giving each feature's combine mode when it is read out by more than "
        "one barcode: 'sum' (OR — sum member barcodes; the default when absent/blank) or 'all' (AND — "
        "call the feature only in cells where every member barcode fired)",
    )
    p.add_argument(
        "--min-umi",
        type=float,
        default=1.0,
        help="minimum per-barcode distinct-UMI count for a barcode to count as 'fired' under the 'all' "
        "(AND) combine mode (default 1)",
    )
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    # Guard the user-mapped CSV column names: the two roles must be distinct, and neither may
    # collide with a tag-stat column. On the inner join, every tag-stat column is carried into the
    # joined frame -- so a --csv-feature-col that names ANY tag-stat column (e.g. `count`,
    # `totalWeight`, `unique_UMI`, or the CELL/FEATURE keys) would otherwise pass through the
    # join/group silently and put the WRONG data (e.g. numeric counts) into the output `feature`
    # column; a collision on the cell key also crashes group_by/rename with a raw polars
    # DuplicateError. Read the real tag-stat header so we reject every collision, not just the three
    # flag-named columns.
    with open(args.tag_stat_tsv, newline="") as fh:
        reserved = set(next(csv.reader(fh, delimiter="\t"), []))
    if args.csv_barcode_col == args.csv_feature_col:
        raise SystemExit("--csv-barcode-col and --csv-feature-col must differ")
    if args.combine_col is not None and args.combine_col in (args.csv_barcode_col, args.csv_feature_col):
        raise SystemExit("--combine-col must differ from --csv-barcode-col and --csv-feature-col")
    if args.min_umi < 0:
        raise SystemExit("--min-umi must be >= 0")
    cols_to_check = [
        ("--csv-barcode-col", args.csv_barcode_col),
        ("--csv-feature-col", args.csv_feature_col),
    ]
    if args.combine_col is not None:
        cols_to_check.append(("--combine-col", args.combine_col))
    for name, val in cols_to_check:
        if val in reserved:
            raise SystemExit(
                f"{name}={val!r} collides with a tag-stat column ({sorted(reserved)}); choose a different CSV column"
            )

    counts = _load(
        args.tag_stat_tsv,
        args.tag_feature_csv,
        args.cell_col,
        args.feature_tag_col,
        args.umi_count_col,
        args.csv_barcode_col,
        args.csv_feature_col,
        args.combine_col,
        args.min_umi,
    )
    counts = counts.with_columns(pl.lit(args.sample_id).alias("sampleId"))

    # abundance matrix (cell x feature) UMI counts
    (
        counts.select(["sampleId", "cellId", "feature", "umiCount"])
        .sort(["sampleId", "cellId", "feature"])
        .write_csv(f"{args.output_prefix}_abundance.csv")
    )

    # within-cell fractions (normalised across features per cell, sum to 1). Computed
    # once here (with_fraction) and reused for the per-cell summary so the two never diverge.
    cf = with_fraction(counts)
    cf.select(["sampleId", "cellId", "feature", "fraction"]).sort(["sampleId", "cellId", "feature"]).write_csv(
        f"{args.output_prefix}_fractions.csv"
    )

    # per-cell summary (table-only collapse): one row per (sampleId, cellId) with the max feature UMI
    # count / fraction and the "feature (fraction%, umi) | ..." string. cf already carries fraction, so
    # nothing is recomputed.
    per_cell_summary(cf).write_csv(f"{args.output_prefix}_per_cell_summary.csv")


if __name__ == "__main__":
    main()
