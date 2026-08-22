---
'@platforma-open/milaboratories.feature-integration': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

Reconcile the quality layer: one rollup level, twelve measurements, and no invented lines.

The quality output changes shape, so a reader of a previous run's report will find rows missing and one
figure computing differently. Every change removes a claim the run could not support.

**Only the sample rolls up.** The panel and capture statuses are gone. A panel status assumed its
per-tag measurements would mostly carry statuses, and they do not — one is categorical and the rest are
read as comparisons against the other tags in the same panel, which cannot be rolled into a severity
without discarding the comparison that made them findings. A capture status was then the worst of every
sample and every panel, which reduces to the worst of every sample: a statement that only repeats what
sits beside it. Nothing hides, because a reagent finding states itself on its own per-tag row, keyed by
the panel that has it. `--capture-map` is still accepted and is not read.

**Three measurements are now stated exclusions rather than rows.** Sequencing saturation goes because a
scientist cannot act on it for the run already collected, and whether the run was deep enough is
answered by reads per cell against the vendor's recommendation. The known-answer check goes because
nothing declares a known answer — no surface asks which clonotype the scientist already knows — so
building the measurement means building that declaration first. Self-disagreement at an identity goes
because it has nothing to compare against, and so cannot separate a faulty reagent from a panel full of
weak binders. The obligation to show identity figures beside an alerting tag goes with it.

**Self-disagreement is computed by pooling cells.** For one tag: every set with two or more cells that
could answer contributes all of them, and the cells sitting in the minority of their own set are the
numerator. The previous form scored sets — what share of sets disagreed at all — which needed a
small-set cutoff, since a share over three cells takes only four values and would otherwise set the
figure. **This changes the number on every run.** Two states cap the new figure at half.

**A comparison is not a line, so it cannot produce a status.** The against-the-run route is removed
along with the interquartile fence behind it. Per-tag self-disagreement now reads *unjudged* and carries
its value for a reader to compare against the tag's siblings. What that costs is real and accepted: a
barcoded reagent binding something other than the receptor no longer announces itself, and a reader who
does not scan the column sees a bad tag and a good one alike. The alternative was a multiplier nobody
published, which moves the invention up a level rather than removing it — and an outlier rule fires on
healthy runs, because marginal binding inflates disagreement across a whole panel.

**The per-sample checks in the interface drop two invented cutoffs.** Reads assigned to the panel keeps
its inherited 0.50 line and loses the second tier below it, which had no published source. Reads
matching the read pattern now carries no status at all, the matched share being none of the four numbers
the field publishes for this assay. Cells detected is unchanged and is now described as what it is, a
categorical fact rather than a quantity judged against a cutoff.

**A tag the reads never show is now *never asked*, not *not bound*.** Zero reads across a sample is
categorical and cannot arise from biology: ambient reagent reaches every cell, so a tag that bound
nothing still returns counts. What zero reads means is a reagent never added, a barcode mis-declared,
or a library that failed — and none of those put the question the panel file says was put. Those cells
now leave that identity's denominator instead of voting a confident negative on every clonotype in the
run. A per-cell absence is unchanged: a cell that read nothing for a tag its sample did measure still
votes *not bound*, which is a reading that happened and failed. `declaredNeverSeen` carries no status
now, the verdict having taken that job.

**A baseline is required, and a run without one does not happen.** The bottom rung is gone — "no
baseline" is no longer a value a scientist can select, and an unselected baseline is refused rather
than answered. The alternative was every position reading *unreliable*, which is honest and useless: a
full punchcard of non-answers costs what a real run costs and looks like a result at a glance.

Where the refusal falls follows from when the condition becomes knowable. A missing baseline tag and a
panel below the tag count are properties of the **settings**, so they are caught before anything runs
and the message names the condition that failed. Whether a sample holds enough cells whose counts
separate is a property of the **data**, so a run on that rung proceeds, finishes, reports that no
baseline could be established, and draws no punchcard. Its answer frames keep their headers and carry
no rows; the frames describing the run's structure are written in full.
