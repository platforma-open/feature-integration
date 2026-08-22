"""Per-sample QC summary for the Feature Integration block.

One row per sample: read-level metrics from mitool's parse JSON report
(parseReport.total/.matched), cell/feature/UMI metrics from the tag-stat TSV, and the
panel-assigned fraction from the refine-tags JSON report. panelAssignedFraction is left
blank only when no refine report is available. Stdlib and polars only.
"""

import argparse
import csv
import json
import sys

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


def _refine_assigned_fraction(path: str | None, feature_tag: str = "FEATURE") -> float | None:
    """Panel-assigned fraction from the refine-tags JSON report.

    The feature refine step corrects each barcode against the panel whitelist and drops reads
    whose barcode is not within correction distance of any entry. The fraction is that step's
    ``outputCount / inputCount`` -- the share of reads entering correction that were kept.
    ``feature_tag`` is the mitool tag name to match against the report's ``tagName``.

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
        if step.get("tagName") == feature_tag:
            input_count = step.get("inputCount", 0)
            if not input_count:
                return None
            return step.get("outputCount", 0) / input_count
    # A report with steps but none matching the feature tag means the schema or tag naming
    # drifted. Surface it rather than silently blanking the metric for every sample.
    if steps:
        print(
            f"[qc-report] refine report has no {feature_tag!r} step "
            f"(saw tags {[s.get('tagName') for s in steps]}); panel-assigned fraction left blank",
            file=sys.stderr,
        )
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
    # A header-only tag-stat (every read dropped) has no data rows, so polars infers String
    # for every column. Coerce here, or .sum()/.median() below raise on String arithmetic.
    # Mirrors per_cell_metrics._load.
    stat = stat.with_columns(pl.col(args.umi_col).cast(pl.Int64))

    cells = int(stat[args.cell_col].n_unique())
    features = int(stat[args.feature_col].n_unique())
    total_umis = int(stat[args.umi_col].sum())
    per_cell = stat.group_by(args.cell_col).agg(pl.col(args.umi_col).sum().alias("u"))
    median_umis = float(per_cell["u"].median()) if per_cell.height else 0.0
    assigned = _refine_assigned_fraction(args.refine_report, args.feature_col)

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

    # Also emit the row as JSON so the model can read per-sample QC (getDataAsJson) to build
    # the block's live "Analysis logs": the per-sample completed count and the run summary.
    with open("result_qc.json", "w") as jf:
        json.dump(row, jf)


if __name__ == "__main__":
    main()
