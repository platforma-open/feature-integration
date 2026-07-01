---
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

Fix a pre-Run deadlock that left the block unable to run after the D4 column-mapping UI was added.

The barcode/feature column dropdowns (`csvColumnOptions` / `controlOptions`) are populated by the
prerun (staging) reading the uploaded tag→feature CSV, and their values are required by `args()`. But
the CSV upload was driven only from the main (production) render via `getImportProgress`, which is
unreachable until `args()` passes — so nothing uploaded the CSV before Run, the prerun's
`emit-columns` step never ran, the dropdowns stayed empty, `args()` kept throwing, and Run stayed
disabled: a circular dependency.

- Prerun (`prerun.tpl.tengo`) now exposes the CSV import handle (`tagFeatureCsvImportHandle:
  csvImport.handle`).
- Model adds a second `getImportProgress` driver resolved from `ctx.prerun` (`isActive: true`),
  mirroring `samples-and-data`'s "drives prerun file uploads" driver, so the CSV is uploaded during
  staging. The dropdowns then populate, `args()` passes, and Run enables.
