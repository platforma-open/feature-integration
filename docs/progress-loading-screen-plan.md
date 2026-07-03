# Per-sample progress loading screen — plan

Status: proposed (not yet implemented)
Ticket: MILAB-6496 (BEAM)
Author reference blocks: `blocks/peptide-extraction` ("Peptide Profiling")

## Re-verification (2026-07-02)

Re-checked against later background changes (a violin **Graph** tab + a per-cell
results collapse). The plan still holds — those changes are orthogonal to the
loading mechanism. What they touched, and why none of it invalidates the plan:

- `fb-pipeline.tpl.tengo`: added a `perCellSummary` output (defineOutputs +
  return). The mitool chain, `.saveStdoutStream()` on every step, and the dead
  `MI_PROGRESS_PREFIX = "parse"` (line 137) are all unchanged.
- `main.tpl.tengo`: now builds two frames — `perCellTable` (collapsed, one row
  per `[sampleId, cellId]`) and `graphPf` (full `cell x feature` matrix). The
  `processColumn` outputs list gained a `columnSpecs.perCellSummaryOutput(...)`
  append. My "add a `parseLogStream` output" step is the *same* `append(outputs,
  {...})` pattern they just used — it slots in cleanly next to the existing
  appends.
- `model/src/index.ts`: added `pf` / `pfPcols` (graph) outputs and a `/graph`
  section; `perCellTable` still exists (now the collapsed frame). Still only
  `isRunning` — **no** `started` / `sampleKeys` / `parseProgress`. The
  loading-screen gaps are unchanged.
- New `ui/src/pages/GraphPage.vue` (`/graph`): a `GraphMaker` fed the `pf`
  output (an `outputWithStatus`), so it renders its **own** loading/error state
  internally. No progress work needed there — the loading screen stays a
  Main-page (`/`) concern, exactly as scoped below.
- `MainPage.vue` is unchanged (still the static `PlAlert` empty state, lines
  61-63) — the surface this plan replaces.

Only edits below are line-number refreshes; the design is unaffected.

## Problem

While the block runs, the main page shows a single static sentence
(`PlAlert`: "Per-cell feature results appear after you set the inputs in
Settings and run the block."). There is no sense of progress, no per-sample
state, and no indication the run is alive. The only live signal — the
`analysisLog` heartbeat ("Processing… N samples complete.") — is hidden behind
the "Logs" button.

Feedback: the running state should be a useful loading screen.

## Reference: how Peptide Profiling does it

Peptide Profiling shows a **live per-sample table** the instant Run is pressed:
one row per sample appears immediately as "Queued", each row's Progress column
then ticks through stage → % → ETA, and quality tags / mini-charts fill in as
each sample finishes. It is built from three layers:

1. **Workflow emits progress.** Each per-sample mitool step runs with
   `.env("MI_PROGRESS_PREFIX", "[==PROGRESS==]")` and
   `.printErrStreamToStdout().saveStdoutStream()`. The parse step's stdout is
   returned as a Log resource (`parse.getStdoutStream()`), and `processColumn`
   emits it as a sample-keyed Log column (`parseLogStream`), plus a nested
   per-step `stepLogs` map.
2. **Model surfaces it.** `started`, `isRunning`, a `sampleKeys` output
   (`parseResourceMap(resolve("parseLogStream"), a => a.getLogHandle(), true)`
   guarded by `getInputsLocked()` → the full sample roster the moment bodies
   start), and `progress`/`parseProgress`
   (`parseResourceMap(..., a => a.getProgressLogWithInfo(ProgressPrefix), false)`).
3. **UI renders it.** An in-memory `AgGridVue` with a Progress column
   (`headerComponentParams: { type: "Progress" }` + a `progress(cellData)`
   callback returning `{ status, percent, text, suffix }`), status tags, and a
   `PlAgOverlayLoading` overlay (`variant: 'running' | 'not-ready'`).

Files: `blocks/peptide-extraction/workflow/src/parse.tpl.tengo:32,54,64`,
`.../main.tpl.tengo:223-236,241,528`, `.../model/src/index.ts:95-135`,
`.../ui/src/results.ts`, `.../ui/src/pages/MainPage.vue:83-146,204-227`,
`.../ui/src/parseProgress.ts`.

## Why this ports well to Feature Integration

Feature Integration is the same *kind* of block:

- It already runs a per-sample `pframes.processColumn` over `pl7.app/sampleId`
  (`workflow/src/main.tpl.tengo:122`), executing the mitool chain
  (parse → refine-tags → tag-stat) per sample in `fb-pipeline.tpl.tengo`.
- `fb-pipeline.tpl.tengo` **already** sets `MI_PROGRESS_PREFIX` on the parse
  step (line 137) and **already** calls `.saveStdoutStream()` on every mitool
  step.
- The model already exposes `isRunning` and resolves a per-sample `qcJson` map.

The pipeline compute does not change. The gaps are: the captured stdout streams
are never *returned* as outputs, there is no early sample roster, no `started`
output, no per-sample `progress`, and the UI has no progress surface.

## Goal (this plan = Option B)

Keep `perCellTable` (`PlAgDataTableV2`) as the results view. Replace the static
"run the block" alert with a **live per-sample progress grid** while the block
has no results yet, backed by new model outputs. Do not restructure the main
page into a permanent sample grid (that is Option A, out of scope here).

## Non-goals

- No change to the per-sample pipeline logic or its outputs.
- No live progress for steps that do not emit `MI_PROGRESS_PREFIX` lines
  (refine-tags, tag-stat, and the Python steps show coarse state only).
- No Option A main-page restructure (results into slide-overs / secondary view).

## Tiers

### B0 — no workflow change (model + UI only)

Show a per-sample status grid built from the **existing** `qcJson` map: a sample
is "Done" once its `qcJson` entry appears, else "Processing…". Surface
`analysisLog` in the empty state.

Limitation: `qcJson` only settles at the *end* of each sample, so rows cannot
appear "Queued" up front and there is no live % — the grid grows one row per
finished sample. Getting the full roster early needs an early per-sample signal
(B1). Deriving the roster from the upstream `pl7.app/label` column is possible
but the block author already flagged over-count risk against a project-wide
sample total (`model/src/index.ts:231-234`) — so the robust early roster is the
`parseLogStream` + `getInputsLocked()` approach in B1.

### B1 — full Option B (target)

Add the workflow surfacing + model outputs so the grid shows the full roster
immediately and live parse progress per sample. This is the recommended target.

## B1 concrete changes (sketch — illustrative, not final)

### 1. Workflow — surface the parse log stream

`workflow/src/fb-pipeline.tpl.tengo`
- Add `parseLogStream` to `defineOutputs` (line 24).
- Use a distinct sentinel instead of the current `"parse"` value (line 137),
  matching the model's search string. Reuse Peptide's proven sentinel:
  ```tengo
  progressPrefix := "[==PROGRESS==]"
  ...
  env("MI_PROGRESS_PREFIX", progressPrefix).
  ```
- Return the parse step's stdout stream (the exec already saves it, line 153):
  ```tengo
  return {
      abundance: metrics.getFile("result_abundance.csv"),
      ...
      qcJson: qc.getFileContent("result_qc.json"),
      parseLogStream: parse.getStdoutStream()
  }
  ```

`workflow/src/main.tpl.tengo`
- Add a flat per-sample Log output to the `processColumn` outputs list
  (mirrors Peptide `parseLogStreamOutput`, `main.tpl.tengo:223-236`):
  ```tengo
  outputs = append(outputs, {
      type: "Resource",
      spec: {
          kind: "PColumn",
          name: "pl7.app/log",
          domain: { "pl7.app/blockId": blockId },
          valueType: "Log"
      },
      name: "parseLogStream",
      path: ["parseLogStream"]
  })
  ```
- Return it as an output:
  ```tengo
  outputs: {
      ...
      parseLogStream: perSampleResults.outputData("parseLogStream")
  }
  ```

### 2. Model — new outputs

`model/src/index.ts`
- Add the progress constants (copy Peptide's):
  ```ts
  export const ProgressPrefix = "[==PROGRESS==]";
  export const ProgressPattern =
    /(?<stage>[^:]*):(?: *(?<progress>[0-9.]+)%)?(?: *ETA: *(?<eta>.+))?/;
  ```
- Add outputs (`isRunning` already exists):
  ```ts
  .output("started", (ctx) => ctx.outputs !== undefined)
  .output("sampleKeys", (ctx) => {
    const acc = ctx.outputs?.resolve("parseLogStream");
    if (!acc || !acc.getInputsLocked()) return undefined;
    return parseResourceMap(acc, (a) => a.getLogHandle(), true);
  })
  .output("parseProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("parseLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  ```
- `sampleLabels` roster: reuse the label-column lookup already inline in
  `analysisLog` (extract it, or add a dedicated `sampleLabels` output like
  Peptide's `model/src/index.ts:157-189`).

### 3. UI — progress grid in the empty/running state

`ui/src/parseProgress.ts` (new) — copy Peptide's parser verbatim (it imports
`ProgressPattern` from the model).

`ui/src/results.ts` (new, lighter than Peptide's) — build `SampleResult[]` from
`sampleKeys` + `parseProgress` + the `qcJson`-derived done-set:
- per sample: `qcJson present → "Done"`; else `live parse line → that line`;
  else `"Queued"`.

`ui/src/pages/MainPage.vue` — replace the `PlAlert` branch (lines 61-63) with an
in-memory `AgGridVue` (Sample + Progress columns, `PlAgOverlayLoading`), shown
when `!app.model.outputs.perCellTable`. Keep `PlAgDataTableV2` for results. The
Progress column reuses the SDK cell (all components already in
`@platforma-sdk/ui-vue`; no new components). Model reference:
`peptide-extraction/ui/src/pages/MainPage.vue:104-146,204-227`.

## Effort

- Workflow: small — the streams are already captured; surfacing is ~15 lines
  across two files, plus the sentinel fix.
- Model: small — 3 outputs + constants, all copied from a working block.
- UI: medium — the new `results.ts`/`parseProgress.ts` + the MainPage grid
  (the bulk of the work), directly cribbed from Peptide's MainPage.
- No new SDK components, no pipeline changes.

## Open decisions

1. **B0 vs B1.** B1 gives the immediate full "Queued" roster + live parse %;
   B0 is model+UI-only but grows one row per finished sample with no live %.
   Recommendation: B1.
2. **Grid vs. lighter list.** An `AgGridVue` grid matches Peptide and reuses SDK
   cells; a plain list would be less code but diverge from the house style
   (`harness/ui.md`: prefer SDK components). Recommendation: grid.
3. **Which steps report live progress.** Only `parse` emits `MI_PROGRESS_PREFIX`
   today. Adding it to refine/tag-stat means a nested `stepLogs` map (Peptide's
   full form) — more work; defer unless the parse-only signal feels too coarse.

## Testing

- `pnpm build:dev` green across model/workflow/ui.
- Live verify via the pl MCP against a local backend (per project convention):
  run the block on a multi-sample feature-barcode dataset and confirm rows
  appear as "Queued" at start, show live parse %, and flip to "Done" per sample.
- Existing `test/src/wf.test.ts` should be unaffected (no output removed); add a
  assertion that `parseLogStream` is present if a workflow test covers a run.

## As built (2026-07-02) — B1 implemented

Implemented; `pnpm run build:dev` green (model/ui/workflow rebuilt fresh, tengo-check + lint + format
pass). One deliberate deviation from the sketch above:

- **UI uses `PlProgressCell` (a stack), not `AgGridVue`.** feature-integration's UI has no `ag-grid-vue3`
  / `ag-grid-enterprise` dep and its workspace catalog has no ag-grid entries, so peptide's in-memory
  grid would have meant new deps + catalog + lockfile churn. `PlProgressCell` (uikit, re-exported by
  `@platforma-sdk/ui-vue`) renders a determinate/indeterminate progress bar + left/right text
  standalone — same live per-sample UX, zero new dependencies. The Main page renders one cell per
  sample (`step = "<label> — <stage>"`).
- Model exposes `sampleProgress` (combined roster + live progress, gated on `getInputsLocked`) +
  `completedSamples` + `sampleLabels` + `started`; the UI's `results.ts` combines them into rows and
  `parseProgress.ts` parses the mitool line. `sampleLabels` duplicates analysisLog's label lookup (each
  output is a pure ctx function; a shared helper would need the internal ctx type) — candidate cleanup.

**Outstanding: live verification.** Not yet run. The connected BEAM project still has the pre-change
block loaded (the new outputs are absent), so verifying requires reloading the dev block + re-running
on a multi-sample dataset to watch Queued → live parse % → Processing… → Done.

## Constraints

- MILAB-6496 is under a **push-hold**: commit locally only, do not push until the
  whole project is done.
- Spec-first: this doc is the spec; it should be reviewed before code.
