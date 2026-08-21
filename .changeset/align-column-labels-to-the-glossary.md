---
'@platforma-open/milaboratories.feature-integration.workflow': patch
---

Align the exported columns' reader-facing labels to the spec glossary.

Labels and descriptions only. No p-column name, domain or axis spec changes, so nothing downstream re-binds and no column identity moves.

- **"Reference count" → "Baseline reading"** on the per-cell comparator. The glossary defines *baseline* as the reading a count is measured against, and *reference* as the tag rather than the reading. Every other reader-facing surface moved to "baseline" already; this one lives in the workflow and was missed.
- **"Antigens bound / offered / settled / unsettled" → "Identities …"** on the clonotype counts. They count identities, never tags — the module's own comment said so two lines above the labels — and the glossary separates an identity, "a group of tags read as one thing", from the antigen a tag carries.
- **The cell-grain sibling becomes "Identities this cell bound"**, because the clonotype-grain count now carries the plain name. The two are different numbers over different populations, and a reader meeting both under one name would take the smaller for a subset of the larger, which it is not.
- **"Panel" → "Panel used"** on the per-sample column. The panel axis's own label column is the other "Panel", and it names the panel itself.
- **"Measured thing" → "Measurement subject"**, and two Title Case stragglers to sentence case.

Checked while doing this and deliberately left alone: "Feature" and "Tag" are not two names for one layer. The feature axis keys by antigen name and the tag axis carries barcode sequences, they are separate axes on purpose, and the module already guards against a new column keying on the legacy feature axis while holding barcodes.

No two exported columns now share a label.
