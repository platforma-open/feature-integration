# @platforma-open/milaboratories.feature-integration.workflow

## 2.1.0

### Minor Changes

- c44afc8: Add progress bar

### Patch Changes

- Updated dependencies [c44afc8]
  - @platforma-open/milaboratories.feature-integration.per-cell-metrics@2.1.0

## 2.0.0

### Major Changes

- 632b4bf: Feature Integration v1 — consolidated notes (all prior changesets combined into one; everything
  major-bumped).

  ## Feature-barcode workflow (core)

  Implement the feature-barcode workflow (plan Tasks 3–4).

  - Workflow: per-sample mitool pipeline (`parse → refine-tags → tag-stat -u`) over the feature-barcode
    FASTQs, then the per-cell-metrics Python software, importing the per-cell results as the A-0010
    contract p-columns keyed `[pl7.app/sampleId, pl7.app/sc/cellId, pl7.app/feature/featureId]`
    (`umiCount` / `fraction` / `consensusFeature` / optional `specificityScore`), exported to the result
    pool for VDJ Multiomic Integration.
  - Tag pattern: cell barcode `CELL`, feature barcode `FEATURE` (mitool's first-class feature tag type,
    mitool#86), molecule `UMI`. Read geometry is configurable (cellLen/umiLen/featureLen, 10x 5' v2
    defaults) — DP-1 "parameterize + proceed".
  - Feature-barcode error correction: `refine-tags` corrects `FEATURE` against the panel whitelist (the
    tag column of the user's tag→feature CSV, emitted as `panel.txt` by the per-cell-metrics `emit-panel`
    entrypoint) — within-Hamming-1 reads snap to a panel barcode and off-panel reads are dropped
    (mitool#87 fixed the `-t TAG#file:` whitelist CLI). `CELL` is de-novo corrected; the tag order scopes
    UMI deduplication to `(cell, feature)`.
  - Python `per_cell_metrics._load()` consumes mitool's aggregated `tag-stat -u` output (the pre-computed
    `unique_UMI` distinct-molecule count) instead of counting raw UMI rows — DP-2.

  ## Per-cell metrics (Python)

  - Front-end plan gaps: enforce the dominance 0.5 floor in the UI; user-mapped CSV barcode/feature
    columns (D4); per-sample QC summary table (reads parsed/matched, cells, features, UMIs); specificity
    - consensus hints on the results page; fix a pre-existing raw tag-stat QC output-name panic.
  - Compute `panelAssignedFraction` in the per-sample QC report (was always blank). It now reads the
    FEATURE correction step's `outputCount / inputCount` — the fraction of reads kept after correcting
    the feature barcode against the panel whitelist — falling back to blank when no refine report is
    available, the report has no FEATURE step, or that step has zero input reads. Covered by new
    behavioral tests in `test_qc_report.py`.
  - Fix a crash when no (cell, feature) pair survives the tag→feature join (a wrong read geometry, or a
    sample with no on-panel reads): both empty-input paths now write header-only CSVs instead of failing
    the whole per-sample run (the UMI-count column is coerced to numeric on read so a header-only
    tag-stat, which polars would infer as String, doesn't break the fraction division).
  - Vectorize the consensus and specificity computations. Both previously looped in Python over every
    cell (consensus) and every (cell, feature) row (specificity, via `iter_rows` + a list of dicts),
    which was slow and held a Python-object copy of the data on top of the polars frame — the first thing
    to OOM on large samples. They are now pure-polars/numpy column operations (group-by + window for the
    dominant-category rule; scipy `beta.cdf` applied to whole columns for the score). Output is
    byte-identical, guarded by the golden consensus test plus oracle tests that cross-check both
    vectorized paths against the pure `consensus_category`/`specificity_score` rules. Measured: 1.2M
    (cell, feature) rows with a control process in ~0.9 s at ~0.7 GB. Empty input stays header-only via
    polars schema-preservation.

  ## Model & UI

  - Feature-parity enhancements from a review against recent blocks:
    - Negative-control dropdown now works: staging parses the tag→feature CSV (`emit-features`
      entrypoint) and the model exposes the discovered feature names as `controlOptions` (was an empty
      stub).
    - Robustness: `tag-stat -u` runs with `--use-local-temp` (avoids shared /tmp exhaustion on the
      on-disk sort); mitool `parse`/`refine-tags` memory is sized from the input reads' blob size via
      `memFormula` (clamped, with the metaExtra floor as fallback) instead of a fixed request.
    - Observability: per-sample × per-step mitool/Python logs, an `isRunning` spinner signal, and a raw
      `tag-stat` QC table surfaced on a QC page (`PlLogView` + `PlAgDataTableV2`).
    - UI/model polish: results table is `retentive` + `withStatus` (no flicker on recompute); the
      tag→feature CSV is validated client-side (required columns) with a feature-count preview; a dynamic
      subtitle reflects the chosen control feature. Export column specs moved to `column-specs.lib.tengo`
      with the standard abundance/order/visibility annotations (identity-neutral).
  - Render all three results tables (`perCellTable`, `tagstatQcTable`, `qcSummaryTable`) with
    `createPlDataTableV2` instead of V3: they are the block's own self-contained, non-batch
    `processColumn` frames that V3's discovery cannot render (the scoped-sources form returns undefined;
    the array-columns form hangs on the upstream Samples & Data FASTQ File-dataset). V2 takes the columns
    directly and auto-joins the `pl7.app/label` sample name onto the sample axis (so tables show the real
    sample name, not the internal sample hash).
  - Harden the model: read the prerun feature/column lists with `getDataAsJsonOrUndefined` instead of
    `getDataAsJson` (the latter throws "Resource has no content." while staging is still computing), and
    reject in `args()` when the barcode-sequence and feature-name columns map to the same CSV column
    (previously only caught by the Python after the full mitool chain ran).
  - Standardize the sidebar subtitle to the block-label pattern: the subtitle reads
    `data.defaultBlockLabel` (falling back to a static string), which a UI watchEffect mirrors from a new
    `suggestedBlockLabel` model output — a dynamic `"<dataset> · <barcode> → <feature>"` string derived
    from the selected FASTQ dataset, the barcode-sequence column, and the feature-name column (each part
    dropped until set). The derivation lives in the output because the subtitle context has no result
    pool.
  - Split the QC page into two full-height single-table sections — "Per-sample QC" and "Raw tag-stat" —
    the standard one-table-per-page pattern.
  - UI tidy-up on the Main and QC pages: whitelist help text moved into a `#tooltip` slot; dataset input
    label renamed to "Select dataset" (block convention); removed the "N features detected" hint; the
    no-negative-control info banner made dismissable (persisted via a `controlInfoDismissed` UI-only
    field); pipeline logs moved into a "Logs" slide-over; fixed the stacked QC tables collapsing to their
    footers.
  - Add column-header tooltips (`pl7.app/description`) to the results tables (Main: Feature fraction,
    Consensus feature, Specificity score; Per-sample QC: Panel-assigned fraction; Raw tag-stat: Distinct
    UMIs (raw)). Annotations only — column identity is unchanged, so the A-0010 downstream contract is
    unaffected.
  - Mark the barcode-sequence and feature-name column dropdowns as `required` (red-star indicator),
    matching their `args()` validation — both must be set before Run enables.
  - Rework the logs into a single "Analysis logs" (wide 80% slide-over) that scales to any sample count,
    replacing the per-tool-step log boxes. While the run is in progress it shows a live
    `Processing… N samples complete` count (no fixed denominator — the block only processes the samples
    present in its feature-barcode dataset, not every project sample); when the run finishes it shows a run-level
    summary — reads parsed, panel-assigned % (median + range), cells/features, and any samples flagged
    for a panel-assigned fraction below 50% (listed by name). qc_report.py now emits per-sample QC as
    JSON (`qcJson`); the model reads it to build the log (completed-sample count + aggregate), with
    sample names from the upstream label column. Detailed per-sample statistics stay on the QC page.

  ## Cell-barcode whitelist (added, then UI removed for v1)

  - Added an optional, chemistry-selected cell-barcode whitelist for CELL correction: a "Cell barcode
    whitelist (10x)" setting pointing `refine-tags` at a 10x built-in (`#builtin:<name>`, e.g.
    `737K-august-2016`) so the emitted `pl7.app/sc/cellId` strings match the VDJ producer by
    construction; default `""` = de-novo.
  - Then removed the UI selector for v1 (Feature Integration is de-novo only): it was not spec-required,
    de-novo already yields the ~99% cross-block join empirically, only the 5' v2 option was verified, and
    the others carried footguns (whitelist/UMI-length could disagree; non-5'v2 VDJ-side alignment
    unconfirmed). Aligning cell barcodes across producers is a chain-level concern to revisit once the
    downstream join is verified end to end. The `#builtin:` workflow/model plumbing is kept dormant
    (`cellWhitelist` stays `""`) as a documented seam. See
    `docs/dormant-features/cell-whitelist-correction-plan.md`.

  ## Robustness / performance / fixes

  - Fix a pre-Run deadlock introduced with the D4 column-mapping UI: the barcode/feature dropdowns
    (`csvColumnOptions` / `controlOptions`) are populated by the prerun reading the uploaded CSV, and
    their values are required by `args()`, but the CSV upload was driven only from the main render (which
    is unreachable until `args()` passes) — a circular dependency. The prerun now exposes the CSV import
    handle and the model adds a second `getImportProgress` driver resolved from `ctx.prerun`
    (`isActive: true`), mirroring `samples-and-data`, so the CSV uploads during staging, the dropdowns
    populate, and Run enables.
  - Fix `perCellTable` failing to render with `partitionKeyLength (0) must be strictly less than the
number of axes (0)`: the per-sample QC summary was an `Xsv` output with empty axes emitted in the
    same `processColumn` as the contract columns, tripping `xsv.importFile`'s assertion and crashing the
    shared render. It is now collected as a `[sampleId]` file map and concatenated + imported once by a
    child template `qc-summary.tpl.tengo` (injecting the real `sampleId` per row), keeping the 8 typed
    metric columns. Also fixes a latent output-name mismatch (`qcSummary` vs the body's `qc` key).
  - Size the `tag-stat` and `per-cell-metrics` steps' memory from input volume (memFormula, base 8 GiB +
    input-blob × multiplier, clamped to 128 GiB) instead of a fixed 8 GiB, mirroring parse/refine
    (tag-stat by the refined.mic blob, per-cell-metrics by the tag-stat TSV). Prevents the two
    input-sized steps from OOMing on large samples. The Advanced-Settings per-process override still
    applies to parse/refine only.

  ## Per-cell results table, QC labels, graph tab

  - The Main results table is now ONE ROW PER CELL `[sampleId, cellId]` instead of the per-(cell ×
    feature) matrix. `per_cell_metrics.py` emits a new `result_per_cell_summary.csv` (imported as the
    table-only `perCellSummary` columns): `Max Feature UMI count`, `Max Feature Fraction`, and (with a
    negative control) `Max Specificity score` — the per-cell max across features — plus a `Features`
    summary string that lists every feature the cell has signal for as `feature : umiCount : fraction`,
    sorted by descending fraction (dominant first), mirroring antibody-sequence-liabilities'
    `pl7.app/isSummary` column. The Main table now shows consensus + these per-cell columns. The
    A-0010 export contract is UNCHANGED — the full per-(cell × feature) `abundance`/`fractions`/
    `consensusFeature`/`specificityScore` matrix still flows to the result pool for VDJ Multiomic
    Integration.
  - Per-sample QC table now shows the real sample name: it moved from `createPlDataTableV3`
    (`selector { mode: "enrichment", maxHops: 0 }`, which never joins the `pl7.app/label` column) to
    `createPlDataTableV2`, which auto-joins it — previously the sample axis rendered the internal hash.
  - All tables render `Sample` first, then `Cell ID` (sampleId is axis 0, cellId axis 1).
  - New "Graph" tab (last in the tab list): an embedded `@milaboratories/graph-maker` violin plot
    (chart-type discrete) defaulting to y = Feature Fraction, grouped by Sample, faceted by Feature. It
    plots the full per-(cell × feature) matrix (a new `graphPf` workflow output) via a `pf` model output
    built from `getRelatedColumns` (this block's columns + axis-compatible pool columns, so the sample
    grouping shows real sample names) with File-valued columns filtered out of the picker.

  ## Assets

  - Update the block and organization logos.

  ## Loading screen — live per-sample progress

  Replace the Main page's static "run the block" placeholder with a live per-sample progress list while a
  run is in progress. Each sample appears as soon as the roster is enumerated ("Queued"), then shows the
  current mitool step by name with its percent + ETA ("Parsing reads" → "Refining barcodes" → "Counting
  UMIs"), and flips to "Done" when its per-sample pipeline finishes. Modeled on blocks/peptide-extraction.

  - Workflow: each mitool step (parse / refine-tags / tag-stat) runs with a shared `[==PROGRESS==]`
    `MI_PROGRESS_PREFIX` sentinel and its stdout is surfaced two ways — a flat per-sample `parseLogStream`
    Log column (roster + early gate) and a nested per-sample × per-step `stepLogs` Log ResourceMap.
  - Model: new outputs — `started`, `sampleProgress` (roster, gated on `getInputsLocked`), `stepProgress`
    (per-sample × per-step latest progress line), `completedSamples` (from the per-sample qcJson), and
    `sampleLabels`.
  - UI: `sampleResults` picks each sample's latest active step; the Main page renders a stack of
    `PlProgressCell`s (no new UI dependencies), gated on `isRunning` so it shows while running and the
    results table shows once done.

  ## Per-cell "Feature breakdown" column

  Rename the per-cell summary column "Features" → **"Feature breakdown"** and reformat its per-feature
  list from `feature : umiCount : fraction` to `feature (fraction%, umiCount UMI)` (percent instead of a
  raw decimal, "<1%" for a nonzero feature that rounds below 1%), bullet-separated with non-breaking
  padding and sorted by descending fraction. Display-only — the exported per-feature matrix (A-0010
  contract) is unchanged.

  ## Main page — progress-only view (in-UI data display hidden for now)

  Operator feedback: the block should not surface result data in its own UI — only per-sample progress,
  matching MiXCR Clonotyping (Main + a QC tab). The Main page now always shows the per-sample progress
  grid; when the run finishes every row settles into its "Done" state instead of swapping to the results
  table. The "Raw tag-stat" and "Feature Fraction Distribution" (Graph) tabs are commented out in
  `sections()`. This is display-only and reversible — the results-table branch, both tabs' pages, their
  model outputs (`perCellTable` / `tagstatQcTable` / `pf` / `pfPcols`), and the routes are all left intact,
  so a tab is re-enabled by uncommenting one line. The "Per-sample QC" tab stays. The A-0010 export
  contract to VDJ Multiomic Integration is unaffected.

  ## CIDConflictError fix — isolate mitool stages in render.create sub-templates

  The block hit a `CIDConflictError` on every run once the per-sample pipeline was exercised. Root cause:
  each mitool step (`parse` / `refine-tags` / `tag-stat`) captures rate-dependent progress via
  `saveStdoutStream()`, and the SDK's `exec.tpl` is `hash_override`-pinned — so any `getFile(...)` off a
  streaming exec has a stable resource identity but a run-to-run drifting content hash. Feeding such a file
  into a downstream exec's `addFile` and then flattening that exec's per-sample output into a p-frame puts a
  stable-id / drifting-hash node on the deduplication path, which conflicts on re-render.

  Fix: every exec stage now runs inside its own `render.create` sub-template (`fb-parse`, `fb-refine`,
  `fb-tagstat`, `fb-downstream`), and `fb-pipeline` is a thin orchestrator that returns only sub-template
  render outputs — never an inline `exec.getFile(...)`. A `render.create` resource has a content-derived
  identity, so a consumer inside a boundary absorbs the drift and its per-sample outputs flatten cleanly
  (the same structure the peptide-extraction block uses). The A-0010 export contract is untouched. NOTE:
  this render.create split was an intermediate step that only _relocated_ the conflict — the actual fix is
  in the next section (removing `saveStdoutStream`), which also removes the live per-step progress grid this
  section originally described. The unconsumed `parse_report.txt` / `refine_report.txt` reports were dropped
  along the way.

  ## CIDConflictError fix — remove `saveStdoutStream` from the mitool execs

  The `render.create` split above was necessary but NOT sufficient: the block still hit `CIDConflictError`
  (non-deterministically on a clean first render, reliably on re-render). Root cause, confirmed against the
  SDK: `saveStdoutStream()` writes the rate-dependent stdout as a saved file INTO the exec's `files` map
  (`exec/index.lib.tengo`), so every `getFile` off a mitool exec resolved to a run-to-run drifting content
  hash while its resource identity stayed pinned (the SDK `exec.tpl` is `hash_override`-pinned) — a
  stable-id / drifting-hash pair that conflicts on any dedup-relevant flatten. `render.create` only
  relocated the conflict; it could not remove it.

  Fix: drop `saveStdoutStream` (and the now-dead progress plumbing) from the parse / refine-tags / tag-stat
  execs, so their outputs are deterministic and dedup cleanly. This trades away the live per-step progress
  bars: the Main grid now shows each sample as `Processing…` → `Done` (driven by the per-sample `qcJson`
  completion plus the input sample-label roster), and the Quality / Read-recovery columns still fill in at
  completion. The A-0010 export contract is unchanged. Two unused `saveStdoutStream` calls in `prerun.tpl`
  were also removed (dead trap surface). Keeping the live grid would require `render.createEphemeral` (loses
  per-sample caching) or an SDK stdout-capture mode that keeps the stream out of the dedup-relevant files map.

### Patch Changes

- Updated dependencies [632b4bf]
  - @platforma-open/milaboratories.feature-integration.per-cell-metrics@2.0.0
