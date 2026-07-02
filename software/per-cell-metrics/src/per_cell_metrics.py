"""Per-cell feature metrics for the Feature Integration block.

Collapses mitool tag-stat output into a (cell x feature) UMI matrix, then computes within-cell
fractions, the consensus feature (dominant-category rule, spec A-0012), and an optional Cell Ranger
specificity score (spec A-0014).

The math functions are pure and unit-tested; the CLI wires them to CSV I/O. Every output is sorted
before writing: stable row order makes the CLI deterministic and keeps the workflow's pure-template
dedup canonical.
"""

import argparse
import csv
import sys

import polars as pl
from scipy.stats import beta

DOMINANCE_FLOOR = 0.5  # spec A-0012: threshold is user-adjustable down to 0.5, never lower

# Schema for the no-control specificity output only. When no negative control is set we still emit a
# header-only specificity CSV (the workflow's output set is fixed), and that frame has no source rows,
# so it needs an explicit schema. Every other output is a pure-polars transform of `counts`, which
# carries its schema through the empty case natively (an empty join writes a header-only CSV, not a
# crash).
_SPECIFICITY_SCHEMA = {
    "sampleId": pl.Utf8,
    "cellId": pl.Utf8,
    "feature": pl.Utf8,
    "specificityScore": pl.Float64,
}


def consensus_category(counts: dict[str, float], threshold: float) -> str | None:
    """Dominant-category rule (spec A-0012).

    Returns the single dominant category when it is the unique maximum AND its share of the total is
    >= threshold; "ambiguous" when signal exists but no unique category passes (a spread distribution,
    or an exact split at the 0.5 floor); None when there is no signal at all. ``threshold`` is clamped
    up to the 0.5 floor.
    """
    threshold = max(threshold, DOMINANCE_FLOOR)
    positive = {k: v for k, v in counts.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return None
    max_val = max(positive.values())
    winners = [k for k, v in positive.items() if v == max_val]
    if len(winners) == 1 and (max_val / total) >= threshold:
        return winners[0]
    return "ambiguous"


def specificity_score(antigen_umi, control_umi):
    """Cell Ranger BEAM specificity score (spec A-0014), constants are Cell Ranger's:
    (1 - betaCDF(0.925, antigenUMI + 1, controlUMI + 3)) * 100.

    Accepts scalars or numpy arrays. scipy's beta.cdf is vectorized, so the CLI passes whole columns
    (the array path avoids a per-row Python loop); returns a numpy float or float array accordingly.
    """
    return (1.0 - beta.cdf(0.925, antigen_umi + 1, control_umi + 3)) * 100.0


# Decimal places for the fraction in the per-cell feature-summary string (table-only display; the
# exported Double fractions keep full precision).
_SUMMARY_FRACTION_DECIMALS = 3


def per_cell_summary(counts: pl.DataFrame, control: str | None) -> pl.DataFrame:
    """One row per (sampleId, cellId): the cell's max feature UMI count and max feature fraction
    (and, with a negative control, the max specificity score), plus a ``featureSummary`` string that
    lists every feature the cell has signal for as ``feature : umiCount : fraction``, joined by
    " | " and sorted by descending fraction (dominant feature first, feature name as tie-break).

    This is a TABLE-ONLY collapse of the (cell x feature) matrix -- the per-feature abundance,
    fractions, consensus, and specificity outputs (the A-0010 export contract) are unaffected.
    ``counts`` is the (sampleId, cellId, feature, umiCount) long frame; an empty frame carries its
    schema through to a header-only summary.
    """
    per_cell = counts.with_columns(
        (pl.col("umiCount") / pl.col("umiCount").sum().over(["sampleId", "cellId"])).alias("fraction")
    )
    has_control = control is not None
    if has_control:
        # specificity per (cell, feature) vs the cell's control UMIs (0 when the cell has none) --
        # same betaCDF path as the exported specificity column; here we keep only the per-cell max.
        ctrl = per_cell.filter(pl.col("feature") == control).select(["cellId", pl.col("umiCount").alias("_controlUmi")])
        per_cell = per_cell.join(ctrl, on="cellId", how="left").with_columns(pl.col("_controlUmi").fill_null(0))
        scores = specificity_score(per_cell["umiCount"].to_numpy(), per_cell["_controlUmi"].to_numpy())
        per_cell = per_cell.with_columns(pl.Series("specificityScore", scores, dtype=pl.Float64))

    per_cell = per_cell.with_columns(
        pl.format(
            "{} : {} : {}",
            pl.col("feature"),
            pl.col("umiCount"),
            pl.col("fraction").round(_SUMMARY_FRACTION_DECIMALS),
        ).alias("_entry")
    )
    aggs = [
        pl.col("umiCount").max().alias("maxUmiCount"),
        pl.col("fraction").max().alias("maxFraction"),
        pl.col("_entry")
        .sort_by(["fraction", "feature"], descending=[True, False])
        .str.join(" | ")
        .alias("featureSummary"),
    ]
    out_cols = ["sampleId", "cellId", "maxUmiCount", "maxFraction"]
    if has_control:
        aggs.append(pl.col("specificityScore").max().alias("maxSpecificityScore"))
        out_cols.append("maxSpecificityScore")
    out_cols.append("featureSummary")
    return per_cell.group_by(["sampleId", "cellId"]).agg(aggs).select(out_cols).sort(["sampleId", "cellId"])


def _load(
    tag_stat_tsv: str,
    tag_feature_csv: str,
    cell_col: str,
    feature_tag_col: str,
    umi_count_col: str,
    csv_barcode_col: str = "tag",
    csv_feature_col: str = "feature",
) -> pl.DataFrame:
    """Aggregated mitool ``tag-stat -u`` rows -> (cellId, feature, umiCount) long frame.

    ``mitool tag-stat -t CELL -t FEATURE -u UMI`` emits one row per (cell, feature-barcode) group:
    columns ``CELL FEATURE count totalWeight unique_UMI``. ``unique_UMI`` is the distinct-UMI
    (molecule) count for the group -- mitool does the deduplication, so we take that column directly
    rather than counting raw UMI rows ourselves. The tag->feature CSV maps the feature barcode to its
    feature/antigen name (spec A-0004, A-0009); ``csv_barcode_col``/``csv_feature_col`` let the user
    map arbitrary CSV header names to that barcode/feature role (D4). We sum the distinct-UMI counts
    across barcodes that map to the same feature. The output column is always named ``feature``
    regardless of the source CSV's header, since downstream Xsv import depends on that name.
    """
    stat = pl.read_csv(tag_stat_tsv, separator="\t")
    # A header-only tag-stat (a sample whose reads were all dropped -- e.g. every read off-panel) has no
    # data rows, so polars infers every column as String. Coerce the UMI-count column to a numeric type
    # up front, otherwise the downstream fraction division fails on String arithmetic. On a populated
    # file the column is already integer and this cast is a no-op.
    stat = stat.with_columns(pl.col(umi_count_col).cast(pl.Int64))
    mapping = pl.read_csv(tag_feature_csv)  # columns: csv_barcode_col (feature barcode), csv_feature_col
    print(
        f"[per-cell-metrics] tag-stat: {stat.height} rows, columns={stat.columns}",
        file=sys.stderr,
    )
    print(
        f"[per-cell-metrics] tag->feature CSV: {mapping.height} rows, columns={mapping.columns}",
        file=sys.stderr,
    )
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
    counts = (
        joined.group_by([cell_col, csv_feature_col]).agg(pl.col(umi_count_col).sum().alias("umiCount")).rename(rename)
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
        help="CSV column holding the feature barcode (join key; spec A-0004)",
    )
    p.add_argument(
        "--csv-feature-col",
        default="feature",
        help="CSV column holding the feature/antigen name (spec A-0004, A-0009)",
    )
    p.add_argument("--dominance-threshold", type=float, default=0.6)
    p.add_argument("--control", default=None, help="negative-control feature name (spec A-0014)")
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    # Guard the user-mapped CSV column names (D4): the two roles must be distinct, and neither may
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
    for name, val in (
        ("--csv-barcode-col", args.csv_barcode_col),
        ("--csv-feature-col", args.csv_feature_col),
    ):
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
    )
    counts = counts.with_columns(pl.lit(args.sample_id).alias("sampleId"))

    # abundance matrix (cell x feature) UMI counts
    (
        counts.select(["sampleId", "cellId", "feature", "umiCount"])
        .sort(["sampleId", "cellId", "feature"])
        .write_csv(f"{args.output_prefix}_abundance.csv")
    )

    # within-cell fractions (normalised across features per cell, sum to 1) -- spec A-0010
    fractions = counts.with_columns(
        (pl.col("umiCount") / pl.col("umiCount").sum().over("cellId")).alias("fraction")
    ).select(["sampleId", "cellId", "feature", "fraction"])
    fractions.sort(["sampleId", "cellId", "feature"]).write_csv(f"{args.output_prefix}_fractions.csv")

    # consensus feature per cell (dominant-category rule, spec A-0012), vectorized in polars: the
    # dominant feature is the unique per-cell max whose share of the cell's total is >= the threshold
    # (clamped to the 0.5 floor); otherwise "ambiguous". No-signal cells never occur here (tag-stat
    # counts are all > 0), so None is never produced. Mirrors consensus_category, which the tests pin
    # (and an oracle test cross-checks this vectorized path against it).
    threshold = max(args.dominance_threshold, DOMINANCE_FLOOR)
    (
        counts.group_by(["sampleId", "cellId"])
        .agg(
            pl.col("umiCount").sum().alias("_total"),
            pl.col("umiCount").max().alias("_max"),
            (pl.col("umiCount") == pl.col("umiCount").max()).sum().alias("_nAtMax"),
            pl.col("feature").sort_by("umiCount", descending=True).first().alias("_top"),
        )
        .with_columns(
            pl.when((pl.col("_nAtMax") == 1) & (pl.col("_max") / pl.col("_total") >= threshold))
            .then(pl.col("_top"))
            .otherwise(pl.lit("ambiguous"))
            .alias("consensusFeature")
        )
        .select(["sampleId", "cellId", "consensusFeature"])
        .sort(["sampleId", "cellId"])
        .write_csv(f"{args.output_prefix}_consensus.csv")
    )

    # optional specificity score per (cell, feature) vs the negative control (spec A-0014).
    # specificity_score broadcasts over numpy arrays (scipy beta.cdf is vectorized), so the whole
    # column is computed at once instead of a per-row Python loop. An empty join carries the schema
    # through natively -> header-only CSV.
    if args.control is not None:
        ctrl = counts.filter(pl.col("feature") == args.control).select(
            ["cellId", pl.col("umiCount").alias("controlUmi")]
        )
        spec = counts.join(ctrl, on="cellId", how="left").with_columns(pl.col("controlUmi").fill_null(0))
        scores = specificity_score(spec["umiCount"].to_numpy(), spec["controlUmi"].to_numpy())
        (
            spec.with_columns(pl.Series("specificityScore", scores, dtype=pl.Float64))
            .select(["sampleId", "cellId", "feature", "specificityScore"])
            .sort(["sampleId", "cellId", "feature"])
            .write_csv(f"{args.output_prefix}_specificity.csv")
        )
    else:
        # No negative control: still emit an (empty, header-only) specificity CSV so the workflow's
        # fixed output set is satisfied. It is not imported when no control is set (main.tpl and the
        # model gate the specificity column on hasControl).
        pl.DataFrame(schema=_SPECIFICITY_SCHEMA).write_csv(f"{args.output_prefix}_specificity.csv")

    # per-cell summary (table-only collapse): one row per (sampleId, cellId) with the max feature UMI
    # count / fraction (/ specificity, with a control) and the "feature : umi : fraction | ..." string.
    # The maxSpecificityScore column is present only with a control, matching how main.tpl / the model
    # gate the specificity import on hasControl.
    per_cell_summary(counts, args.control).write_csv(f"{args.output_prefix}_per_cell_summary.csv")


if __name__ == "__main__":
    main()
