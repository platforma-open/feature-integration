"""Per-sample QC summary for the Feature Integration block.

One row per sample: read-level metrics from mitool's parse JSON report (parseReport.total/.matched),
cell/feature/UMI metrics from the tag-stat TSV, the panel-assigned and antigen-dropped shares from the
refine-tags JSON report, and the aggregate-barcode read fraction
(`qc_measures.detect_aggregate_barcodes`) computed from the tag-stat TSV's per-barcode UMI and read
totals.

EVERY READ SHARE HERE IS OVER MATCHED READS, and that is load-bearing: it lets any two of them be
subtracted. A share taken over one refine-tags step's own input cannot, because each level is fed from
the previous level's survivors, so no two steps share a denominator.
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
    "featureDroppedShare",
    "cellBarcodeValidFraction",
    # The two counts behind the share above, so the sample's own report can state them. The share alone
    # cannot: 0.98 over 400 reads and 0.98 over 40M are the same number and not the same finding.
    "cellBarcodeIn",
    "cellBarcodeOut",
    "aggregateBarcodeFraction",
    "aggregateBarcodesFlagged",
    "aggregateBarcodeThreshold",
]


def _parse_report(path: str) -> tuple[int, int]:
    with open(path) as fh:
        rep = json.load(fh)
    pr = rep.get("parseReport", rep)
    return int(pr.get("total", 0)), int(pr.get("matched", 0))


def _refine_step_counts(path: str | None, tag_name: str) -> tuple[int, int] | None:
    """One refine-tags step's ``(inputCount, outputCount)``, or None where it cannot be read.

    The COUNTS, not the ratio, because two of this file's figures divide them by `readsMatched`
    rather than by the step's own input. Each level of refine-tags is fed from the previous level's
    survivors -- the tag order is CELL, FEATURE, UMI -- so the FEATURE step's `inputCount` is what
    survived cell-barcode correction, NOT the matched-read count. A figure built on that input is
    over a denominator no other read-level figure here shares, and subtracting two such figures is
    what produced a unit error in `rescued_share`. One denominator for every read share removes the
    class rather than the instance.

    Returns None where the report is absent or unreadable, or carries no matching step.
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
            return int(step.get("inputCount", 0)), int(step.get("outputCount", 0))
    if steps:
        print(
            f"[qc-report] refine report has no {tag_name!r} step "
            f"(saw tags {[s.get('tagName') for s in steps]}); its figures are left blank",
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
    # Blank, never 0.0. A measurement the run could not supply the
    # inputs for as its reason rather than as a number, and a blank and a zero are opposite
    # findings: a sample whose reads never arrived would otherwise sit beside its neighbours
    # reading a median of nothing, which is a library that failed rather than a library missing.
    median_umis = float(per_cell["u"].median()) if per_cell.height else ""
    # Both FEATURE figures are over MATCHED READS, so every read share this file writes shares one
    # denominator and any two of them can be subtracted. `panelAssignedFraction` is what reached the
    # panel; `featureDroppedShare` is what the antigen-barcode step could not place there. They do not
    # sum to 1 -- the cell-barcode and UMI steps take their own reads in between, which is exactly the
    # information a single ratio over the step's own input threw away.
    feature_counts = _refine_step_counts(args.refine_report, args.feature_col)
    assigned = None
    feature_dropped = None
    if feature_counts is not None and matched:
        feature_in, feature_out = feature_counts
        assigned = feature_out / matched
        feature_dropped = (feature_in - feature_out) / matched
    # The same report's CELL step. No chemistry whitelist is selectable here, so the step runs de-novo
    # correction and drops a read whose barcode is too poor in quality to place -- its kept share is a
    # read-QUALITY share, which is what the measurement's label and route now say.
    #
    # The counts and the share come from ONE read of the report, and the share is derived from the counts
    # rather than read separately: two figures a reader sees side by side must not be able to disagree.
    cell_counts = _refine_step_counts(args.refine_report, args.cell_col)
    cell_in, cell_out = cell_counts if cell_counts is not None else (None, None)
    cell_valid = (cell_out / cell_in) if cell_in else None
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
        "matchedFraction": (matched / total) if total else "",
        "cellsDetected": cells,
        "featuresDetected": features,
        "totalUniqueUmis": total_umis,
        "medianUmisPerCell": median_umis,
        "panelAssignedFraction": "" if assigned is None else assigned,
        "featureDroppedShare": "" if feature_dropped is None else feature_dropped,
        "cellBarcodeValidFraction": "" if cell_valid is None else cell_valid,
        "cellBarcodeIn": "" if cell_in is None else cell_in,
        "cellBarcodeOut": "" if cell_out is None else cell_out,
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
