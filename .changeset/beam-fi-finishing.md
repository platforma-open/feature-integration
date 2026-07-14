---
"@platforma-open/milaboratories.feature-integration": patch
"@platforma-open/milaboratories.feature-integration.model": patch
"@platforma-open/milaboratories.feature-integration.ui": patch
"@platforma-open/milaboratories.feature-integration.workflow": patch
---

Feature Barcode Profiling finishing polish:

- Rename the block display title to "Feature Barcode Profiling" (display only).
- Strip dots from the default block subtitle.
- Add tooltips to every remaining setting (dataset, tag-feature CSV, barcode / feature / negative-control columns, read-layout preset).
- Disable and dim the CSV-derived tag-mapping dropdowns until a tag-feature CSV is loaded.
- Show a per-step "[N/M]" progress counter that advances through the final per-cell-metrics stage instead of an indeterminate "Computing metrics".
- Expose optional per-sample mitool CPU / memory overrides in Advanced Settings.
- Fix the broken mitool reference link in the block description.
