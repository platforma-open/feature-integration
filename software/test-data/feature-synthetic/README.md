# feature-synthetic test bed

Tiny, hand-designed fixtures for the Feature Integration per-cell-metrics tests. Committed (not
generated at test time) and excluded from ruff. Regenerate with `python generate.py`.

- `tags.csv` — tag->feature map (`tag,feature`): `AAAA`→AGX, `CCCC`→BGX, `GGGG`→CTRL.
- `tagstat_main.tsv` — mitool-tag-stat-shaped rows (`CELL`, `FEATURE`, `umi`), one row per observed
  (cell, feature-barcode, UMI) molecule.

Designed so per-cell consensus is hand-computable:

| cell | UMI counts | within-cell fractions | consensus @0.6 |
|------|------------|-----------------------|----------------|
| cell1 | AGX 3, BGX 1 | 0.75 / 0.25 | **AGX** |
| cell2 | AGX 1, BGX 1, CTRL 1 | 0.33 each | **ambiguous** |
| cell3 | AGX 2 | 1.0 | **AGX** |
