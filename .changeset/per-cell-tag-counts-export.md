---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Per-cell antigen counts are exported, before the minimum is applied

A new export, keyed `[sampleId, cellId, tagId]`, carrying the UMI count each cell held for each
barcode. It is what a downstream per-cell composition plot needs: one bar per cell, split by antigen.

**Before the count minimum, and that is the point.** The counts the verdicts are computed on have
already had every value below the minimum set to zero, and the comparator tag is exempt from that —
so on a declared-baseline run the control keeps its small counts while the antigens lose theirs. Those
numbers answer "what counted as evidence of binding". This export answers "what did the cell capture",
so a cell's tags add up to what that cell actually held. The column's own description says so, because
the two do not reconcile and a reader who mixes them draws the wrong conclusion.

It cannot be derived from the floored counts afterwards: once the minimum has run, a count of 3 and a
count that was never there are both 0.

Partitioned by sample, the only column in the block that is. It is the largest table the run produces,
at one row per (cell, tag), and a composition plot reads one sample per view — so a reader after one
sample touches one partition instead of the whole run.
