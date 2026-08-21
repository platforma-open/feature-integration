---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Whether the minimum count reaches the baseline tag is now a setting, off by default.

The block exempted the declared baseline tag from the minimum count and hard-coded that. It is now "Apply the minimum count to the baseline tag" under Advanced reading settings, unticked.

**It changes no verdict, and that is checked rather than asserted.** Each baseline source reads its own counts before the minimum, so the level a count is judged against is the same either way. An end-to-end test runs the same bed with the setting off and on and requires the verdicts, the per-cell counts and the per-cell scalars to be byte-identical, while requiring the removed-readings count to differ — so the test cannot pass on a bed where the setting reaches nothing.

What it does change is the run's own accounting: how many readings the run reports as removed, how many cells it reports as emptied, and through those, which of a clonotype's cells count as empty.

The emptied-cell population follows the same switch. With the baseline exempt, a cell holding only a below-minimum baseline reading never had evidence of binding for the minimum to remove. With the baseline subject to it, that cell has been emptied. Scoping the population one way while flooring the other would report a cell as keeping evidence it no longer has.

One stale piece of reasoning is retired with this. The exemption's second stated ground was that flooring the comparator "lowers every denominator and shifts the whole run toward bound". Since each rung reads its own source raw, flooring here reaches no denominator at all, and that clause no longer holds.
