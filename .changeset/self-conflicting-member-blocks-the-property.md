---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

A panel member that contradicts itself no longer lets one member's declaration stand for the whole identity.

A property holds of a grouped identity only where its member tags agree. A tag whose own rows contradict each other has no agreed value, so it reached that test as an empty string and was filtered out exactly like a tag whose cell was blank — and a blank member is deliberately not allowed to veto its neighbours.

On a panel with barcode reuse that inverts the outcome. Measured on a real sixteen-row panel grouped on its role column: an identity whose five member tags declared six different antigen names between them came back carrying **one member's name**, because four of the five had contradicted themselves into silence and the survivor then agreed with nobody but itself. Nothing in the export marked it partial.

A member that contradicted itself is a disagreement, not a silence, and now blocks the property. That is the direction the tag-grain rule already takes — it keeps disagreements rather than dropping them, because with barcode reuse an inconsistent declaration is the expected case and dropping it silently breaks the panel file's no-silent-drop rule. This stops that guarantee being undone one grain higher.

Strictly more omission, never more assertion. Panels with heavy barcode reuse will carry fewer declarations on grouped identities than before, which is the correction rather than a side effect. A member that genuinely declared nothing still does not block its neighbours, and a column the identity was grouped on is still settled by construction.

No new computation: the call site already built the disagreement map for its warnings and simply did not pass it down.
