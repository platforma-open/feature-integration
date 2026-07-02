---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Fix per-cell-metrics crash when no (cell, feature) pair survives the tag->feature join (e.g. a wrong
read geometry, or a sample with no on-panel reads). Both empty-input paths now write header-only
CSVs instead of failing the whole per-sample run: the consensus/specificity frames are built with an
explicit schema (an empty row-list otherwise yields a schema-less frame whose `.sort()` raised
`ColumnNotFoundError`), and the UMI-count column is coerced to a numeric type on read (a header-only
tag-stat makes polars infer every column as String, which broke the fraction division).
