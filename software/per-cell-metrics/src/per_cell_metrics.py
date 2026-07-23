"""Per-cell feature metrics for the Feature Integration block.

Collapses mitool tag-stat output into a (cell x feature) UMI matrix, then computes within-cell
fractions, the consensus feature (dominant-category rule), and an optional Cell Ranger
specificity score.

The math functions are pure and unit-tested; the CLI wires them to CSV I/O. Every output is sorted
before writing: stable row order makes the CLI deterministic and keeps the workflow's pure-template
dedup canonical.
"""

import argparse
import csv
import sys

import polars as pl
from scipy.stats import beta

DOMINANCE_FLOOR = 0.5  # threshold is user-adjustable down to 0.5, never lower

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


CROSS_REACTIVE = "Target cross-reactive"


def consensus_category(
    counts: dict[str, float],
    threshold: float,
    control: str | None = None,
    offtargets: frozenset[str] = frozenset(),
    label_crossreactive: bool = False,
) -> str | None:
    """Dominant-category rule.

    Returns the single dominant category when it is the unique maximum AND its share of the total is
    >= threshold; "ambiguous" when signal exists but no unique category passes (a spread distribution,
    or an exact split at the 0.5 floor); None when there is no signal at all. ``threshold`` is clamped
    up to the 0.5 floor.

    The negative ``control`` and the ``offtargets`` set are references, not callable antigens: they are
    excluded from the winner candidates, so a cell dominated by control/off-target signal is "ambiguous",
    never the control or an off-target. Their UMIs are still counted in ``total`` (the denominator), so
    control/off-target signal SUPPRESSES antigen dominance rather than being renormalised away — a cell
    swamped by them correctly fails the threshold instead of having its top on-target inflated to 100%.

    ``offtargets`` designate features whose property (e.g. Type = Off-Target) marks them as
    binders the user does not want to call. When they are supplied and ``label_crossreactive`` is set,
    the overloaded "ambiguous" bucket is split: a cell whose on-target (non-excluded) signal collectively
    passes the threshold but is spread across >= 2 on-target features is called "cross-reactive" (a
    genuine multi-/cross-reactive binder — e.g. the same target's human + cyno variants) rather than
    lumped with true noise. A cell whose on-target signal fails the threshold (off-target/control-swamped,
    or a flat spread) stays "ambiguous". With no off-targets designated the rule is unchanged.
    """
    threshold = max(threshold, DOMINANCE_FLOOR)
    excluded = set(offtargets)
    if control is not None:
        excluded.add(control)
    positive = {k: v for k, v in counts.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return None
    candidates = {k: v for k, v in positive.items() if k not in excluded}
    if not candidates:
        return "ambiguous"  # only control/off-target (or no) signal — no on-target to call
    max_val = max(candidates.values())
    winners = [k for k, v in candidates.items() if v == max_val]
    if len(winners) == 1 and (max_val / total) >= threshold:
        return winners[0]
    # cross-reactive: on-target signal collectively dominates but is split across >= 2 on-targets.
    if label_crossreactive and len(candidates) >= 2 and (sum(candidates.values()) / total) >= threshold:
        return CROSS_REACTIVE
    return "ambiguous"


def offtarget_features(
    tag_feature_csv: str,
    csv_feature_col: str,
    offtarget_col: str,
    offtarget_values: frozenset[str],
) -> frozenset[str]:
    """Feature names whose designated property (``offtarget_col``) value is in ``offtarget_values``.

    The off-target designation is property-driven: the user picks one imported per-feature property
    column (e.g. ``antigen_class``) and the set of its values that mark a feature as off-target (e.g.
    {"Off-Target", "Off-target"}). This reads the tag->feature CSV — which carries those property columns —
    and returns the resolved set of off-target FEATURE names, so the dominant call can exclude them.

    Values are matched exactly, whitespace-trimmed but CASE-SENSITIVE (``strip()`` on both sides, no
    case folding): a feature is off-target only if its ``offtarget_col`` value is byte-identical (after
    trimming) to one the user selected. Real panels (e.g. B043) may carry mixed casing of one designation
    — ``Off-Target`` and ``Off-target`` in a single column — so the user selects every casing they mean;
    each distinct value is offered separately in the block's dropdown. Whitespace is trimmed because
    leading/trailing spaces are invisible in the picker; casing is left intact because it is visible and
    the user's to choose (the block never silently broadens a selection to unselected values). The
    returned FEATURE names are verbatim (trimmed) from the CSV.
    """
    mapping = pl.read_csv(tag_feature_csv)
    if offtarget_col not in mapping.columns:
        raise SystemExit(
            f"--offtarget-col={offtarget_col!r} is not a column of the tag->feature CSV ({mapping.columns})"
        )
    wanted_trimmed = {v.strip() for v in offtarget_values}
    resolved = {
        (feat or "").strip()
        for feat, val in mapping.select(
            pl.col(csv_feature_col).cast(pl.Utf8),
            pl.col(offtarget_col).cast(pl.Utf8),
        ).iter_rows()
        if val is not None and val.strip() in wanted_trimmed
    }
    return frozenset(resolved)


def specificity_score(antigen_umi, control_umi):
    """Cell Ranger BEAM specificity score, constants are Cell Ranger's:
    (1 - betaCDF(0.925, antigenUMI + 1, controlUMI + 3)) * 100.

    Accepts scalars or numpy arrays. scipy's beta.cdf is vectorized, so the CLI passes whole columns
    (the array path avoids a per-row Python loop); returns a numpy float or float array accordingly.
    """
    return (1.0 - beta.cdf(0.925, antigen_umi + 1, control_umi + 3)) * 100.0


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
      compete for dominance, take a fraction, or get a specificity score. This expresses the LIBRA-seq /
      dual-probe design where a cell is antigen-specific only when both probe barcodes fire.

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
    summary so the two never diverge or recompute the window."""
    return counts.with_columns(
        (pl.col("umiCount") / pl.col("umiCount").sum().over(["sampleId", "cellId"])).alias("fraction")
    )


def with_specificity(frame: pl.DataFrame, control: str) -> pl.DataFrame:
    """Add the per-(cell, feature) Cell Ranger specificity score vs the cell's control UMIs (0 when the
    cell has no control reads). scipy beta.cdf is evaluated once over the whole column (no per-row loop).
    An empty join carries the schema through. Computed once in main() and reused for both the exported
    specificity CSV and the per-cell summary's max, so the two never diverge or recompute the betaCDF.

    The control itself is the reference, not a scored antigen: its own row's score is nulled, so the
    control never appears as a scored feature in the exported specificity CSV (main() drops null scores)
    and never drives the per-cell maxSpecificityScore (a max skips nulls)."""
    ctrl = frame.filter(pl.col("feature") == control).select(["cellId", pl.col("umiCount").alias("_controlUmi")])
    joined = frame.join(ctrl, on="cellId", how="left").with_columns(pl.col("_controlUmi").fill_null(0))
    scores = specificity_score(joined["umiCount"].to_numpy(), joined["_controlUmi"].to_numpy())
    return (
        joined.with_columns(pl.Series("specificityScore", scores, dtype=pl.Float64))
        .with_columns(
            pl.when(pl.col("feature") == control)
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise(pl.col("specificityScore"))
            .alias("specificityScore")
        )
        .drop("_controlUmi")
    )


def per_cell_summary(per_cell: pl.DataFrame) -> pl.DataFrame:
    """One row per (sampleId, cellId): the cell's max feature UMI count and max feature fraction
    (and, when a ``specificityScore`` column is present, the max specificity score), plus a
    ``featureSummary`` string that lists every feature the cell has signal for as
    ``feature (fraction%, umiCount UMI)``, bullet-separated and sorted by descending fraction (dominant
    feature first, feature name as tie-break). Fractions display as whole percents, with "<1%" for a
    nonzero feature that rounds below 1%.

    This is a TABLE-ONLY collapse of the (cell x feature) matrix -- the per-feature abundance,
    fractions, consensus, and specificity outputs (the per-cell export contract) are unaffected.
    ``per_cell`` is the (sampleId, cellId, feature, umiCount) long frame ALREADY carrying the
    ``fraction`` column (and ``specificityScore`` when a negative control is set) that main() computed
    once for the exported CSVs -- so the per-cell maxima can never diverge from the exported columns,
    and the fraction window / betaCDF are not recomputed here. An empty frame carries its schema through
    to a header-only summary.
    """
    has_control = "specificityScore" in per_cell.columns

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
        # comma-separated, dominant feature first.
        .str.join(", ")
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
    p.add_argument("--dominance-threshold", type=float, default=0.6)
    p.add_argument("--control", default=None, help="negative-control feature name")
    p.add_argument(
        "--offtarget-col",
        default=None,
        help="CSV property column (e.g. antigen_class) designating on/off-target; features whose value "
        "is in --offtarget-values are excluded from the dominant call (like the control) and enable the "
        "cross-reactive label",
    )
    p.add_argument(
        "--offtarget-values",
        default=None,
        help="comma-separated values of --offtarget-col that mark a feature as off-target (e.g. 'Off-Target,Off-target')",
    )
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    # Resolve the off-target feature set from the designated property column + values. Both flags must be
    # given together; features carrying an off-target value are excluded from the dominant call (as the
    # control is) and turn on the cross-reactive label. Absent -> unchanged behaviour (empty set).
    offtargets: frozenset[str] = frozenset()
    if (args.offtarget_col is None) != (args.offtarget_values is None):
        raise SystemExit("--offtarget-col and --offtarget-values must be given together")
    if args.offtarget_col is not None:
        wanted = frozenset(v.strip() for v in args.offtarget_values.split(",") if v.strip())
        offtargets = offtarget_features(args.tag_feature_csv, args.csv_feature_col, args.offtarget_col, wanted)
    label_crossreactive = len(offtargets) > 0

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

    # consensus feature per cell (dominant-category rule), vectorized in polars: the
    # dominant feature is the unique per-cell max whose share of the cell's total is >= the threshold
    # (clamped to the 0.5 floor); otherwise "ambiguous". No-signal cells never occur here (tag-stat
    # counts are all > 0), so None is never produced. Mirrors consensus_category, which the tests pin
    # (and an oracle test cross-checks this vectorized path against it).
    threshold = max(args.dominance_threshold, DOMINANCE_FLOOR)
    # The negative control and the off-target features are references, not callable antigens: exclude
    # them from the winner candidates so a control/off-target-dominated cell is "ambiguous", never the
    # control or an off-target. Their UMIs stay in `_total` (the denominator, computed from the full
    # `counts`), so their signal suppresses dominance rather than being renormalised away. When off-
    # targets are designated, a cell whose on-target signal collectively passes the threshold but is
    # spread across >= 2 on-targets is "cross-reactive". Mirrors consensus_category(control=..., off
    # targets=..., label_crossreactive=...), which the oracle test pins the vectorized path against.
    excluded = list(offtargets) + ([args.control] if args.control is not None else [])
    antigens = counts if not excluded else counts.filter(~pl.col("feature").is_in(excluded))
    totals = counts.group_by(["sampleId", "cellId"]).agg(pl.col("umiCount").sum().alias("_total"))
    tops = antigens.group_by(["sampleId", "cellId"]).agg(
        pl.col("umiCount").max().alias("_max"),
        (pl.col("umiCount") == pl.col("umiCount").max()).sum().alias("_nAtMax"),
        pl.col("feature").sort_by("umiCount", descending=True).first().alias("_top"),
        # on-target signal: sum + distinct on-target features present (for the cross-reactive branch)
        pl.col("umiCount").sum().alias("_onTotal"),
        pl.col("feature").n_unique().alias("_nOn"),
    )
    (
        totals.join(tops, on=["sampleId", "cellId"], how="left")
        .with_columns(
            # _top is null for a cell whose only signal is control/off-target -> ambiguous.
            pl.when(
                pl.col("_top").is_not_null()
                & (pl.col("_nAtMax") == 1)
                & (pl.col("_max") / pl.col("_total") >= threshold)
            )
            .then(pl.col("_top"))
            .when(
                # cross-reactive: on-target signal collectively dominates but is split across >= 2 on-targets
                pl.lit(label_crossreactive)
                & (pl.col("_nOn") >= 2)
                & (pl.col("_onTotal") / pl.col("_total") >= threshold)
            )
            .then(pl.lit(CROSS_REACTIVE))
            .otherwise(pl.lit("ambiguous"))
            .alias("consensusFeature")
        )
        .select(["sampleId", "cellId", "consensusFeature"])
        .sort(["sampleId", "cellId"])
        .write_csv(f"{args.output_prefix}_consensus.csv")
    )

    # optional specificity score per (cell, feature) vs the negative control. Computed
    # once (with_specificity: scipy beta.cdf vectorized over the whole column) and reused for the
    # per-cell summary's max. An empty join carries the schema through natively -> header-only CSV.
    if args.control is not None:
        summary_frame = with_specificity(cf, args.control)
        (
            # The control's own row carries a null score (it is the reference, not a scored antigen) --
            # drop those so the exported specificity is antigen-only. summary_frame KEEPS the control row
            # (with a null score) so the per-cell summary's umi/fraction breakdown still shows it.
            summary_frame.filter(pl.col("specificityScore").is_not_null())
            .select(["sampleId", "cellId", "feature", "specificityScore"])
            .sort(["sampleId", "cellId", "feature"])
            .write_csv(f"{args.output_prefix}_specificity.csv")
        )
    else:
        # No negative control: still emit an (empty, header-only) specificity CSV so the workflow's
        # fixed output set is satisfied. It is not imported when no control is set (main.tpl and the
        # model gate the specificity column on hasControl).
        pl.DataFrame(schema=_SPECIFICITY_SCHEMA).write_csv(f"{args.output_prefix}_specificity.csv")
        summary_frame = cf

    # per-cell summary (table-only collapse): one row per (sampleId, cellId) with the max feature UMI
    # count / fraction (/ specificity, with a control) and the "feature (fraction%, umi) | ..." string.
    # summary_frame already carries fraction (+ specificityScore with a control), so nothing is
    # recomputed. The maxSpecificityScore column is present only with a control, matching how main.tpl /
    # the model gate the specificity import on hasControl.
    per_cell_summary(summary_frame).write_csv(f"{args.output_prefix}_per_cell_summary.csv")


if __name__ == "__main__":
    main()
