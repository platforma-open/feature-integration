---
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Run quality reads by reagent, and the fitted background is a grid you can enlarge.

The Reagents table leads with the reagent's name rather than its barcode. Every axis of that table now
carries a label column, so no column renders a raw id: a tag reads as its name, an identity as its
antigen, a panel as its panel. A barcode several panels name differently reads as those names joined,
under every grouping — previously it fell back to its own sequence whenever the run grouped by a panel
property.

The fitted background is a grid of small multiples ordered by tag, so one reagent's samples sit side by
side, and any panel enlarges to a dialog. Every count plot now sizes itself to its container; each drew
at a fixed 674px before and overlapped its neighbours. The score and reference plots label their own x
axis, which they had been passing and having ignored.

The exported verdicts drop two columns a reader derives from the pair beside them: cells that read not
bound is `answered - bound`, and cell agreement is that pair read against the state. Both are still
computed, and the punchcard still carries them.
