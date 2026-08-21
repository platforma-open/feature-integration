---
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Offer the tag-distribution baseline as a source, with its two conditions as settings.

"Each tag's own distribution" joins the baseline dropdown. It reads each count against the lower of two components fitted to that tag's counts across the sample's cells, which is what serves a panel that declares no baseline tag and is too small to stand in for one — the shape every antibody kit has, since they cap at fifteen tags.

It is the only source offered unconditionally. Whether it can serve turns on the sample's cell count and on whether each tag's counts separate, and the second is answered per tag rather than per run, so the conditions are stated in the option's description and the run reports what it managed: which tags fitted, which did not, and why. A tag that did not separate takes only the antigens it carries with it; every other antigen in the same cells is answered normally.

Two new settings under "Baseline thresholds": the cells a sample needs before the rung may serve (300, from the study the method comes from) and how deep the dip between the two components must be (0.5, this block's choice — nothing published sets it). Both are sent on every run, so the record states the numbers a reading would have used whichever source served.
