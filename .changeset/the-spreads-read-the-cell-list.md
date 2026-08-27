---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

The score and reference spreads read the cell list, like every other per-cell figure.

The two plots a scientist places the cutoff and the gate from were computed over every analysed
barcode, while the per-tag count histograms beside them on the same page were narrowed to the cell
list. On a run with 378,163 analysed barcodes against a 2,553-cell list, the cutoff plot was 99.3%
ambient and unusable, and two populations sat side by side in one `_qc_tag_bins.json`.

Both spreads and both decile series now go through `_listed`, the same narrowing the count plots use.
The reference readings are narrowed by key rather than by join, because the comparator is a dict keyed
by cell; where a list arrived and no listed cell carries a comparator, no rows are written rather than
an all-zero spread. A run with no cell list is unchanged — every barcode is kept, and `cellListSource`
in the run record says which case a figure was computed under.

`bin_counts` now calls `_listed` instead of repeating its join inline.
