---
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

UI tidy-up on the Main and QC pages.

- Cell-barcode whitelist: move the "snap to a 10x whitelist…" help text into a `#tooltip` slot.
- Rename the dataset input label from "Feature-barcode FASTQ" to "Select dataset" (block convention).
- Remove the "N features detected" hint under the tag→feature CSV upload.
- Make the no-negative-control info banner dismissable (`closable`), persisted via a new
  `controlInfoDismissed` UI-only data field.
- Move the pipeline logs off the QC page into a "Logs" slide-over opened from a button next to
  Settings (top of the Main page).
- Fix the QC page: the two stacked `PlAgDataTableV2` tables collapsed to just their footers because
  the component is `height: 100%` and had no bounded parent. Each table now sits in a bounded-height
  container so both render.
