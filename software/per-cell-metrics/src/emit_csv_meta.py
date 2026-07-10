"""Emit the tag->feature CSV's column headers and the distinct values of each column as one JSON.

A single staging pre-step, replacing the former emit-columns + emit-features pair.
The block's barcode-sequence / feature-name column dropdowns read ``columns``; the negative-control
dropdown reads ``valuesByColumn[<feature column>]`` for whichever column the user maps to the
feature-name role. Emitting the distinct values of EVERY column up front (the tag->feature CSV is a
small feature panel) lets the control dropdown populate the instant the feature column is picked —
with no second staging exec and no staging rerun; the model just indexes the already-emitted map.

Header order is preserved so the column dropdowns read top-to-bottom like the file; per-column values
are deduplicated and sorted so the output is deterministic (canonical). Stdlib only — trivial and fast.
"""

import argparse
import csv
import json


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_feature_csv", help="tag->feature CSV")
    p.add_argument("output", help="output JSON file ({columns, valuesByColumn})")
    args = p.parse_args()

    with open(args.tag_feature_csv, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            raise SystemExit(f"no header row found in {args.tag_feature_csv}")
        # Preserve header order, drop blank header cells; keep each named column's row index so we can
        # collect its values by position (DictReader would lose order and collapse duplicate headers).
        columns = [h.strip() for h in header if h.strip()]
        col_index = {h.strip(): i for i, h in enumerate(header) if h.strip()}
        values: dict[str, set[str]] = {c: set() for c in columns}
        for row in reader:
            for c in columns:
                i = col_index[c]
                if i < len(row) and row[i].strip():
                    values[c].add(row[i].strip())

    meta = {"columns": columns, "valuesByColumn": {c: sorted(values[c]) for c in columns}}
    with open(args.output, "w") as out:
        json.dump(meta, out)


if __name__ == "__main__":
    main()
