"""Per-sample QC summary for the Feature Integration block.

One row per sample: read-level metrics from mitool's parse JSON report (parseReport.total/.matched),
cell/feature/UMI metrics from the tag-stat TSV, the panel-assigned fraction from the refine-tags JSON
report, and the aggregate-barcode read fraction (`qc_measures.detect_aggregate_barcodes`) computed
from the tag-stat TSV's per-barcode UMI and read totals. panelAssignedFraction is left blank only
when no refine report is available.
"""

import argparse
import csv
import json
import sys

import polars as pl
from qc_measures import (
    AGGREGATE_BARCODE_IQR_MULTIPLIER,
    AGGREGATE_BARCODE_MIN_THRESHOLD,
    AGGREGATE_BARCODE_TOP_N,
    detect_aggregate_barcodes,
)

FIELDNAMES = [
    "sampleId",
    "readsTotal",
    "readsMatched",
    "matchedFraction",
    "cellsDetected",
    "featuresDetected",
    "totalUniqueUmis",
    "medianUmisPerCell",
    "panelAssignedFraction",
    "cellBarcodeValidFraction",
    "aggregateBarcodeFraction",
    "aggregateBarcodesFlagged",
    "aggregateBarcodeThreshold",
]


def _parse_report(path: str) -> tuple[int, int]:
    with open(path) as fh:
        rep = json.load(fh)
    pr = rep.get("parseReport", rep)
    return int(pr.get("total", 0)), int(pr.get("matched", 0))


def _refine_kept_fraction(path: str | None, tag_name: str = "FEATURE") -> float | None:
    """The share of reads one refine-tags step kept, as ``outputCount / inputCount``.

    Each step corrects one tag's barcode against a whitelist and drops reads whose barcode is
    not within correction distance of any entry. Which whitelist depends on the tag: the
    FEATURE step corrects against the panel, the CELL step against the chemistry's barcodes.
    ``tag_name`` is the mitool tag to match against the report's ``tagName``.

    Returns None, blank in the CSV, when the report is absent or unreadable, carries no
    matching step, or that step has zero input reads. QC never crashes on an edge-case report.
    """
    if not path:
        return None
    try:
        with open(path) as fh:
            rep = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    steps = rep.get("steps", [])
    for step in steps:
        if step.get("tagName") == tag_name:
            input_count = step.get("inputCount", 0)
            if not input_count:
                return None
            return step.get("outputCount", 0) / input_count
    # A report with steps but none matching the feature tag means the schema or tag naming drifted.
    # Surface it rather than silently blanking the metric for every sample.
    if steps:
        print(
            f"[qc-report] refine report has no {tag_name!r} step "
            f"(saw tags {[s.get('tagName') for s in steps]}); panel-assigned fraction left blank",
            file=sys.stderr,
        )
    return None


def _aggregate_barcode_metrics(
    stat: pl.DataFrame,
    cell_col: str,
    umi_col: str,
    count_col: str,
    reads_total: int,
    multiplier: float = AGGREGATE_BARCODE_IQR_MULTIPLIER,
    min_umi_threshold: float = AGGREGATE_BARCODE_MIN_THRESHOLD,
    top_n: int = AGGREGATE_BARCODE_TOP_N,
) -> tuple[float | None, int, float | None]:
    """Fraction of reads_total sitting in barcodes `detect_aggregate_barcodes` flags.

    Per-barcode UMI and read totals from the whole whitelist-corrected barcode universe (`stat`, not
    the cell list) feed `detect_aggregate_barcodes`. Returns `(None, 0, None)` only where `reads_total`
    is falsy; otherwise the fraction is always a number, 0.0 where nothing is flagged, so a run that
    checked and found no aggregate reads is never indistinguishable from one that never checked.
    """
    if not reads_total:
        return None, 0, None
    per_barcode = (
        stat.group_by(cell_col)
        .agg(
            pl.col(umi_col).sum().alias("umiCount"),
            pl.col(count_col).sum().alias("readCount"),
        )
        .rename({cell_col: "barcode"})
    )
    flagged, threshold = detect_aggregate_barcodes(
        per_barcode.select("barcode", "umiCount"), multiplier, min_umi_threshold, top_n
    )
    flagged_reads = per_barcode.filter(pl.col("barcode").is_in(flagged))["readCount"].sum() if flagged else 0
    return flagged_reads / reads_total, len(flagged), threshold


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_stat_tsv")
    p.add_argument("--parse-report", required=True)
    p.add_argument("--refine-report", default=None)
    p.add_argument("--sample-id", required=True)
    p.add_argument("--cell-col", default="CELL")
    p.add_argument("--feature-col", default="FEATURE")
    p.add_argument("--umi-col", default="unique_UMI")
    p.add_argument("--count-col", default="count")
    # The aggregate-barcode detection knobs. Defaults mirror qc_measures.py's own constants. Moving any
    # of them changes which barcodes `detect_aggregate_barcodes` flags.
    p.add_argument("--aggregate-iqr-multiplier", type=float, default=AGGREGATE_BARCODE_IQR_MULTIPLIER)
    p.add_argument("--aggregate-min-umi-threshold", type=float, default=AGGREGATE_BARCODE_MIN_THRESHOLD)
    p.add_argument("--aggregate-top-n", type=int, default=AGGREGATE_BARCODE_TOP_N)
    p.add_argument("--output", default="result_qc.csv")
    args = p.parse_args()

    total, matched = _parse_report(args.parse_report)
    stat = pl.read_csv(args.tag_stat_tsv, separator="\t")
    # A header-only tag-stat (every read dropped) has no data rows, so polars infers String for every
    # column. Coerce here, or .sum()/.median() below raise on String arithmetic.
    stat = stat.with_columns(
        pl.col(args.umi_col).cast(pl.Int64),
        pl.col(args.count_col).cast(pl.Int64),
    )

    cells = int(stat[args.cell_col].n_unique())
    features = int(stat[args.feature_col].n_unique())
    total_umis = int(stat[args.umi_col].sum())
    per_cell = stat.group_by(args.cell_col).agg(pl.col(args.umi_col).sum().alias("u"))
    median_umis = float(per_cell["u"].median()) if per_cell.height else 0.0
    assigned = _refine_kept_fraction(args.refine_report, args.feature_col)
    # The same report's CELL step. It corrects each cell barcode against the chemistry's whitelist rather
    # than against the panel, so its kept share is the share of reads whose barcode the chemistry could
    # have produced.
    cell_valid = _refine_kept_fraction(args.refine_report, args.cell_col)
    agg_fraction, agg_flagged, agg_threshold = _aggregate_barcode_metrics(
        stat,
        args.cell_col,
        args.umi_col,
        args.count_col,
        total,
        args.aggregate_iqr_multiplier,
        args.aggregate_min_umi_threshold,
        args.aggregate_top_n,
    )

    row = {
        "sampleId": args.sample_id,
        "readsTotal": total,
        "readsMatched": matched,
        "matchedFraction": (matched / total) if total else 0.0,
        "cellsDetected": cells,
        "featuresDetected": features,
        "totalUniqueUmis": total_umis,
        "medianUmisPerCell": median_umis,
        "panelAssignedFraction": "" if assigned is None else assigned,
        "cellBarcodeValidFraction": "" if cell_valid is None else cell_valid,
        "aggregateBarcodeFraction": "" if agg_fraction is None else agg_fraction,
        "aggregateBarcodesFlagged": agg_flagged,
        "aggregateBarcodeThreshold": "" if agg_threshold is None else agg_threshold,
    }
    with open(args.output, "w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerow(row)

    # Also emit the row as JSON so the model can read per-sample QC (getDataAsJson) to build the block's
    # live "Analysis logs".
    with open("result_qc.json", "w") as jf:
        json.dump(row, jf)


if __name__ == "__main__":
    main()
