"""Parse gate for the Feature Integration block.

This gate reads the parse report and emits a ``decision.json`` the workflow branches on:
continue the mitool chain only when at least one read matched; otherwise skip
refine/tag-stat (they would crash on the empty input) and feed the empty fallbacks this
gate also writes into the unchanged downstream:

  * ``--empty-tagstat``       a header-only tag-stat TSV (the columns mitool ``tag-stat -t CELL -t
                              FEATURE -u UMI`` emits), which per_cell_metrics / qc_report already treat
                              as an empty (0-cell) sample;
  * ``--empty-refine-report`` an empty ``{}`` refine report, so qc_report's panel-assigned fraction is
                              simply blank.

The fallbacks are written unconditionally (they are trivial and are ignored on the matched>0 path, where
real refine/tag-stat outputs are used instead). Stdlib only -- trivial and fast.
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
    # mitool writes {"parseReport": {"total", "matched", ...}, ...}. Tolerate an unwrapped report too.
    pr = rep.get("parseReport", rep)
    total = int(pr.get("total", 0))
    matched = int(pr.get("matched", 0))
    should_continue = matched > 0

    if not should_continue:
        # Surfaced in the exec's stderr. The block's analysis log separately flags the zero-cell sample.
        print(
            f"[parse-gate] parse matched {matched} of {total} reads — no features will be extracted "
            f"for this sample; check the read geometry / tag pattern against the data",
            file=sys.stderr,
        )

    with open(args.decision_out, "w") as out:
        json.dump({"total": total, "matched": matched, "shouldContinue": should_continue}, out)

    # Header-only tag-stat fallback: exactly the columns mitool `tag-stat -t CELL -t FEATURE -u UMI`
    # emits, so the downstream per_cell_metrics / qc_report see a well-formed empty table.
    header = f"{args.cell_tag}\t{args.feature_tag}\tcount\ttotalWeight\tunique_{args.umi_tag}\n"
    with open(args.empty_tagstat, "w") as out:
        out.write(header)

    with open(args.empty_refine_report, "w") as out:
        out.write("{}")


if __name__ == "__main__":
    main()
