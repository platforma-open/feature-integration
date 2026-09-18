---
'@platforma-open/milaboratories.feature-integration': minor
---

Upgrade to structure v3 and the latest SDK (model/ui 1.83, workflow-tengo 6.10,
tengo-builder 4.1, block-tools 2.16).

Adds the now-mandatory block kind. Its init-params contract is the block's two
inputs, the panel mapping, the read geometry and the binding-reading knobs, so a
project template can seed a runnable configuration; view state and the QC
thresholds stay out of it. `ReferenceSource` and `GroupingRule` move to the kind
package and are re-exported from the model, where every reader finds them.
