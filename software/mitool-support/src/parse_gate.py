"""Parse gate for the Feature Integration block.

Reads the parse report and writes ``decision.json``, which the workflow branches on.
One or more matched reads: the mitool chain continues. Zero matched reads: refine and
tag-stat are skipped, because they crash on empty input, and this gate writes the empty
outputs they would have produced:

  * ``--empty-tagstat``       header-only TSV with mitool's ``tag-stat -t CELL -t FEATURE
                              -u UMI`` columns. per_cell_metrics and qc_report read it as
                              a 0-cell sample.
  * ``--empty-refine-report`` empty ``{}``. qc_report's panel-assigned fraction goes blank.

Both fallbacks are always written. They are trivial, and the matched>0 path ignores them.
Stdlib only.
"""

import argparse
import json
import sys


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("parse_report", help="mitool parse JSON report (parse_report.json)")
    p.add_argument("decision_out", help="output decision JSON ({total, matched, shouldContinue})")
    p.add_argument("--empty-tagstat", required=True, help="output path for the header-only fallback tag-stat TSV")
    p.add_argument("--empty-refine-report", required=True, help="output path for the empty fallback refine report")
    p.add_argument("--cell-tag", default="CELL", help="mitool cell tag name (tag-stat CELL column)")
    p.add_argument("--feature-tag", default="FEATURE", help="mitool feature tag name (tag-stat FEATURE column)")
    p.add_argument("--umi-tag", default="UMI", help="mitool UMI tag name (tag-stat unique_<UMI> column)")
    args = p.parse_args()

    with open(args.parse_report) as fh:
        rep = json.load(fh)
    # mitool writes {"parseReport": {...}, ...}. Also accept an unwrapped report.
    pr = rep.get("parseReport", rep)
    total = int(pr.get("total", 0))
    matched = int(pr.get("matched", 0))
    should_continue = matched > 0

    if not should_continue:
        # Goes to the exec's stderr. The analysis log flags the zero-cell sample separately.
        print(
            f"[parse-gate] parse matched {matched} of {total} reads — no features will be extracted "
            f"for this sample; check the read geometry / tag pattern against the data",
            file=sys.stderr,
        )

    with open(args.decision_out, "w") as out:
        json.dump({"total": total, "matched": matched, "shouldContinue": should_continue}, out)

    # Header-only fallback with mitool `tag-stat -t CELL -t FEATURE -u UMI` columns, so
    # per_cell_metrics and qc_report see a well-formed empty table.
    header = f"{args.cell_tag}\t{args.feature_tag}\tcount\ttotalWeight\tunique_{args.umi_tag}\n"
    with open(args.empty_tagstat, "w") as out:
        out.write(header)

    with open(args.empty_refine_report, "w") as out:
        out.write("{}")


if __name__ == "__main__":
    main()
