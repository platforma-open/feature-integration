"""Emit a tag->feature CSV's column headers as a JSON array.

Feeds the block's barcode-sequence / feature-name column dropdowns (D4): the model reads this list
(via the prerun) so the user maps which column is which, instead of a hardcoded schema. Header order
is preserved so the dropdowns read top-to-bottom like the file. Stdlib only (matches emit_panel.py /
emit_features.py) — a trivial, fast staging pre-step.
"""

import argparse
import csv
import json


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_feature_csv", help="tag->feature CSV")
    p.add_argument("output", help="output JSON file (array of column names)")
    args = p.parse_args()

    with open(args.tag_feature_csv, newline="") as fh:
        header = next(csv.reader(fh), None)
    if not header:
        raise SystemExit(f"no header row found in {args.tag_feature_csv}")

    with open(args.output, "w") as out:
        json.dump([h.strip() for h in header], out)


if __name__ == "__main__":
    main()
