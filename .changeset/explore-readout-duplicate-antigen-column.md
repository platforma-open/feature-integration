---
'@platforma-open/milaboratories.feature-integration.model': patch
---

Explore readout: one Antigen column, not two

The by-identity table rendered the identity's name twice. The label is emitted under one spec into
two frames -- `identityLabels` into the verdict export, and `reagentIdentityLabels` into the reagent
frame -- and the reagent frame reaches the block as its own `antigenReagentTable` output, so
`columns: null` discovers that copy. The model also supplied the export's copy as a primary column,
so both rendered.

Neither carries a domain, so no visibility rule tells them apart: the rule that makes the identity's
name visible matches both. Dropping one supplier is the only route, and the discovered copy is the
one that survives -- the reagent frame is built unconditionally beside the verdicts.
