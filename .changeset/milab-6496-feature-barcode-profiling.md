---
"@platforma-open/milaboratories.feature-integration": minor
"@platforma-open/milaboratories.feature-integration.model": minor
"@platforma-open/milaboratories.feature-integration.ui": minor
"@platforma-open/milaboratories.feature-integration.workflow": patch
"@platforma-open/milaboratories.feature-integration.per-cell-metrics": patch
---

Feature Barcode Profiling — pilot finishing (BEAM in-vivo).

Analysis / functionality:

- Preview (dry-run) mode with a per-file read cap for fast settings checks.
- Multi-barcode antigens: `sum` (OR, default) and `all` (AND — called only where every probe barcode fires) combine modes, declared via an optional tag-CSV combine column plus a Min-UMI advanced field (covers the LIBRA-seq dual-probe design).
- Import per-feature properties from the tag-to-feature CSV's extra columns (A-0026): every column beyond the mapped barcode-sequence and feature-name columns becomes a `pl7.app/feature/property` p-column on the shared feature axis, published as a `featureProperties` export so properties (antigen type, species, pool, ...) ride into VDJ Multiomic Integration and Lead Selection. Generic, no hardcoded schema.
- Off-target-aware consensus plus a "cross-reactive" label, off by default: the controls are exposed, but with no off-target designation set the dominant call is byte-identical to before and the label never appears.

Resource allocation:

- Default per-sample mitool CPU/memory now match the MiXCR blocks (`mixcr-analyze`): parse/refine default to `cpu(16)` and `clamp(gib(64) + size("reads")*4, gib(64), gib(256))`; tag-stat and per-cell-metrics memory floors raised with a 256 GiB cap. Optional per-sample `perProcessCPUs` / `perProcessMemGB` overrides remain in Advanced Settings.
- The polars steps (per-cell-metrics, qc-report) are bound to the granted CPU via `POLARS_MAX_THREADS` so they no longer size their thread pool to all host cores.

UX / logging:

- Live per-sample, per-step logs (mitool parse / refine / tag-stat plus the Python per-cell-metrics step), opened by double-clicking a sample row; a per-step "[N/M]" progress counter that advances through the final Python stage, with the progress label following the live stream.
- The sample column auto-populates when the tag CSV has a column whose values match the dataset's sample names (replacing the manual suggestion banner); the CSV-metadata staging keys on the CSV alone, so the barcode / feature-name dropdowns stay populated across reloads and dataset changes; tag-mapping dropdowns are gated until a tag CSV is loaded.
- Tooltips across every setting, the read-layout fields, and the Quality / Read-recovery columns; the analysis-logs drawer leads with a hint pointing to the richer per-sample logs.

Housekeeping:

- Rename the block display title to "Feature Barcode Profiling" (display only).
- Remove middot/bullet separators from labels, the default subtitle, and the breakdown output.
- Fix the broken mitool reference link in the block description; remove stale planning docs.
