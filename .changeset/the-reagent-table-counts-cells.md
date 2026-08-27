---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

The reagent table counts cells, and an unreadable sibling does not vote.

**The per-tag figures are scoped to the cell list.** How many cells held any count of a tag, how many
it called bound, and the median over the cells holding one were taken over every observed barcode.
Ambient reagent reaches most barcodes, so observed barcodes outnumber cells by one to two orders of
magnitude while carrying one or two counts each — and the median a reagent delivers is how this table
reports a reagent working under the level at which anything is credited. On a real run every tag in the
panel read as a failed reagent. The figures now say which cell list they were computed against, since
two runs whose lists came from different sources do not share a denominator.

**A sibling with no settled reading no longer votes.** The rule is to put every tag of an identity in
bound or not bound on its own count, and a cell whose reading was gated or had no comparator is in
neither. Counting *unreliable* as a state let it form a sibling majority, so a tag that fitted where its
siblings did not read as differing from all of them — reported as the panel's worst reagent, and its
only working one in fact. With a gate on, the same bug diluted every real rate by the share of cells the
gate set aside.

**A fraction with no denominator prints blank rather than zero.** The matched-read fraction of a sample
whose reads never arrived, and the median UMIs per cell of a sample holding no cell, both printed
`0.00` — beside a neighbour's `0.98` that reads as a library that failed rather than one that is
missing.

**Two QC descriptions said the wrong denominator.** The usable-antigen-read fraction and the
aggregate-barcode read fraction are both taken over the sample's total reads, and both column
descriptions said *over reads matched*; the first also asserted a UMI-validity condition it does not
carry. Both are measurements with published alert lines, so a reader hovering the column that alerted
was told to divide by the wrong number.

**The sample report shows each measurement's detail.** It was declared and populated and never
rendered — which is what made the sticky measurement unreadable, since a count of cells above a declared
gate and the median of the readings where none is declared arrive in the same column and only the
detail says which. Every distribution-shaped measurement's deciles ride there too.
