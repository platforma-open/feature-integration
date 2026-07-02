---
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Standardize the sidebar subtitle to the block-label pattern, reflecting the current inputs.

The subtitle now reads `data.defaultBlockLabel` (falling back to a static string), which a UI
watchEffect mirrors from a new `suggestedBlockLabel` model output — a dynamic
`"<dataset> · <barcode> → <feature>"` string derived from the selected FASTQ dataset (resolved from
the result pool by ref), the barcode-sequence column, and the feature-name column. Each part is
dropped until set, so the subtitle updates as the block is configured. The derivation lives in the
output (not `.subtitle`) because the subtitle render context has no result pool. Replaces the previous
control-feature-only subtitle.
