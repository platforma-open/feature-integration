"""Emit the feature-barcode panel as a plain sequence list for mitool refine-tags
whitelist correction (``-t FEATURE#file:panel.txt``).

The panel is the tag column of the user's tag->feature CSV: the authoritative set of
feature barcodes. One barcode per line, deduplicated and sorted, so the output is
canonical and the workflow's pure-template dedup stays stable. Standard library only, so
this stays a trivial, fast pre-step.
"""

import argparse
import csv


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_feature_csv", help="tag->feature CSV (columns: tag, feature)")
    p.add_argument("output", help="output panel file (one feature barcode per line)")
    p.add_argument("--tag-col", default="tag", help="feature-barcode column name in the CSV")
    args = p.parse_args()

    with open(args.tag_feature_csv, newline="") as fh:
        reader = csv.DictReader(fh)
        if args.tag_col not in (reader.fieldnames or []):
            raise SystemExit(
                f"column {args.tag_col!r} not found in {args.tag_feature_csv} (columns: {reader.fieldnames})"
            )
        # `or ""` rather than a get() default: a short row's missing columns are
        # present-and-None in DictReader's output, not absent, so the default never fires
        # and .strip() met None. One malformed line took the whole run down.
        seqs = {seq for seq in ((row.get(args.tag_col) or "").strip() for row in reader) if seq}

    if not seqs:
        raise SystemExit(f"no feature barcodes found in column {args.tag_col!r}")

    with open(args.output, "w") as out:
        for seq in sorted(seqs):
            out.write(seq + "\n")


if __name__ == "__main__":
    main()
