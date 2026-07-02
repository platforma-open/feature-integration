---
'@platforma-open/milaboratories.feature-integration.model': patch
---

Harden the model: read the prerun feature/column lists with `getDataAsJsonOrUndefined` instead of
`getDataAsJson` (the latter throws "Resource has no content." while staging is still computing, per
the SDK accessor guidance), and reject in `args()` when the barcode-sequence and feature-name columns
map to the same CSV column (previously only caught by the Python after the full mitool chain ran).
