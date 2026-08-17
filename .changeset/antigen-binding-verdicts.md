---
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Antigen binding is reported as a four-state verdict per clonotype set and antigen identity: **bound**, **not bound**, **never asked**, or **unreliable**. The last two are not kinds of "not bound" — *never asked* means the experiment did not put that antigen to those cells, and *unreliable* means it did and the data cannot settle the result. Both are emitted as rows rather than left absent, so a reader can tell an unanswered question from a negative answer.

An identity is a group of tags, not a feature name: the same barcode carries different names in different samples' panels, so name-keying splits one reagent and can merge two. Tags combine into an identity by the highest of their counts, and the grouping is a rule over the panel's declared properties rather than a frozen map, so the same run can be read at more than one grouping.

**What each verdict rests on travels with it.** Every row carries how many of the set's cells could have answered at that identity, how many did, and the agreement among them — so a verdict resting on three cells is distinguishable from one resting on forty. Where an antigen read *not bound* while something it was declared to compete with read *bound* for the same clonotype, the row says so, and a downstream filter can test it.

**Nothing is orderable.** No score, rank or per-antigen magnitude leaves the block, and a build-time assertion refuses any score annotation on an emitted column. A verdict is a statement about what the experiment could establish; ranking clonotypes by it is a downstream block's job, from these outputs plus other assays.

**Quality measurements ship with the reading.** Fifteen measurements across sample, tag, identity, panel and capture levels, each stating what it counts and — only where a line can be defended — what a bad value implies. A measurement with no defensible line reads *unjudged* rather than being given an invented threshold, and one the run could not supply inputs for reads *not evaluated* with its reason. Every level reports coverage beside its status, so a run states both what is wrong and how much of it was actually checked. Sample and panel roll up as separate axes: a bad sample is prepared again, a bad reagent is replaced.

The panel-versus-reads check is emitted as a p-column in both directions — barcodes the reads carry that no panel declared, and tags a panel declared that the reads never showed.

**Removed:** the dominant-feature ("consensus") call and the per-cell specificity score. A single dominant antigen per cell answers a different question from the one this block now answers, and a specificity magnitude is exactly the narrowing the four-state verdict replaces. The `pl7.app/feature/consensusFeature` and specificity p-columns are no longer emitted. No block in this workspace consumes them.

**Unchanged:** the per-cell UMI count and fraction columns, the negative-control marker column, and the combine-mode settings.

**A single-cell V(D)J dataset is required for the antigen stage.** Without one the block still runs and still emits its per-cell UMI counts and fractions, per-sample QC and per-feature properties exactly as before — but it produces no verdicts, no per-antigen columns and no panel check, rather than producing empty ones. The Verdicts page says so instead of showing an empty table.
