---
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

The fitted-background grid reads one sample at a time

The fit runs per (sample, tag), so the grid drew one panel per pair — 27 samples and 9 barcodes is 243
panels on one page, and every title had to repeat the sample to tell them apart. It now shows one
sample, chosen from a selector above it, so a panel is titled by its reagent alone and the sample is
named once. Barcodes read in the order the panel file declares them, so a barcode holds the same slot
whichever sample is shown.

What that gives up is reading down one reagent across samples. The grid still supports that shape;
nothing asks for it today.

Each panel's caption now carries the bound count alone, and the fit's own numbers moved to the enlarged
panel, where a value is read rather than scanned. The cell count that used to lead the caption was the
sample's analysed population — the same number on every panel of the sample — and now sits once above
the grid.

**The Panel column is out of the tables.** It is a hash of the sorted barcode list, because no panel
file names its panel, and a run declaring one panel for every sample repeated that hash identically on
every row. It stays available in the column picker, and a multi-panel run should switch it on:
`Seen in 2/3` cannot be read without knowing which three samples.

**Fixes**

- A fitted background mean of 0.000488 printed as `0`, a value the fit cannot produce — three
  significant figures were computed and then discarded by a formatter keeping three decimal places.
- The Run quality page failed to render at all: a watch read a value declared further down the file.
- A run computed before the bound count existed reported "no count reaches the bound probability",
  stating a finding no run had produced. Absent and null now read differently.
- Resizing the window redrew every panel on every frame, and each redraw leaks a tooltip node in the
  uikit. A few pixels of tolerance takes a drag from hundreds of redraws to a handful. The leak itself
  is the uikit's.
- The quality-report JSON was pretty-printed, which roughly doubled it for a file only the UI reads.
