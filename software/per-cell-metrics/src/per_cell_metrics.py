"""Per-cell feature metrics for the Feature Integration block.

Collapses mitool tag-stat output into a (cell x feature) UMI matrix, then computes within-cell
fractions and a per-cell summary. Math functions are pure and unit-tested; the CLI wires them to
CSV I/O. Every output is sorted before writing, which keeps the workflow's pure-template dedup
canonical.
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
    """Collapse ONE cell's per-barcode UMI counts into per-feature counts by combine mode.

    The pure rule the vectorized ``_load`` path mirrors; an oracle test pins them together. One antigen
    can be read out by several barcodes, such as a dual-labeled probe:

    - ``"sum"`` (OR, default): UMI is the sum of member barcodes present in the cell.
    - ``"all"`` (AND): called only when every member fired at ``umi >= min_umi``, UMI is their sum.
      Otherwise the feature is absent for the cell -- omitted, not zero -- so it takes no share of the
      cell's signal. This is the LIBRA-seq dual-probe design.

    ``barcode_umi`` holds only barcodes with signal, because tag-stat emits count>0 rows. Off-panel
    barcodes are ignored, mirroring the inner join.
    """
    present: dict[str, dict[str, float]] = {}
    for bc, umi in barcode_umi.items():
        feat = barcode_to_feature.get(bc)
        if feat is None:
            continue  # off-panel: mirrors the tag->feature inner join
        present.setdefault(feat, {})[bc] = umi
    out: dict[str, float] = {}
    for feat, bc_umis in present.items():
        if feature_modes.get(feat, "sum") == "all":
            members = feature_barcodes[feat]
            # Presence is tested explicitly so AND stays correct at min_umi == 0, where a 0.0 default
            # would let an absent barcode fire.
            if all(bc in bc_umis and bc_umis[bc] >= min_umi for bc in members):
                out[feat] = sum(bc_umis.values())
        else:  # "sum" / OR
            out[feat] = sum(bc_umis.values())
    return out


def with_fraction(counts: pl.DataFrame) -> pl.DataFrame:
    """Add the within-cell UMI ``fraction`` to the (sampleId, cellId, feature, umiCount) frame.

    Each feature's share of its cell's total, summing to 1 per cell. main() computes it once and reuses
    it for the exported CSV and the per-cell summary, so the two cannot diverge.

    A cell whose counts are all zero divides to NaN, which reaches the exported CSV and then crashes
    `per_cell_summary`'s Int64 cast. Zero is the honest reading. Real input cannot get there, because
    tag-stat emits count>0 rows only."""
    total = pl.col("umiCount").sum().over(["sampleId", "cellId"])
    return counts.with_columns(pl.when(total > 0).then(pl.col("umiCount") / total).otherwise(0.0).alias("fraction"))


def per_cell_summary(per_cell: pl.DataFrame) -> pl.DataFrame:
    """One row per (sampleId, cellId): max feature UMI count, max fraction, and a ``featureSummary``
    string of every feature with signal as ``feature (fraction%, umiCount UMI)``, bullet-separated,
    largest share first, feature name as tie-break.

    A TABLE-ONLY collapse; the abundance and fractions exports are unaffected. ``per_cell`` already
    carries the ``fraction`` main() computed, so the maxima cannot diverge from the exports.
    """
    # "<1%" so a real-but-tiny signal never reads as "0%". Exports keep full precision.
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

    ``tag-stat -t CELL -t FEATURE -u UMI`` emits one row per (cell, barcode) group with columns
    ``CELL FEATURE count totalWeight unique_UMI``. mitool deduplicates, so ``unique_UMI`` is taken
    directly as the molecule count. ``csv_barcode_col`` / ``csv_feature_col`` map arbitrary headers
    onto the barcode and feature roles.

    Barcodes sharing a feature collapse by its ``combine_col`` mode: ``"sum"`` or absent sums the
    counts (OR, default), ``"all"`` emits the feature only where every member fired at >= ``min_umi``.
    The output column is always named ``feature``, because Xsv import needs it.
    """
    stat = pl.read_csv(tag_stat_tsv, separator="\t")
    # A header-only tag-stat (every read dropped) has no data rows, so polars infers String for every
    # column. Coerce here, or the fraction division fails on String arithmetic.
    stat = stat.with_columns(pl.col(umi_count_col).cast(pl.Int64))
    mapping = pl.read_csv(tag_feature_csv)  # columns: csv_barcode_col, csv_feature_col
    # Normalize the join key and feature name the way mitool does.
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
    # A barcode must appear on exactly one CSV row. A repeat fans the inner join out once per copy, and
    # the group_by(...).sum() below then silently DOUBLES that barcode's molecule counts.
    dup_barcodes = (
        mapping.group_by(csv_barcode_col).agg(pl.len().alias("_n")).filter(pl.col("_n") > 1)[csv_barcode_col].to_list()
    )
    if dup_barcodes:
        raise SystemExit(
            f"tag->feature CSV has {len(dup_barcodes)} barcode(s) on more than one row "
            f"(column {csv_barcode_col!r}); each feature barcode must map to exactly one feature. "
            f"Remove the duplicate rows: {dup_barcodes[:8]}"
        )
    # Per-feature combine mode and member set, parsed once from the small mapping. Default "sum" (OR);
    # "all" requests AND. A blank cell is unset. Non-blank rows of one feature must agree.
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

    # Per-feature mode and expected member count, joined onto the aggregate. n_expected counts the
    # DISTINCT barcodes mapping to the feature; the AND gate keeps a group only when that many fired.
    # With no combine column every feature is "sum", so the filter is a no-op.
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
            pl.col("_fired").sum().cast(pl.UInt32).alias("_nFired"),
        )
        .join(mode_df, on=csv_feature_col, how="left")
        # sum-mode always survives. "all"-mode survives only when every member fired.
        .filter((pl.col("_mode") != "all") | (pl.col("_nFired") == pl.col("_nExpected")))
        .select([cell_col, csv_feature_col, "umiCount"])
        .rename(rename)
    )
    print(
        f"[per-cell-metrics] counts (cell x feature): {counts.height} rows",
        file=sys.stderr,
    )
    return counts


# The live-progress contract the mitool steps already use. The workflow captures this step's stdout as
# a stream and the model scrapes lines carrying this prefix. Without it the bar sat at the band floor
# for the whole of this step, the slowest one on a large run.
#
# The percent is the share of THIS step done when the named phase begins. No ETA: these phases are
# whole-frame polars operations with no iteration count to extrapolate from.
_PROGRESS_PREFIX = "[==PROGRESS==]"


def _progress(stage: str, percent: float) -> None:
    """Emit one progress line. Flushed, or it sits in the pipe buffer until the step ends."""
    print(f"{_PROGRESS_PREFIX}{stage}: {percent:.1f}%", flush=True)


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

    # The two mapped roles must be distinct, and neither may collide with a tag-stat column. The inner
    # join carries every tag-stat column through, so a --csv-feature-col naming one of them would silently
    # put the WRONG data into the output `feature` column; a collision on the cell key crashes with a raw
    # polars DuplicateError. Read the real header, so every collision is caught.
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

    _progress("Reading counts", 0.0)
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

    _progress("Writing counts per cell", 45.0)
    (
        counts.select(["sampleId", "cellId", "feature", "umiCount"])
        .sort(["sampleId", "cellId", "feature"])
        .write_csv(f"{args.output_prefix}_abundance.csv")
    )

    _progress("Computing within-cell fractions", 65.0)
    # Within-cell fractions, computed once here and reused by the per-cell summary.
    cf = with_fraction(counts)
    cf.select(["sampleId", "cellId", "feature", "fraction"]).sort(["sampleId", "cellId", "feature"]).write_csv(
        f"{args.output_prefix}_fractions.csv"
    )

    # Per-cell summary: one row per (sampleId, cellId) with max UMI count and fraction, plus the
    # "feature (fraction%, umi) | ..." string. cf already carries fraction.
    _progress("Summarising cells", 85.0)
    per_cell_summary(cf).write_csv(f"{args.output_prefix}_per_cell_summary.csv")
    _progress("Metrics complete", 100.0)


if __name__ == "__main__":
    main()
