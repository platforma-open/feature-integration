"""Per-sample QC summary for the Feature Integration block.

One row per sample: read-level metrics from mitool's parse JSON report (parseReport.total/.matched),
cell/feature/UMI metrics from the tag-stat TSV, and the panel-assigned fraction from the refine-tags
JSON report (the FEATURE correction step's outputCount / inputCount — the fraction of reads kept after
correcting the feature barcode against the panel whitelist). panelAssignedFraction is left blank only
when no refine report is available. Stdlib + polars only.
"""

import argparse
import csv
import json

import polars as pl

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
]


def _parse_report(path: str) -> tuple[int, int]:
    with open(path) as fh:
        rep = json.load(fh)
    pr = rep.get("parseReport", rep)
    return int(pr.get("total", 0)), int(pr.get("matched", 0))


def _refine_assigned_fraction(path: str | None) -> float | None:
    """Panel-assigned fraction from the refine-tags JSON report.

    The FEATURE refine step corrects each feature barcode against the panel whitelist and drops reads
    whose barcode is not within correction distance of any panel entry. The panel-assigned fraction is
    that step's ``outputCount / inputCount`` — the fraction of reads entering feature correction that
    were kept (assigned to a panel feature).

    Returns None (blank in the CSV) when the report is absent/unreadable, carries no FEATURE step, or
    that step has zero input reads, so QC never crashes on a missing or edge-case report.
    """
    if not path:
        return None
    try:
        with open(path) as fh:
            rep = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for step in rep.get("steps", []):
        if step.get("tagName") == "FEATURE":
            input_count = step.get("inputCount", 0)
            if not input_count:
                return None
            return step.get("outputCount", 0) / input_count
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_stat_tsv")
    p.add_argument("--parse-report", required=True)
    p.add_argument("--refine-report", default=None)
    p.add_argument("--sample-id", required=True)
    p.add_argument("--cell-col", default="CELL")
    p.add_argument("--feature-col", default="FEATURE")
    p.add_argument("--umi-col", default="unique_UMI")
    p.add_argument("--output", default="result_qc.csv")
    args = p.parse_args()

    total, matched = _parse_report(args.parse_report)
    stat = pl.read_csv(args.tag_stat_tsv, separator="\t")

    cells = int(stat[args.cell_col].n_unique())
    features = int(stat[args.feature_col].n_unique())
    total_umis = int(stat[args.umi_col].sum())
    per_cell = stat.group_by(args.cell_col).agg(pl.col(args.umi_col).sum().alias("u"))
    median_umis = float(per_cell["u"].median()) if per_cell.height else 0.0
    assigned = _refine_assigned_fraction(args.refine_report)

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
    }
    with open(args.output, "w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerow(row)

    # High-level analysis log -> stdout, surfaced as the block's single "Analysis logs" view. qc runs
    # last and has every stage's numbers, so it narrates the whole run at milestone level with one key
    # figure per step; full per-sample statistics live on the QC page.
    assigned_txt = "n/a" if assigned is None else f"{assigned:.0%}"
    matched_pct = f"{(matched / total):.0%}" if total else "n/a"
    print("Feature Integration — analysis log")
    print()
    print(f"Parsed feature-barcode reads — {total:,} reads ({matched_pct} matched the read pattern).")
    print(f"Corrected feature barcodes against the panel — {assigned_txt} of reads assigned.")
    print(f"Computed per-cell metrics — {cells:,} cells across {features} features.")
    print("QC report ready.")
    print()
    print("Analysis complete. See the QC page for full per-sample statistics.")


if __name__ == "__main__":
    main()
