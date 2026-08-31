---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration': minor
---

The undeclared-barcode Status judges each barcode, not the whole sample

The Status column read the sample's aggregate undeclared share, so it was one
word repeated down every row of a sample. A sample carrying one heavy
undeclared sequence among many light ones said nothing about which sequence to
look at.

Status now reads each row's own share of its sample's pre-refine reads. It warns
above 1% and alerts above 5%. Those two numbers are operator-set and
overridable, not inherited: the field publishes 0.50/1.0 for a sample's
AGGREGATE undeclared share, and that line does not transfer to one sequence,
because an aggregate reaches 0.50 while no single sequence comes near it.

The alert end changed direction with it. It compared for equality, which fired
only at exactly the error threshold and let every larger share read *warn* — the
worse finding being the one that never showed. Both ends now face the same way.

The sample-level share keeps its column, renamed to "Sample Undeclared (%)", and
carries no status.

Every column description in that table was rewritten to one instruction per
sentence, active voice, and short sentences.
