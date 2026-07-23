---
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Emit the negative control on the feature axis. The chosen control feature is now surfaced as a dedicated hidden per-feature marker (`pl7.app/feature/negativeControl`), so VDJ Multiomic Integration can remove the control from its antigen metrics (restriction index, antigen breadth, per-antigen fraction columns, and the dominant call). No user-facing change — the marker is hidden and is not offered as a per-feature property.
