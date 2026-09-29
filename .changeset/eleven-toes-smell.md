---
"@platforma-open/milaboratories.feature-integration.feature-properties": patch
"@platforma-open/milaboratories.feature-integration.per-cell-metrics": minor
"@platforma-open/milaboratories.feature-integration.mitool-support": patch
"@platforma-open/milaboratories.feature-integration.verdicts": minor
"@platforma-open/milaboratories.feature-integration.workflow": minor
"@platforma-open/milaboratories.feature-integration": minor
"@platforma-open/milaboratories.feature-integration.model": minor
"@platforma-open/milaboratories.feature-integration.kind": major
"@platforma-open/milaboratories.feature-integration.test": patch
"@platforma-open/milaboratories.feature-integration.ui": minor
---

Deduplication, block kind v2, and panel and dataset checks

Results are now reused instead of recomputed. A second identical block in the same project runs no
analysis at all, and a block in a new project over the same data runs only table conversions. Sample
ids are anonymized through the per-sample processing, so the same data yields the same work in every
project, in both the default and the sample-aware mode. Tables now appear as soon as their own
results are ready instead of when the whole block finishes.

The block kind moves to 2.0.0. Its init-params contract now also carries the run mode and read
limit, the aggregate-barcode knobs and the QC warn/error lines. The panel CSV is narrowed to
`index://` handles, and the panel column choices travel only with it, since an `upload://` handle
names a file on one machine and resolves nowhere else. The per-barcode grouping rule is no longer
accepted. CPU and memory stay out of the contract.

The "One identity per barcode" option is removed from Target Identity: leaving it empty gives one
identity per barcode. Run is disabled, with a warning, when the tag-barcode FASTQ dataset and the
single-cell V(D)J dataset have no sample in common. A cleared Sample column is no longer
re-filled by the auto-suggestion. New per-sample metrics evaluate binding against a baseline, the
reference-reading histogram uses a log scale, and QC descriptions move from the software to the UI.

The Python software is split into four packages (verdicts, mitool-support, feature-properties and
per-cell-metrics), so a change in one script no longer re-runs the whole pipeline. Upgrades the SDK
to model/ui 1.84 and workflow-tengo 6.12.
