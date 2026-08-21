---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Raise the panel rung's member minimum from 8 to 25.

The figure comes from one preprint, whose own panels held 50 and 100 members, and nothing validates it lower. It gates the method rather than tuning it: below it, comparing a count against a handful of other antigens is not a background estimate, so the baseline it permits is not conservative but wrong.

At 8 the rung was within reach of an antibody panel. It is not meant to be — those kits cap at fifteen tags — so a panel that declares no baseline tag no longer stands in as its own background. Such a run reads each tag against its own distribution instead, which is what that source was added for. A run that wants the old behaviour can still lower the setting, and says so wherever its verdicts appear.
