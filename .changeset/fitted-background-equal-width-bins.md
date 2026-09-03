---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.model': patch
---

The fitted background draws the distribution the run holds

The fitted-background grid is the only way to see whether a tag's counts separated into two
populations, so a hump it invents, or one it hides, is the error this surface cannot carry. Three
things were wrong with it.

**The zeros were missing.** The plot was binned from the sparse counts frame, which has no rows for
cells that read nothing. Those zeros are most of the background, so the plot showed one decaying hump
whatever the fit had found — the left half of the distribution was simply absent. It is now binned over
the cells the fit actually ran on, one entry per cell in the sample, and a cell that read nothing counts
as a zero.

**Every bar is now the same width.** Bins sit at `expm1(k * 0.2)`, uniform in `log1p`, which is what
the plot's axis already is. The source paper histograms `log1p` counts at a fixed width for the same
reason.

Before, bins were whole numbers stepping geometrically, so their widths ran from 0.301 of a decade at
`[0, 1)` down to 0.079 at `[4, 5)`. A raw count then made a wide bar stand above a narrow one holding
the same density, so each bar had to be divided by its own width — and dividing by the wrong width hid
a real signal component completely: on a mixture whose upper mode was cleanly separated at a mean of
60, that hump drew at 0.9% of the background peak. Equal widths remove the division and the error with
it. Bar height is a plain cell count again, so the hover readout reports a cell count.

The cost of equal widths is that the edges are no longer whole numbers, and counts are — consecutive
integers sit further apart than one bin until about count 13, so the low end is a comb of separated
bars. The paper's own figures show the same gaps. `LOG1P_BIN_WIDTH` is coarser than the paper's 0.075
for that reason: at 0.075 a real tag came back with 75 of 97 bins empty.

**The bound line is drawn.** Under a fitted baseline the threshold is a probability, so a plot in counts
had nothing to mark it against. Each fit now resolves the count at which the run's bound probability
starts calling a cell bound, and the panel draws it. A fit that reaches no such count draws no line and
says so, rather than marking one at the bottom.

Each (sample, tag) is binned against its own range, so the emitted weight lists have different lengths
and are shorter than the shared edge set. The plot pads them, which draws the same picture — past a
pair's own maximum every bar is empty either way. Binning inside the fit rather than returning per-cell
arrays takes the fitting step from 2.1 million numbers held to 7,764 on a 27-sample run.
