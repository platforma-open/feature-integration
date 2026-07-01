---
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Feature-parity enhancements from a review against recent blocks.

- Negative-control dropdown now works: staging parses the tag→feature CSV (`emit-features` entrypoint)
  and the model exposes the discovered feature names as `controlOptions` (was an empty stub).
- Robustness: `tag-stat -u` runs with `--use-local-temp` (avoids shared /tmp exhaustion on the on-disk
  sort); mitool `parse`/`refine-tags` memory is sized from the input reads' blob size via `memFormula`
  (clamped, with the metaExtra floor as fallback) instead of a fixed request.
- Observability: per-sample × per-step mitool/Python logs, an `isRunning` spinner signal, and a raw
  `tag-stat` QC table are surfaced on a new "QC" page (`PlLogView` + `PlAgDataTableV2`).
- UI/model polish: results table is now `retentive` + `withStatus` (no flicker on recompute); the
  tag→feature CSV is validated client-side (required columns) with a feature-count preview; a dynamic
  subtitle reflects the chosen control feature.
- Export column specs moved to `column-specs.lib.tengo` with the standard abundance/order/visibility
  annotations (identity-neutral — the downstream contract is unchanged).
