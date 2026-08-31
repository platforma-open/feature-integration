---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

The fitted background draws the distribution the run holds

Two defects, one picture. The fitted-background grid is the only way to see whether a tag's counts
separated into two populations, so a hump it invents is the one error this surface cannot carry.

**Bins now sit on whole numbers.** `np.geomspace` puts edges between whole numbers, and a UMI count is
a whole number, so a bin could fall strictly between two counts and stand empty at every weight a run
could produce. On a 15-tag run topping out at 5,155 counts the bin at [2.039, 2.911) held nothing on
all 15 panels and read as a missing bar. Edges are now whole and strictly increasing, so every bin
holds at least one count. The low end steps by 1. Above that the step is geometric as before. The last
edge is one past the top count, which makes every bin half-open. A run now takes at most 24 bins
instead of always 24.

**Bars are now density, cells per count.** Bin width in counts rises across the edge set — 1, 1, 2, 3,
4, 6, 9, 14, 21 on one real run — so a bin spanning 4 counts stood about four times a neighbour
spanning 1 at equal density. That step read as a second hump on tags whose counts only decay. Dividing
each weight by the counts its bin spans removes it. The y axis is labelled "Cells per count", and each
panel's caption carries the cell count the plot is drawn from.

The hover readout on that grid now reports the density rather than the cell count, because
`PlChartHistogram` prints the number it is handed under a fixed `count:` label. The caption carries the
magnitude instead.
