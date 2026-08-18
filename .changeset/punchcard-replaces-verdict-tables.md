---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Replace the Binding verdicts and Quality checks tables with a punchcard

The two result tables are removed as views. A punchcard takes their place: rows are clonotype sets,
columns are the antigen identities picked from a dropdown, and a cell is one punch whose colour is the
verdict and whose size is the support behind it. Every identity is already in the result, so picking one
costs a redraw rather than a run.

Both artifacts are still emitted. The verdicts and the run's own measurements are what the block owes,
and dropping a view does not release it from producing them — the verdicts still export to downstream
blocks, and the quality frames are still built by the workflow. What no longer exists is the pair of
grids that presented them.

The reading itself is unchanged: no threshold, default or verdict moves.

Block data migrates to v4, dropping the three grid states the removed views owned and adding the
punchcard's own state and its identity selection.
