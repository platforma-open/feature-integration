---
'@platforma-open/milaboratories.feature-integration': major
'@platforma-open/milaboratories.feature-integration.model': major
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

Antigen barcode binding profiling: verdicts, the baseline ladder, the quality layer and the readout.

This release reconciles the block to the antigen-barcode binding-profiling spec. The entries below were written one change at a time and are grouped here; each keeps its own wording.

## Verdicts and the baseline ladder

Antigen binding is reported as a four-state verdict per clonotype set and antigen identity: **bound**, **not bound**, **never asked**, or **unreliable**. The last two are not kinds of "not bound" — *never asked* means the experiment did not put that antigen to those cells, and *unreliable* means it did and the data cannot settle the result. Both are emitted as rows rather than left absent, so a reader can tell an unanswered question from a negative answer.

An identity is a group of tags, not a feature name: the same barcode carries different names in different samples' panels, so name-keying splits one reagent and can merge two. Tags combine into an identity by the highest of their counts, and the grouping is a rule over the panel's declared properties rather than a frozen map, so the same run can be read at more than one grouping.

**What each verdict rests on travels with it.** Every row carries how many of the set's cells could have answered at that identity, how many did, and the agreement among them — so a verdict resting on three cells is distinguishable from one resting on forty. Where an antigen read *not bound* while something it was declared to compete with read *bound* for the same clonotype, the row says so, and a downstream filter can test it.

**Nothing is orderable.** No score, rank or per-antigen magnitude leaves the block, and a build-time assertion refuses any score annotation on an emitted column. A verdict is a statement about what the experiment could establish; ranking clonotypes by it is a downstream block's job, from these outputs plus other assays.

**Quality measurements ship with the reading.** Fifteen measurements across sample, tag, identity, panel and capture levels — the spec's fourteen, with its single saturation-and-depth row shipping as two, because reads-per-cell is computable from what the parse step already reports while saturation is not, and one measurement cannot be half *not evaluated*. Each states what it counts and — only where a line can be defended — what a bad value implies. A measurement with no defensible line reads *unjudged* rather than being given an invented threshold, and one the run could not supply inputs for reads *not evaluated* with its reason. Every level reports coverage beside its status, so a run states both what is wrong and how much of it was actually checked. Sample and panel roll up as separate axes: a bad sample is prepared again, a bad reagent is replaced.

**The panel's declarations travel with the verdicts.** Whatever the panel file says consistently about an identity's tags is exported beside that identity's readings — role, species, carrier, whatever the scientist's own table carries — each as its own filterable column keyed by identity. A property an identity's member tags disagree about is omitted rather than resolved to a winner: an identity read as one thing whose members disagree about what that thing is has no declaration to travel. Without this a reader can see that an identity was bound and not what it was declared to be, and cannot ask whether a clonotype bound its target and nothing in the control group.

The panel-versus-reads check is emitted as a p-column in both directions — barcodes the reads carry that no panel declared, and tags a panel declared that the reads never showed.

**Removed:** the dominant-feature ("consensus") call and the per-cell specificity score. A single dominant antigen per cell answers a different question from the one this block now answers, and a specificity magnitude is exactly the narrowing the four-state verdict replaces. The `pl7.app/feature/consensusFeature` and specificity p-columns are no longer emitted. No block in this workspace consumes them.

**Unchanged:** the per-cell UMI count and fraction columns, the negative-control marker column, and the combine-mode settings.

**A single-cell V(D)J dataset is required for the antigen stage.** Without one the block still runs and still emits its per-cell UMI counts and fractions, per-sample QC and per-feature properties exactly as before — but it produces no verdicts, no per-antigen columns and no panel check, rather than producing empty ones. The Verdicts page says so instead of showing an empty table.

The baseline is the scientist's choice. The block no longer picks one.

`what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either. The block derived it in two layers. Neither derives now.

**This changes what an existing project computes.** A project that never touched the baseline field was silently answered under a derived rung — the declared tag where one existed, else the panel's own readings. It is now answered under the bottom rung: no baseline, and every verdict that needs one reads *unreliable*. The settings page says so in a warning while the field is unchosen. Choosing the rung that was being derived restores the previous numbers exactly.

An unselected run is not refused. Refusing to start would be the block deciding a scientist's methodology by withholding the run, which is the same act as choosing one for them. It completes, and the run record carries both what was asked for and what served.

"No baseline" is now on the list. It was withheld on the reasoning that nobody would choose a run with no answers, which was right about the consequence and wrong about the status: it is a position held in print, by scientists who argue that a tag declared to be bound by nothing is not truly negative and that a reference chosen that way lends false confidence. On that view the absence is a design choice rather than an omission.

A rung that stops being serviceable is still never swapped for another. The run reports no baseline and records the request beside it.

Stop the software picking a baseline rung, and require the choice on the command line.

`what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either.

The software's own three-rung default is removed rather than left unused. Leaving it in place was a trap: the workflow omits `--reference-source` whenever the model's value is empty, so removing the derivation in the model alone would have silently promoted this one to the live rule, deriving in the layer furthest from the reader. `--reference-source` is now required, so there is nothing left to promote.

`served_source` is unaffected and still degrades a rung that cannot serve to *none*, never to a different rung.

The model still derives, which is a known deviation and the last one left. Removing it needs a ruling the spec does not settle — whether a run with nothing selected refuses to start or completes with every verdict that needs a baseline reading unreliable — because the two need different code. The docstring there now states this, and no longer claims such verdicts read *not evaluated*, which is a quality-measurement status and never a verdict state.

Read counts against one declared baseline tag, or none. Several are refused, not combined.

The block used to take the highest reading among several declared baseline tags. `baseline-scope` states that references are never combined, and taking the highest is a combination — so a panel declaring several no longer gets a comparator nobody specified.

Reading against several is **deferred to a later version**, not dropped. That atom builds the reference as a grouping over a declared panel property: each reference serves the group its declaration scopes it to. This block has no group-by half — a tag is a comparator for the whole panel or for none of it — so it cannot say which antigens a second comparator belongs to. Refusing is also what the field does: the ordinary antibody run rejects a second control outright, and the T-cell run requires one control per allele and rejects two.

The run stops and names the tags it found, rather than falling silently to no comparator. This is a panel a scientist fixes in a minute, and a silent fall to *unreliable* everywhere would not tell them how.

Scoped to the rung that reads a declared tag **as** the comparator. Under the panel rung and the tag-distribution rung, several declared tags are unambiguous — the first treats them as ordinary readings in its median, the second never sees the role at all — so those runs are unaffected.

One role value can mark several tags, so this is a limit on the tags found, not on how many values are picked. The settings tooltip says so.

One value marks the baseline tag, not several.

"Values that mark the baseline tag" was a multi-select. It is now a single-select, "Value that marks the baseline tag".

`040-glossary` splits the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference is a job given to one of them, declared with the run. So the value that nominates the baseline is singular.

Several values only ever described a panel whose role column spells one role more than one way. A panel that does that is asking to be corrected, not accommodated.

`referenceValues` stays a list in the block's data, so the `--reference-values` flag and every stored project keep their shape. A project that had picked several values keeps them until the field is next touched, and the run still refuses if they mark more than one tag. That refusal is unchanged and is now the only one that can fire: one value can still mark several tags, which is a panel fact the control cannot see.

Whether the minimum count reaches the baseline tag is now a setting, off by default.

The block exempted the declared baseline tag from the minimum count and hard-coded that. It is now "Apply the minimum count to the baseline tag" under Advanced reading settings, unticked.

**It changes no verdict, and that is checked rather than asserted.** Each baseline source reads its own counts before the minimum, so the level a count is judged against is the same either way. An end-to-end test runs the same bed with the setting off and on and requires the verdicts, the per-cell counts and the per-cell scalars to be byte-identical, while requiring the removed-readings count to differ — so the test cannot pass on a bed where the setting reaches nothing.

What it does change is the run's own accounting: how many readings the run reports as removed, how many cells it reports as emptied, and through those, which of a clonotype's cells count as empty.

The emptied-cell population follows the same switch. With the baseline exempt, a cell holding only a below-minimum baseline reading never had evidence of binding for the minimum to remove. With the baseline subject to it, that cell has been emptied. Scoping the population one way while flooring the other would report a cell as keeping evidence it no longer has.

One stale piece of reasoning is retired with this. The exemption's second stated ground was that flooring the comparator "lowers every denominator and shifts the whole run toward bound". Since each rung reads its own source raw, flooring here reaches no denominator at all, and that clause no longer holds.

Add the tag-distribution baseline, and build every baseline from raw counts.

A run with no declared control tag can now read each count against that tag's own distribution across the sample's cells, split into two components. It serves where the sample holds at least 300 cells and the tag's counts actually separate; a tag that does not separate reports no comparator rather than an invented one, and only the identities built from that tag are affected. Both conditions are settings on the CLI (`--distribution-min-cells`, `--distribution-separation`) and both are recorded in the run record, alongside a per-tag list of what could not be fitted.

This is the first comparator that varies by identity rather than by cell, so the shared admissibility bundle now carries the identity a comparison is being made about.

Separately, and independent of the new rung: every comparator is now computed from the raw counts rather than the floored ones. The minimum count applies to the reading being judged, never to what it is judged against — and because reference tags are exempt from the minimum and antigen tags are not, the panel comparator was previously a median taken over a mixture of raw and floored values. Panel comparators rise, so fewer cells read *bound*.

Offer the tag-distribution baseline as a source, with its two conditions as settings.

"Each tag's own distribution" joins the baseline dropdown. It reads each count against the lower of two components fitted to that tag's counts across the sample's cells, which is what serves a panel that declares no baseline tag and is too small to stand in for one — the shape every antibody kit has, since they cap at fifteen tags.

It is the only source offered unconditionally. Whether it can serve turns on the sample's cell count and on whether each tag's counts separate, and the second is answered per tag rather than per run, so the conditions are stated in the option's description and the run reports what it managed: which tags fitted, which did not, and why. A tag that did not separate takes only the antigens it carries with it; every other antigen in the same cells is answered normally.

Two new settings under "Baseline thresholds": the cells a sample needs before the rung may serve (300, from the study the method comes from) and how deep the dip between the two components must be (0.5, this block's choice — nothing published sets it). Both are sent on every run, so the record states the numbers a reading would have used whichever source served.

Raise the panel rung's member minimum from 8 to 25.

The figure comes from one preprint, whose own panels held 50 and 100 members, and nothing validates it lower. It gates the method rather than tuning it: below it, comparing a count against a handful of other antigens is not a background estimate, so the baseline it permits is not conservative but wrong.

At 8 the rung was within reach of an antibody panel. It is not meant to be — those kits cap at fifteen tags — so a panel that declares no baseline tag no longer stands in as its own background. Such a run reads each tag against its own distribution instead, which is what that source was added for. A run that wants the old behaviour can still lower the setting, and says so wherever its verdicts appear.

Carry a third number with every clonotype: how many of its cells were left with no count on any tag.

`support-travels-with-the-reading` asks for it beside the two counts a verdict already ships — how many of the clonotype's cells could have answered at an identity, and how many did. This one is a property of the cell rather than of a position, so it is counted once for the clonotype: a cell with nothing left is empty at every identity, and repeating the subtraction per position would report a per-identity failure that did not happen.

**It changes no verdict.** Those cells vote *not bound* like any other. What it carries is whether a negative rests on cells that read something or on cells that read nothing.

- **The baseline is part of the test, and that is the whole discriminator.** A cell whose antigen tags all fell below the minimum count while its baseline reading survived took up reagent and none of it was antigen — a real negative and a real vote. Only a cell with nothing anywhere read nothing. The existing per-sample `cellsEmptied` counter cannot see this: while the baseline is exempt from the minimum, that counter is scoped to the readings the minimum was allowed to remove, so the baseline is invisible to it. This is a new tally rather than a rename.
- **`Cells that read nothing` ships off by default**, in the table's column chooser, ordered next to `Cells` because it qualifies it — forty cells of which thirty-eight read nothing is a different clonotype from forty that all read something.
- **Turning `Apply the minimum count to the baseline` on moves this number**, and moves nothing else. The verdicts, the per-cell counts and the per-cell scalars are byte-identical across that switch, which is now pinned by a test.
- An emptied cell stays in the clonotype's cell count and stays in the vote. Dropping such cells would shrink the denominator and make verdicts more positive, and filtering them from the cell list is the same effect by another route.

Still to come: the per-sample alert for a run carrying many such cells, which fires whether or not a reader turned the column on. Where it lives and what counts as "many" are open.

## Declarations and identity grouping

Group tags into identities per tag and sample. The panel file declares what a tag carries in each sample, so a barcode reused across panels now resolves to the antigen its own sample declared instead of standing alone under its raw sequence. On a per-sample panel the punchcard renders identities across rather than tags across, and each cell's reading combines only the tags its own sample offered.

A panel member that contradicts itself no longer lets one member's declaration stand for the whole identity.

A property holds of a grouped identity only where its member tags agree. A tag whose own rows contradict each other has no agreed value, so it reached that test as an empty string and was filtered out exactly like a tag whose cell was blank — and a blank member is deliberately not allowed to veto its neighbours.

On a panel with barcode reuse that inverts the outcome. Measured on a real sixteen-row panel grouped on its role column: an identity whose five member tags declared six different antigen names between them came back carrying **one member's name**, because four of the five had contradicted themselves into silence and the survivor then agreed with nobody but itself. Nothing in the export marked it partial.

A member that contradicted itself is a disagreement, not a silence, and now blocks the property. That is the direction the tag-grain rule already takes — it keeps disagreements rather than dropping them, because with barcode reuse an inconsistent declaration is the expected case and dropping it silently breaks the panel file's no-silent-drop rule. This stops that guarantee being undone one grain higher.

Strictly more omission, never more assertion. Panels with heavy barcode reuse will carry fewer declarations on grouped identities than before, which is the correction rather than a side effect. A member that genuinely declared nothing still does not block its neighbours, and a column the identity was grouped on is still settled by construction.

No new computation: the call site already built the disagreement map for its warnings and simply did not pass it down.

A panel may mark several control features, not one.

"Control feature marker (output only)" was a single-select. It is now "Control feature markers (output only)", a multi-select, and every chosen feature is marked in the `pl7.app/feature/negativeControl` column that downstream reads.

`040-glossary` separates the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference that supplies the baseline is a job given to exactly one of them. This setting marks controls and nominates nothing, so it takes as many as the panel has. The nomination is `referenceValues`, which stays singular.

`--control-feature` is now repeatable. It is repeated rather than comma-joined because a feature name may contain a comma, and joining would split one name into two features that do not exist. Duplicates are dropped, so a feature is marked once and the axis it keys on cannot carry it twice.

`controlFeature` is the shape a project saved before the setting took a list. It is still read: every reader goes through `controlFeatures()`, which reads either, so no stored project needs a migration. Nothing writes the singular form now.

Emit the negative control on the feature axis. The chosen control feature is now surfaced as a dedicated hidden per-feature marker (`pl7.app/feature/negativeControl`), so VDJ Multiomic Integration can remove the control from its antigen metrics (restriction index, antigen breadth, per-antigen fraction columns, and the dominant call). No user-facing change — the marker is hidden and is not offered as a per-feature property.

Rename the co-binding cell label from "cross-reactive" to "Target cross-reactive", making explicit that it means a cell binding two or more on-target antigens — distinct from unwanted, nonspecific polyreactivity. Also drop the remaining internal "Decoy" examples (retired in favour of "off-target").

## The quality layer

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

A **Run quality** page shows the run's own quality report and the panel-versus-reads check. Both were computed by the verdict stage on every run and emitted, and neither had a page — so a measurement that came out alerting, and a barcode the panel declared that no read carried, were reported to nobody.

The measurements table carries each measurement's status beside the coverage triple behind it — how many were judged, how many unjudged, how many not evaluated — and, where nothing computed a measurement, the reason it was deferred. All of those are shown without opening the column chooser: a status reading "nothing here is wrong" and a level where almost nothing was checkable must not look the same. Status is the plain word the run emitted, filterable through the discrete filter its column already declares, because `unjudged` and `not evaluated` are states rather than degrees of badness and no four-rank tag vocabulary can say that.

Under a **Panel versus reads** separator, the mismatch check shows both directions in one table — barcodes the panel declared that no read carried, and barcodes the reads carried that the panel never declared — told apart by a filterable direction column.

An absent report and an empty one say different things and are answered differently. No report at all means the verdict stage never ran, which happens when no single-cell V(D)J dataset was picked; the page says that instead of drawing a grid. An empty report means the stage ran and found nothing, which for the mismatch check is the outcome you want; the grid renders and says so in place of its rows.

This page is the run's quality. The existing **Per-sample QC** page is unchanged and still shows the per-sample read statistics.

Run quality rows carry the measurement's readable name, and the two columns explaining what a measurement counts and what a bad value means are shown by default rather than hidden behind the column chooser.

## The readout and its pages

Replace the Binding verdicts and Quality checks tables with a punchcard

The two result tables are removed as views. A punchcard takes their place: rows are clonotype sets,
columns are the antigen identities picked from a dropdown, and a cell is one punch whose colour is the
verdict and whose size is the support behind it. Every identity is already in the result, so picking one
costs a redraw rather than a run.

Both artifacts are still emitted. The verdicts and the run's own measurements are what the block owes,
and dropping a view does not release it from producing them — the verdicts still export to downstream
blocks, and the quality frames are still built by the workflow. What no longer exists is the pair of
grids that presented them.

The reading itself is unchanged: no threshold, default or verdict moves.

Block data migrates to v4, dropping the three grid states the removed views owned and adding the
punchcard's own state and its identity selection.

Punchcard column headers show the identity's full name. They were cut to 20 characters to stop a long label auto-sizing its column off screen, but the cut fell on the barcode suffix that distinguishes two tags sharing a joined label, so distinct columns read as duplicates.

The punchcard renders every identity column and is narrowed with the grid's own columns and filters panels. The "Antigens shown" multi-select is removed, along with the `punchcardIdentities` view state behind it.

The per-sample slide-over is tabbed — Visual Report, Quality Checks and Log — following mixcr-clonotyping. Read recovery and the per-check quality statuses are shown for the selected sample instead of logs alone.

## Settings and config-time guards

Read the tag-feature panel in the UI, so the column dropdowns fill on the pick.

Choosing a panel CSV used to start a round trip — upload the blob, run a staging exec, read its JSON — before the barcode-sequence, feature-name and negative-control dropdowns had anything to offer. The exec itself was 59 lines of stdlib Python, but its artifact shared the package's `requirements.txt`, so the backend built a venv holding polars, numpy and scipy to run it, and paid that again after every version bump.

The UI now reads the file directly. A local pick is read from disk on the gesture and the dropdowns fill immediately. A pick from remote storage, or a project opened where the original file never existed, is read from the CSV blob the prerun already exports — the same parser over the same bytes, so there is no second implementation to keep in agreement.

- **`emit-csv-meta` is gone**: the entrypoint, `emit_csv_meta.py`, and its tests. The prerun now has no exec at all, so nothing builds a venv during staging. It still imports and exports the CSV, which is what drives the upload.
- **`csvMetaSnapshot` in block data** carries the parsed panel, tagged with the handle it was read from. `readCsvMeta` returns it only while that tag matches the CSV currently picked, so a snapshot cannot be read against a different file.
- **A failure is now shown, not logged.** With no workflow-side parser left to fall back on, a discarded parse error would leave empty dropdowns and no explanation, so the reason appears next to the file input.
- Parsing uses `csv-parse`, as in the xsv-import block: RFC 4180 quoting, and both LF and CRLF endings. Real panel files are CRLF.

The `prerunArgs` projection is unchanged and must stay that way — it is what keeps the UI's write to `csvMetaSnapshot` from re-rendering staging. The comment above it now says so.

No change to `args()`, to the production workflow, or to any exported column. Existing projects re-read their panel from the exported blob on open.

Warn at config time when the chosen barcode-sequence column holds no nucleotide sequences. A panel CSV often carries an identifier column beside the sequence column, and picking the identifier previously failed several stages into the run, inside barcode correction, with a Java stack trace. The block now names the offending values and the column that would work. The duplicate-barcode warning stays silent while this one shows, so the two never disagree about the fix.

Clarify Settings tooltips. Each optional field now leads with when to use it (or to leave it blank), and the control, sample-column, off-target, combine-mode, dominance, and min-UMI descriptions are tightened for readability.

The Tag-feature CSV tooltip drops its closing sentence, which pointed at the two labelled fields directly below it, so the tooltip is short enough to stay on screen. The Barcode sequence column tooltip now says the `FEATURE` tag captures the barcode on Read 2 and names Read 2 as the second read of each pair, because the assembled pattern also holds a sibling group called `R2` and "the `FEATURE` capture on Read 2" could be read as containment.

Clarify off-target tooltips. The off-target property/values tooltips drop the retired "Decoy" term (in favour of "off-target antigen"), and the off-target-values tooltip now correctly states that value matching trims surrounding spaces but is case-sensitive — it previously claimed matching ignores case, which contradicted the shipped behaviour.

The contending-antigen editor is not offered for now. Only the editor is deferred: a project that already carries contending groups keeps them, the args projection still passes them, and the emitted verdicts and their competitor notes are unchanged.

It asked the scientist to retype by hand a grouping the panel file is meant to declare, and at the default one-identity-per-tag grouping the identities are the barcodes themselves — so the picker could only offer raw 15-mers. What it should become is contention derived from a declared panel column, alongside the existing grouping choice.

Hide the Combine-mode column selector. It is not exposed to users for now; the control, its validation, and the workflow's combine-mode logic are kept for later re-enable. With the selector hidden, `combineColumn` stays unset and every antigen uses the default "sum" mode.

## Exported columns

The verdict stage's cell artifact is split, so that no frame leaving the block carries a column keyed on `(sample, cell, tag)`.

One frame previously bundled two differently-keyed columns and exported the pair: the per-cell, per-tag counts, and the per-cell scalars — the reference reading and whether a declared gate set the cell aside. Only the second belongs outside. The per-cell per-tag states stay inside the block, because labelling and lead selection read verdicts and never cells, so exporting them shipped the run's largest artifact across the boundary to a consumer that does not exist. The per-cell reference readings are the opposite case: the block is required to report the cells carrying a high reference reading whether or not a gate is declared, and which of them a declared gate set aside.

**The per-cell per-tag counts are no longer imported at all**, not merely un-exported. Nothing read them on either side of the boundary, so importing them built the run's biggest p-frame for no reader on every verdict run — their grain is cell × tag, which on a realistic panel is 11-20× the rows of the sparse reads they derive from. The Python is untouched: it still writes the counts table and the exec template still collects it, so the states are computed and exist within the run. What stops is turning them into p-columns.

**Renamed output:** `antigenCellTable` → `antigenCellReference`. What remains in the frame is per-cell reference readings and gate outcomes, so the old name described a shape the frame no longer has.

Renaming an output is breaking for any downstream consumer, which is why this is worth checking rather than taking on faith — but there are none, so this ships as a patch. The claim is verifiable in one command: `git grep -n -E "antigenCellTable|cellCounts|cellScalars"` over `model/src`, `ui/src` and `workflow/src` returns hits only inside the workflow that builds them. Neither the model nor the UI reads the frame; the model's `perCellTable` output resolves a different, identically-named workflow output and is unaffected. The punchcard, verdicts, QC and panel-mismatch frames are unchanged.

Align the exported columns' reader-facing labels to the spec glossary.

Labels and descriptions only. No p-column name, domain or axis spec changes, so nothing downstream re-binds and no column identity moves.

- **"Reference count" → "Baseline reading"** on the per-cell comparator. The glossary defines *baseline* as the reading a count is measured against, and *reference* as the tag rather than the reading. Every other reader-facing surface moved to "baseline" already; this one lives in the workflow and was missed.
- **"Antigens bound / offered / settled / unsettled" → "Identities …"** on the clonotype counts. They count identities, never tags — the module's own comment said so two lines above the labels — and the glossary separates an identity, "a group of tags read as one thing", from the antigen a tag carries.
- **The cell-grain sibling becomes "Identities this cell bound"**, because the clonotype-grain count now carries the plain name. The two are different numbers over different populations, and a reader meeting both under one name would take the smaller for a subset of the larger, which it is not.
- **"Panel" → "Panel used"** on the per-sample column. The panel axis's own label column is the other "Panel", and it names the panel itself.
- **"Measured thing" → "Measurement subject"**, and two Title Case stragglers to sentence case.

Checked while doing this and deliberately left alone: "Feature" and "Tag" are not two names for one layer. The feature axis keys by antigen name and the tag axis carries barcode sequences, they are separate axes on purpose, and the module already guards against a new column keying on the legacy feature axis while holding barcodes.

No two exported columns now share a label.

## Build and packaging

Lower the per-sample mitool memory floor from 64 GiB to 16 GiB. The 64 GiB floor was applied on every run regardless of input size, and mitool's memory-from-limits launcher turns the grant into a JVM with `-Xms` = 50% of it — a ~32 GiB initial heap even for tiny datasets, which swaps on typical desktop RAM and stalls the "parsing reads" step. The `size("reads")*4` term still scales large inputs up (cap 256 GiB), so only small runs are affected. Also lower the per-sample mitool CPU default from 16 to 8 (matching peptide-extraction; 16 exceeded the core count on typical desktop machines) and fix the "mitool CPUs per sample" tooltip, which stated the default was 4.

Fix the block changelog pointer. `block.meta.changelog` pointed at `file:../CHANGELOG.md` (the repo-root "Initial release" stub), so every published block-pack shipped the 1.0.0 stub and the desktop update view showed no release notes. Point it at `file:./CHANGELOG.md` — the changesets-generated block changelog.
