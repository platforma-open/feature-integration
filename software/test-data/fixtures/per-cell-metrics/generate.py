"""Regenerate the synthetic feature-barcode test bed.

Run from this directory:  python generate.py

The bed mirrors the output of `mitool tag-stat -t CELL -t FEATURE -u UMI`: one row per
(cell, feature-barcode) group, columns `CELL FEATURE count totalWeight unique_UMI`, ordered by the
distinct-UMI count descending. `unique_UMI` is the distinct-molecule count mitool computes; `count`
(raw read occurrences) is deliberately larger than `unique_UMI` so the tests prove the metrics read
the deduplicated `unique_UMI` column, not the raw read count.

It is intentionally tiny and hand-designed so per-cell metrics are hand-computable (on unique_UMI):
  cell1 -> dominant on AGX (3 vs 1 UMIs, 0.75 share)
  cell2 -> ambiguous (1/1/1 across AGX/BGX/CTRL)
  cell3 -> single feature AGX (2 UMIs)
"""

TAGS = [("AAAA", "AGX"), ("CCCC", "BGX"), ("GGGG", "CTRL")]

# (CELL, feature-barcode, count, totalWeight, unique_UMI) -- one row per (cell, feature) group, as
# mitool `tag-stat -u` emits (ordered by unique_UMI desc, then group key). count > unique_UMI models
# PCR duplicates so the metrics provably use the deduplicated count.
ROWS = [
    ("cell1", "AAAA", 7, 7, 3),
    ("cell3", "AAAA", 5, 5, 2),
    ("cell1", "CCCC", 2, 2, 1),
    ("cell2", "AAAA", 4, 4, 1),
    ("cell2", "CCCC", 3, 3, 1),
    ("cell2", "GGGG", 6, 6, 1),
]


def main() -> None:
    with open("tags.csv", "w") as f:
        f.write("tag,feature\n")
        for tag, feature in TAGS:
            f.write(f"{tag},{feature}\n")
    with open("tagstat_main.tsv", "w") as f:
        f.write("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n")
        for cell, fb, count, weight, uniq in ROWS:
            f.write(f"{cell}\t{fb}\t{count}\t{weight}\t{uniq}\n")


if __name__ == "__main__":
    main()
