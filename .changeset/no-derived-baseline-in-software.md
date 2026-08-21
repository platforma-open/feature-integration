---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.model': patch
---

Stop the software picking a baseline rung, and require the choice on the command line.

`what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either.

The software's own three-rung default is removed rather than left unused. Leaving it in place was a trap: the workflow omits `--reference-source` whenever the model's value is empty, so removing the derivation in the model alone would have silently promoted this one to the live rule, deriving in the layer furthest from the reader. `--reference-source` is now required, so there is nothing left to promote.

`served_source` is unaffected and still degrades a rung that cannot serve to *none*, never to a different rung.

The model still derives, which is a known deviation and the last one left. Removing it needs a ruling the spec does not settle — whether a run with nothing selected refuses to start or completes with every verdict that needs a baseline reading unreliable — because the two need different code. The docstring there now states this, and no longer claims such verdicts read *not evaluated*, which is a quality-measurement status and never a verdict state.
