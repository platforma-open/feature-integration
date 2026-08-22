---
'@platforma-open/milaboratories.feature-integration': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

Read the tag-feature panel in the UI, so the column dropdowns fill on the pick.

Choosing a panel CSV used to start a round trip — upload the blob, run a staging exec, read its JSON — before the barcode-sequence, feature-name and negative-control dropdowns had anything to offer. The exec itself was 59 lines of stdlib Python, but its artifact shared the package's `requirements.txt`, so the backend built a venv holding polars, numpy and scipy to run it, and paid that again after every version bump.

The UI now reads the file directly. A local pick is read from disk on the gesture and the dropdowns fill immediately. A pick from remote storage, or a project opened where the original file never existed, is read from the CSV blob the prerun already exports — the same parser over the same bytes, so there is no second implementation to keep in agreement.

- **`emit-csv-meta` is gone**: the entrypoint, `emit_csv_meta.py`, and its tests. The prerun now has no exec at all, so nothing builds a venv during staging. It still imports and exports the CSV, which is what drives the upload.
- **`csvMetaSnapshot` in block data** carries the parsed panel, tagged with the handle it was read from. `readCsvMeta` returns it only while that tag matches the CSV currently picked, so a snapshot cannot be read against a different file.
- **A failure is now shown, not logged.** With no workflow-side parser left to fall back on, a discarded parse error would leave empty dropdowns and no explanation, so the reason appears next to the file input.
- Parsing uses `csv-parse`, as in the xsv-import block: RFC 4180 quoting, and both LF and CRLF endings. Real panel files are CRLF.

The `prerunArgs` projection is unchanged and must stay that way — it is what keeps the UI's write to `csvMetaSnapshot` from re-rendering staging. The comment above it now says so.

No change to `args()`, to the production workflow, or to any exported column. Existing projects re-read their panel from the exported blob on open.
