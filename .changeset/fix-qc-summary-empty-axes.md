---
'@platforma-open/milaboratories.feature-integration.workflow': patch
---

Fix the per-cell results table (`perCellTable`) failing to render with `assertion error:
partitionKeyLength (0) must be strictly less than the number of axes (0)`.

The per-sample QC summary was declared as an `Xsv` processColumn output with `axes: []` (empty) and
emitted in the SAME `processColumn` call as the A-0010 contract columns. An xsv import cannot produce a
per-sample scalar (0 within-file axes) — the empty-axes spec tripped `xsv.importFile`'s
`partitionKeyLength < len(axes)` assertion and crashed the entire shared render, so every co-emitted
output (including the valid `consensus`/`tagstatQc`) inherited the error.

The QC summary is now assembled the way `mixcr-clonotyping` builds its QC report:

- `processColumn` collects the per-sample QC CSV as a `[sampleId]` file map (`type: "Resource"`)
  instead of importing it inline. This also fixes a latent bug where the output pointed at a
  non-existent `qcSummary` body path (the fb-pipeline body returns the file under key `qc`).
- A new child template `qc-summary.tpl.tengo` concatenates the per-sample one-row CSVs (injecting the
  real `sampleId` per row from the map key, overwriting the constant the Python writes) and imports the
  combined table ONCE, keyed `[sampleId]`, with the block's sampleId axis so it unifies with the
  contract columns. The 8 typed metric columns keep their value types, labels, order priorities, and
  formats.

`perCellTable` (abundance/fractions/consensus/specificity) is untouched.
