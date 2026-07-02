---
'@platforma-open/milaboratories.feature-integration.workflow': patch
---

Size the tag-stat and per-cell-metrics steps' memory from input volume (memFormula, base 8 GiB +
input-blob × multiplier, clamped to 128 GiB) instead of a fixed 8 GiB, mirroring the parse/refine
steps. tag-stat is sized by the refined.mic blob, per-cell-metrics by the tag-stat TSV it reads into
polars. Prevents the two input-sized steps from OOMing on large samples where the fixed 8 GiB could
not grow with the data. The Advanced-Settings per-process override still applies to parse/refine only.
