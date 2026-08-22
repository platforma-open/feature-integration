---
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

A panel may mark several control features, not one.

"Control feature marker (output only)" was a single-select. It is now "Control feature markers (output only)", a multi-select, and every chosen feature is marked in the `pl7.app/feature/negativeControl` column that downstream reads.

`040-glossary` separates the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference that supplies the baseline is a job given to exactly one of them. This setting marks controls and nominates nothing, so it takes as many as the panel has. The nomination is `referenceValues`, which stays singular.

`--control-feature` is now repeatable. It is repeated rather than comma-joined because a feature name may contain a comma, and joining would split one name into two features that do not exist. Duplicates are dropped, so a feature is marked once and the axis it keys on cannot carry it twice.

`controlFeature` is the shape a project saved before the setting took a list. It is still read: every reader goes through `controlFeatures()`, which reads either, so no stored project needs a migration. Nothing writes the singular form now.
