---
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

The quality layer reads three statuses, and the reagent table reads the frame each figure is about.

A measurement is now **OK**, **warn** or **alert** and nothing else. One with no line behind it carries no status, and which of the two cases it is reads from the value: a number means nothing judges it, a reason in place of a number means nothing computed it. The row is there either way, and the coverage triple beside it still separates the two.

**Warn is new.** Every inherited line arrives with a warn threshold and an error threshold, and the block held one number per measurement, so each pair was collapsed and a calibrated distinction discarded. Reads per cell at 4,000 read *alerting* and now warns, since one published number gives one boundary. The panel-assigned fraction at 0.49 read *alerting* and now warns, since only a wholly failed sample alerts.

The two thresholds of a line are read independently, because the field warns on a direction and puts error at total failure for three of its four lines.

**The reagent table's figures now come from the right side of the minimum.** Cells with any count and the median count per cell are read from the raw counts, cells called bound from the post-minimum states. The median was taken over bound cells alone, where it could only ever print a number above the cutoff's floor, so a half-degraded reagent showed a healthy figure. Every declared tag keeps a row, so a dead reagent reads as a zero rather than as an absence, and reference tags keep a row whose bound count is empty rather than zero.

**Two measurements changed hands.** The panel-assigned fraction was filed against the *usable antigen reads* row and satisfies the *undeclared barcodes* row: its complement is that quantity exactly, and the line transfers with it. Its status no longer reaches its sample's rollup, because a reagent belongs to the run rather than to any one sample.

**One measurement is new.** The fraction of reads whose cell barcode the chemistry could have produced, warning below 0.75 and alerting below 0.50. The refine-tags report already carried the step it reads. It is the one inherited line with a gradient at both ends rather than a catastrophe, and the reason a third status level exists.

The per-sample quality frame gains a **Valid cell-barcode fraction** column.

**The fitted background now leaves the function that fits it.** Under a population baseline the block fits a two-component mixture per tag and per sample, and kept only the failures. The background component's mean, its share of cells, and the signal mean beside it now reach the measurement set as one tag-level row: the median over the panel's samples that fitted, with the spread and the unfitted count in its detail. It is what a scientist reads to see whether a tag's counts separated at all, and it depends on no cutoff — which matters, because it is read in order to settle one.

Under a declared baseline nothing is fitted, and every row says that rather than going missing.

**The run's scores now have a spread.** The score is computed for every cell and identity, used for one comparison against the cutoff, and was then dropped. It reaches the measurement set as deciles: one figure for the whole run, because the cutoff is one number for the run. A scientist may move that cutoff to where their own run's scores separate, and that licence is unusable unless the scores are in front of them — until now it was set blind.

The measurement carries no line, since a line here would be the block placing the cutoff instead. A population baseline yields a probability rather than a score, and under it the row says so rather than printing a number from the wrong rule. The measurement set gains a **run** grain, which is a grain of one.

**The sticky measurement takes two forms, and the gate decides which.** The block carried two thresholds on a cell's reference reading: the admissibility gate, which sets a cell aside, and a separate observation line defaulted to 100, which counted cells as sticky. Only one exists in the spec. `060-parameter-set` lists seven parameters and a sticky line is not among them, and `290-reference-two-roles` is explicit that *how many are high* needs a high, and only a declared gate supplies one.

So **Sticky cell threshold** is gone from the settings, along with its parameter and its `--high-reference-line` flag. With a gate declared, the cells counted high are the cells it set aside — one number, both jobs. With no gate declared, which is the default, there is no *high* to count and the measurement is the **spread of the reference readings** instead. That spread is what a scientist reads in order to place a gate, and until now a first run offered a count against a line nobody had chosen.

A stored project carrying the old value keeps running; the field is simply no longer read.
