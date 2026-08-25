---
'@platforma-open/milaboratories.feature-integration.ui': minor
---

Every setting is named from the spec glossary, and the form is grouped by what a setting decides.

Seven labels change. "Admissibility gate (baseline UMIs)" and "Min UMIs per barcode" say **unique counts**, which is the glossary's word for a count of distinct molecules. "Minimum cell agreement" becomes "Share of voting cells that must agree", since the glossary's term is a **vote**. "Bound cutoff" becomes "Score at which a cell reads bound", naming the **score** it acts on. "Panel columns that define an identity" becomes "Panel columns that group tags into identities", naming the **grouping**. "High baseline reading" becomes "Line where a baseline reading counts as high", which says it is a line rather than a reading. "Cells needed to fit a tag's own distribution" becomes "Cells a sample needs for this baseline", dropping the method jargon.

Four moves. The sample column joins the other panel-column pickers at the top, where it belongs: it is a panel input, and it was sitting below the baseline among the reading settings. The baseline's own cell condition moves up beside the baseline choice, because it is a condition on that choice. "Optional settings" becomes **The reading** and takes the agreement share, which was alone behind an "Advanced reading settings" accordion that now goes. The "Baseline thresholds" accordion goes too: the gate and the high-reading line both read a cell's own baseline reading, so both exist only under a declared tag, and they now sit in the baseline section with the rung they belong to. A header for two fields that vanish with the rung above them was a section the reader met empty more often than not.

Both of those fields were gated on "not the fitted rung", which also showed them before any baseline was chosen. They are now gated on the declared rung itself.

Three tooltips follow the same words, and the sticky-cell one now says what sticky means and that counting such cells is a measurement while setting them aside is the gate's job.
