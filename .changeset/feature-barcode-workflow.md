---
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Implement the feature-barcode workflow (plan Tasks 3–4).

- Workflow: per-sample mitool pipeline (`parse → refine-tags → tag-stat -u`) over the feature-barcode
  FASTQs, then the per-cell-metrics Python software, importing the per-cell results as the A-0010
  contract p-columns keyed `[pl7.app/sampleId, pl7.app/sc/cellId, pl7.app/feature/featureId]`
  (`umiCount` / `fraction` / `consensusFeature` / optional `specificityScore`), exported to the
  result pool for VDJ Multiomic Integration.
- Tag pattern: cell barcode `CELL`, feature barcode `FEATURE` (mitool's first-class feature tag type,
  mitool#86), molecule `UMI`. Read geometry is configurable (cellLen/umiLen/featureLen, 10x 5' v2
  defaults) — DP-1 "parameterize + proceed".
- Feature-barcode error correction: `refine-tags` corrects `FEATURE` against the panel whitelist (the
  tag column of the user's tag→feature CSV, emitted as `panel.txt` by the per-cell-metrics
  `emit-panel` entrypoint) — within-Hamming-1 reads snap to a panel barcode and off-panel reads are
  dropped (mitool#87 fixed the `-t TAG#file:` whitelist CLI). `CELL` is de-novo corrected; the tag
  order scopes UMI deduplication to `(cell, feature)`.
- Python `per_cell_metrics._load()` consumes mitool's aggregated `tag-stat -u` output (the
  pre-computed `unique_UMI` distinct-molecule count) instead of counting raw UMI rows — DP-2.
