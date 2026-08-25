---
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
---

A sample's report is its own measurement set, and the Quality tag is that set's rollup.

The Quality Checks tab listed **three** hand-written checks against a threshold the UI held itself, while the software computed **nine** sample-level measurements with their own statuses and their own line provenance. A reader opened a sample, met three rows and a green badge, and concluded the sample had been checked. Six measurements were omitted without saying so, and a list that silently drops what it could not check reads exactly like a list that checked everything and found nothing wrong.

**The tab now lists every sample-level measurement the software declares**, in declaration order, including the ones nothing computed. Each row carries its status where a line stands behind it, its value, and — where there is no value — the reason in place of one. A measurement with no line carries no status and shows an em-dash: there is no fourth status word, and which of the two no-status cases applies is read from the value.

**A blank and a zero are opposite findings, so nothing is ever blank.** Every valueless row states why: the aggregate-barcode fraction because nothing in this block detects aggregates, the sticky measurement because no cell of the sample carries a comparator reading, the read-level fractions because the refine-tags report supplied no step with input reads. The reasons travel in `result_qc.csv` too, on a `reason` column that previously carried only the deferred ones.

**The sample's rolled-up status sits at the top of the list, with its coverage beside it** — how many measurements were judged, how many were computed with no line to judge them against, and how many nothing computed. Whether something is wrong and whether anybody looked are different questions and are answered separately.

**The Main grid's Quality tag is that same rollup and is computed nowhere else.** The UI no longer holds a QC threshold of its own: `PANEL_ASSIGNED_LINE`, `qcChecks`, `qualityStatus` and `QcCheck` are gone, and with them the second copy of a line that could drift from the software's. The tag and the report beside it cannot disagree about one sample.

A measurement whose finding belongs to a reagent rather than to the sample keeps its own status on its row and stays out of the sample's rollup, and its row says so — otherwise a reader meets a status on the page that the tag does not carry.

The verdict step emits `result_qc_by_sample.json` alongside `result_qc.csv`, and the model reads it as `sampleQcReport`. The frame remains the artefact every other reader takes; the report is the same measurements keyed by sample, for a view that holds one sample at a time.
