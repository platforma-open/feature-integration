---
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Run quality hides its tab strip while the run computes

The tab set is derived from the baseline rung the run reports. Until the run
settles that rung is unknown, so the strip offered every plot and then dropped
the ones the served rung cannot draw — a reader could open a tab that then
stopped existing.

The strip is now hidden while the block computes. The open view's body keeps
rendering and draws its own processing placeholder, so the page still shows the
run's progress.
