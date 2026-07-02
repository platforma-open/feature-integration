---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Vectorize the consensus and specificity computations in per-cell-metrics. Both previously looped in
Python over every cell (consensus) and every (cell, feature) row (specificity, via `iter_rows` +
a list of dicts), which was slow and held a Python-object copy of the data on top of the polars frame
-- the first thing to OOM on large samples. They are now pure-polars/numpy column operations
(group-by + window for the dominant-category rule; scipy `beta.cdf` applied to whole columns for the
score). Output is byte-identical (guarded by the golden consensus test plus new oracle tests that
cross-check both vectorized paths against the pure `consensus_category`/`specificity_score` rules).
Measured: 1.2M (cell, feature) rows with a control now process in ~0.9 s at ~0.7 GB. The empty-input
path is unchanged (header-only CSVs), now via polars schema-preservation rather than an explicit schema.
