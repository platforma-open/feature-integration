"""Emit the list of feature/antigen names from the tag->feature CSV as a JSON array.

Feeds the block's "negative-control feature" dropdown (spec A-0014): the model reads this
list (via the prerun) and offers each feature name as a control option. We read the CSV's
feature column (spec A-0004, A-0009), deduplicate, and sort so the output is deterministic
(canonical) — the same CSV always yields the same option list. Only the standard library is
used (no polars), matching emit_panel.py, so this stays a trivial, fast staging pre-step.
"""

import argparse
import csv
import json


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_feature_csv", help="tag->feature CSV (columns: tag, feature)")
    p.add_argument("output", help="output JSON file (array of feature names)")
    p.add_argument("--feature-col", default="feature", help="feature-name column in the CSV")
    args = p.parse_args()

    with open(args.tag_feature_csv, newline="") as fh:
        reader = csv.DictReader(fh)
        if args.feature_col not in (reader.fieldnames or []):
            raise SystemExit(
                f"column {args.feature_col!r} not found in {args.tag_feature_csv} (columns: {reader.fieldnames})"
            )
        names = {row[args.feature_col].strip() for row in reader if row.get(args.feature_col, "").strip()}

    with open(args.output, "w") as out:
        json.dump(sorted(names), out)


if __name__ == "__main__":
    main()
