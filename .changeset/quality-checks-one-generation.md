---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Quality Checks: one generation, judged where a line can be defended

Nineteen declared measurements become twelve, and five applied thresholds become nine. Every row was
reworded; four were renamed; two figures were computing the wrong thing.

**The long measurement frame is gone.** The QC surface carried two generations stacked on top of each
other. The first was a single frame written by the verdict stage — keyed (level, entity, panelId,
measurement), four p-column axes, thirteen columns, a coverage triple on every row and a rollup row
per level. (It was written as `result_qc.csv`, which is also the name of the per-sample read-QC file
the earlier stage writes; that one stays.) The second generation is the set of purpose-built surfaces
that replaced it: the Reagents grid, the Undeclared-barcodes grid, the Sample QC table, each sample's
own Quality Checks list, and the binned plots.

The second shipped. The first was switched off at the UI and left running everywhere else, so the
block computed, imported and specced a frame no reader could reach. With it go seven tag- and
run-level measurement declarations, the frame's builder, its axes and columns, the model output and
grid state behind it, `QcEntityCell.vue`, and a sheet fetch that ran `listColumns` and
`getUniqueValues` on every handle change to build options for a hidden grid.

Nothing a reader could see is lost. Cells-with-a-count, the median count, cells called bound and both
disagreement rates are the Reagents table's; a declared tag the reads never show keeps a row there and
reads as a zero; the undeclared sequences are the Undeclared-barcodes table's; the fitted background
is the Run-quality page's per-(sample, tag) grid and `result_qc_backgrounds.csv`; the run's score
spread is the Scores plot. Removing the frame also removed work done twice: the per-panel loop
computed `per_antigen_measures`, `sibling_disagreement` and the per-tag self-disagreement rate once
for the frame and again for the Reagents table, over the same samples — and self-disagreement walks
every cell of every set.

**Two numbers were wrong, and both are corrected.**

*Reads rescued by barcode correction* is a subtraction, and its subtrahend was
`1 - panelAssignedFraction` — a share of that refine step's own input rather than of matched reads,
because each level of refine-tags is fed from the previous level's survivors. The result came out
systematically low. Both terms are now over matched reads, which needed a new `featureDroppedShare`
column carried end to end.

*Mean reads per V(D)J-matched cell* divided **every** matched read in the library by the count of
V(D)J-matched cells. That is Cell Ranger's `Mean Reads per Cell` with its denominator swapped for an
intersection with an external dataset, so the two halves moved independently and the figure tracked
the V(D)J match rate rather than this library's depth: holding one antigen library fixed at 2,500
reads in each of 8,000 barcodes, it read 40,000 against 500 matched cells and 2,500 against 8,000 — a
sixteen-fold swing, warning only where V(D)J recovery was *good*. The numerator is now the reads
inside those same cells, the identical set the usable-read share is taken over, computed once in a
shared helper. Depth no longer needs the read-QC row at all; it does now need the counts table's
`totalWeight` column, which the gather selects explicitly.

**Four new thresholds, and one moved.** Only the first is traceable to a vendor number:

- *Reads matching a panel barcode* — warn below 0.50, alert at 0. INHERITED: the complement of Cell
  Ranger's `ANTIGEN_unrecognized_feature_bc_frac`, published at 0.50 / 1.0 for the Antigen Capture
  library. The block carried this 0.50 once on the wrong quantity (one undeclared sequence), where it
  was correctly rejected and never moved back to the aggregate it belongs to.
- *Reads matching the read pattern* — warn below 0.90, alert below 0.50. A newly declared row; the
  figure was already a Sample QC column. The pattern is all fixed-length N-runs, so a read either
  covers the geometry or it does not and a healthy run sits near 1.00.
- *Reads rescued by barcode correction* — warn above 0.05, alert above 0.10. Both ends face the same
  way: there is no catastrophe value to alert at, since a rescued read is one the pattern already
  matched and the panel already assigned.
- *Median antigen count per V(D)J-matched cell barcode* — warn below 4, alert **at** 1. The warn is
  the default minimum count, below which most of the typical cell's readings are zeroed before any
  call. The alert fires at 1 rather than below it because 1 is the floor of the quantity: the
  population is barcodes holding at least one counted reading, so no lower median can be produced.
- *Reads whose cell barcode passed quality correction* moved from 0.75 / 0.50 to 0.95 / 0.75, and
  from `inherited` to `operator-set`. The published pair was for validity against a chemistry
  whitelist; no whitelist is selectable here, so refine-tags runs de-novo correction and drops reads
  on base quality. Renamed for what it measures. **This is the one change that can alter an existing
  run's status.**

Three of the four are this block's own estimate, so the block gained a fourth provenance route,
`operator-set` — the honest label for a line we chose, with nothing published behind it. Every
threshold is a parameter with a shipped default, movable under **More options → Quality lines**,
which is also where each number's origin is now stated.

**Rows that changed shape.**

*Sticky cells, or the spread of control readings* was one row whose meaning depended on a setting;
two of its three branches only restated that setting. It is now two rows — *Cells set aside as
sticky* and *Median control-tag reading per V(D)J-matched cell* — both narrowed to the V(D)J-matched
cells, and both omitted entirely when no control tag exists. The old median ran over every analysed
barcode, where ambient droplets outnumber cells by one to two orders of magnitude, so it read near
zero on a sample with genuinely sticky cells.

*Reads whose cell barcode passed quality correction* now states its two counts —
"18,000 reads entered correction, 17,500 passed" — because 0.98 over 400 reads and 0.98 over 40M are
the same number and not the same finding. The share is derived from those same two counts, so a row
cannot disagree with its own detail line.

*Reads parsed* and *Median antigen count per observed cell barcode* leave the list. The first is a
count a declared share already carries as its denominator; it stays a Sample QC column, where
comparing depth between samples is the table's job. The second could not be judged — its floor is 1
for the same reason as the row above, so no bad value existed for a line to name — and its only use
was a comparison against the V(D)J-matched median that a reader had to make in their head. The gap
between the two, which was how much signal sat in droplets holding no recovered receptor, is no
longer reported.

*Median antigen count per cell* used to carry eleven decile points in two p-columns that nothing
plotted, printed into a detail string as a wall of digits. The binned distributions on the
Run-quality page are what a reader judges a shape from.

**Reading order, and what a row says.** The list is declared in pipeline order and now displayed in
it: what happened to the reads, then every observed barcode, then the V(D)J-matched cells, then what
the reading rules removed. The tab used to float the rows carrying a threshold to the top, so a
reader saw neither the declaration order nor the pipeline's. The rollup line above the list is gone —
the Main grid's Quality column and each row's own status tag had already said it. Each row now shows
one plain sentence, with what a bad value means behind a click.

**Three declarations nothing ever set are gone.** `deferred_reason` was never set, so six branches
reading it were unreachable. `rolls_up` was never set to `False`, so its branch in the report, its
field on the model type and a paragraph in the Quality Checks tab could never render.
`measurement_row`/`measurement_rows` rendered a declaration "for a reader who never opens this
module"; there was no such reader. A reagent's finding still never reaches a sample's status — the
Reagents table publishes no status column at all, which makes that structural rather than a
per-measurement exemption.

`qcSummaryColumnsSpec` was a full import spec — axes, twelve columns, labels, orders, formats — read
only for its column names, and its seven overlapping columns declared the same p-column names as
`qcSampleSummaryImportSpec`. It is now the name list it was being used as.

**Cross-layer contracts are now tested.** A QC figure has to be declared in three places to arrive: a
column the import spec declares and the frame lacks kills the whole Sample QC page with
`ColumnNotFoundError` from inside ptabler, nowhere near the declaration; a column the gather does not
carry through is dropped silently and reads downstream as "nothing computed this"; and a threshold in
`DEFAULT_LINES` but not in the dict `main` builds does nothing at all, while every unit test reading
`DEFAULT_LINES` still passes. Each of those three has now happened. All three are checked, in both
directions.

A stored project migrates (v12) and loses the hidden grid's saved state, which meant something only
against the frame it was saved on.
