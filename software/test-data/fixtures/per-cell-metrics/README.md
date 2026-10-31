# per-cell-metrics test bed

Tiny, hand-designed fixtures for the Feature Integration per-cell-metrics tests. Committed (not
generated at test time) and excluded from ruff. Regenerate with `python generate.py`.

- `tags.csv` — tag->feature map (`tag,feature`): `AAAA`→AGX, `CCCC`→BGX, `GGGG`→CTRL.
- `tagstat_main.tsv` — output shape of `mitool tag-stat -t CELL -t FEATURE -u UMI`: one row per
  (cell, feature-barcode) group, columns `CELL FEATURE count totalWeight unique_UMI`, ordered by
  `unique_UMI` descending. `unique_UMI` is the distinct-UMI count mitool computes; `count` (raw read
  occurrences) is deliberately > `unique_UMI` so the tests prove the metrics read the deduplicated
  `unique_UMI` column, not the raw read count.

Designed so per-cell metrics are hand-computable (on `unique_UMI`):

| cell | unique_UMI (count) | UMI counts | within-cell fractions | consensus @0.6 |
|------|--------------------|------------|-----------------------|----------------|
| cell1 | AGX 3 (7), BGX 1 (2) | AGX 3, BGX 1 | 0.75 / 0.25 | **AGX** |
| cell2 | AGX 1 (4), BGX 1 (3), CTRL 1 (6) | 1 / 1 / 1 | 0.33 each | **ambiguous** |
| cell3 | AGX 2 (5) | AGX 2 | 1.0 | **AGX** |
