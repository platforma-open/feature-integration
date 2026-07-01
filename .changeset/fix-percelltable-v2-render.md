---
'@platforma-open/milaboratories.feature-integration.model': patch
---

Render perCellTable and tagstatQcTable with createPlDataTableV2 instead of createPlDataTableV3.

Both are the block's own self-contained, non-batch `processColumn` frames. `createPlDataTableV3`'s
discovery cannot render them: the object (scoped-sources) form returns undefined regardless of
anchor/maxHops config, and the array-columns form runs `discoverLabelColumnVariants` over the whole
result pool and hangs on the upstream Samples & Data FASTQ File-dataset (`no_data:…:pf.dataset.*`).
`createPlDataTableV2(ctx, pCols, state)` takes the columns directly (via `getPColumns()`) and renders
the mixed-granularity join — `umiCount`/`fraction` per `[sampleId, cellId, featureId]` with
`consensusFeature` broadcast per `[sampleId, cellId]` — the same pattern `peptide-extraction` uses for
its non-batch processColumn results table. No spec/data change; the exported A-0010 contract columns
are untouched. qcSummaryTable stays on V3 (its single-import frame renders fine).
