---
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Count plots are drawn over the cell list, and each reads on the axis its number is typed on.

The binned count distributions now count the cell list rather than every barcode the reads touched. In
droplet data the observed barcodes outnumber the cells by one to two orders of magnitude, because ambient
reads land on most barcodes, so an ambient population that size was the only hump any panel showed. The
shared edge list is taken from the same filtered counts, so the axis ends at the highest count among cells.
A run that supplied no cell list still counts every barcode — membership is then unknown rather than false
— and `cellListSource` in the run record says which case a plot was drawn under.

The score spread and the reference readings draw on a linear axis. Both are read against a number a
scientist types in the same units, and both had been drawn on a log axis, which put that number where the
reader could not find it. A declared gate of exactly zero now draws its marker; it drew none before, which
read as no gate declared at all.

The fitted-background grid and the sample's own count panels title on the reagent's name and sort by it,
rather than on the barcode sequence behind it. Thumbnails in both grids draw bars only, so a small panel
spends its width on the plot instead of on an axis gutter wider than the plot itself; an enlarged panel
still carries its axes. The grid no longer offers a horizontal scrollbar with four pixels of travel.

Per-sample QC draws each sample's rolled-up status as the same tag the sample list draws, and no longer as
the bare word.

`Cells detected` is now `Cell barcodes detected` on both surfaces that carry it. It counts distinct barcodes
in the tag-stat table, before any cell-calling step, which is one to two orders of magnitude above the cell
count — the measurement set already named it this way, and only the column label overstated it.

The sample view's antigen counts per barcode are deferred, and the section is commented out with what to
uncomment.
