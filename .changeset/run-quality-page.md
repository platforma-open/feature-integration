---
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

A **Run quality** page shows the run's own quality report and the panel-versus-reads check. Both were computed by the verdict stage on every run and emitted, and neither had a page — so a measurement that came out alerting, and a barcode the panel declared that no read carried, were reported to nobody.

The measurements table carries each measurement's status beside the coverage triple behind it — how many were judged, how many unjudged, how many not evaluated — and, where nothing computed a measurement, the reason it was deferred. All of those are shown without opening the column chooser: a status reading "nothing here is wrong" and a level where almost nothing was checkable must not look the same. Status is the plain word the run emitted, filterable through the discrete filter its column already declares, because `unjudged` and `not evaluated` are states rather than degrees of badness and no four-rank tag vocabulary can say that.

Under a **Panel versus reads** separator, the mismatch check shows both directions in one table — barcodes the panel declared that no read carried, and barcodes the reads carried that the panel never declared — told apart by a filterable direction column.

An absent report and an empty one say different things and are answered differently. No report at all means the verdict stage never ran, which happens when no single-cell V(D)J dataset was picked; the page says that instead of drawing a grid. An empty report means the stage ran and found nothing, which for the mismatch check is the outcome you want; the grid renders and says so in place of its rows.

This page is the run's quality. The existing **Per-sample QC** page is unchanged and still shows the per-sample read statistics.
