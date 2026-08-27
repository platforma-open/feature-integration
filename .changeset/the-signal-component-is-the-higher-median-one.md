---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

The fitted rung labels its signal component by median, the gate sets aside above its line, and a
sticky count is not reported over no readings.

**The signal component is the higher-median one.** It was the higher-mean one, justified by a comment
saying a negative binomial's medians are ordered by its means. They are not: the median depends on the
size as much as the mean, at mean 50 and size 0.05 it is 0 while at mean 5 and size 1e6 it is 5, and
sizes are re-estimated per component from that component's own variance every round. An ambient
population — mostly zero with a few enormous counts — fits a component whose mean sits far above a real
binder population's while its median sits far below, so the two orderings pick opposite components.
Labelling the wrong one inverts the tag: on the bed now committed, ordering by mean calls four hundred
cells that read nothing bound, at a probability of exactly one. Where both medians are zero, which is
the mostly-silent case, the mean breaks the tie.

**The admissibility gate sets aside a cell above its threshold, not at it.** `reference-two-roles` says
above, and says a cell is set aside where a reading exceeds the threshold — the same direction the
minimum takes from the other side, where a count of four survives a minimum of four. The code, its
CLI help, the QC column and the settings tooltip all said *reaches*. A cell reading exactly the gate
value now stays in and answers.

**Neither form of the sticky measurement reports a number over no readings.** Both are taken over the
cells' own baseline readings, which only a declared baseline tag supplies. Under the tag-distribution
rung no cell has one, and a gated count over an empty population came out `0.0` — a sample reported as
checked and clean on a question the run never put, and disagreeing with the run record, which already
reported nothing for the same condition.
