---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Compute `panelAssignedFraction` in the per-sample QC report (was always blank).

`_refine_assigned_fraction` was a stub that loaded the refine-tags JSON report but never extracted a
value, so the QC summary's `panelAssignedFraction` column was always empty. It now reads the FEATURE
correction step's `outputCount / inputCount` — the fraction of reads kept after correcting the feature
barcode against the panel whitelist (i.e. assigned to a panel feature). It still falls back to blank
when no refine report is available, the report has no FEATURE step, or that step has zero input reads.
Covered by new behavioral tests in `test_qc_report.py`.
