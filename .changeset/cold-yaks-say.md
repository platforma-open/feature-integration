---
"@platforma-open/milaboratories.feature-integration.per-cell-metrics": minor
"@platforma-open/milaboratories.feature-integration.workflow": minor
"@platforma-open/milaboratories.feature-integration": minor
---

Median antigen depth per clonotype, one column per barcode

A new exported family keyed on the clonotype alone, carrying the median UMI count each barcode reached across that clonotype's cells. A cell offered the barcode that read nothing counts zero; cells from samples whose panel never declared it are left out, having never been asked. A median of zero is not stored, so a clonotype with no real depth in a barcode reads blank there rather than as a floor of zeros.

Every barcode the panel declares takes a column, whether or not any clonotype has depth in it, so the column set holds still between runs and a saved layout or a downstream reference never loses one. Selectable as a ranking score in lead selection, and never a default ranking.