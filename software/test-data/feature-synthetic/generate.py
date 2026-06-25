"""Regenerate the synthetic feature-barcode test bed.

Run from this directory:  python generate.py

The bed is intentionally tiny and hand-designed so per-cell metrics are hand-computable:
  cell1 -> dominant on AGX (3 vs 1 UMIs, 0.75 share)
  cell2 -> ambiguous (1/1/1 across AGX/BGX/CTRL)
  cell3 -> single feature AGX
"""

TAGS = [("AAAA", "AGX"), ("CCCC", "BGX"), ("GGGG", "CTRL")]

# (CELL, feature-barcode sequence, UMI) -- one row per observed molecule.
ROWS = [
    ("cell1", "AAAA", "U1"),
    ("cell1", "AAAA", "U2"),
    ("cell1", "AAAA", "U3"),
    ("cell1", "CCCC", "U4"),
    ("cell2", "AAAA", "U5"),
    ("cell2", "CCCC", "U6"),
    ("cell2", "GGGG", "U7"),
    ("cell3", "AAAA", "U8"),
    ("cell3", "AAAA", "U9"),
]


def main() -> None:
    with open("tags.csv", "w") as f:
        f.write("tag,feature\n")
        for tag, feature in TAGS:
            f.write(f"{tag},{feature}\n")
    with open("tagstat_main.tsv", "w") as f:
        f.write("CELL\tFEATURE\tumi\n")
        for cell, fb, umi in ROWS:
            f.write(f"{cell}\t{fb}\t{umi}\n")


if __name__ == "__main__":
    main()
