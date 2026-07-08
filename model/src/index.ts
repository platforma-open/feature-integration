import type { BlockRenderCtx, InferOutputsType } from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
  createPlDataTableV2,
  DataModelBuilder,
  isPColumnSpec,
  parseResourceMap,
} from "@platforma-sdk/model";
import { assemblePattern, CELL_TAG, FEATURE_TAG, UMI_TAG, validatePattern } from "./pattern";
import { getPreset } from "./presets";
import type { BlockArgs, BlockData } from "./types";

export { assemblePattern, parsePattern, validatePattern } from "./pattern";
export type { PatternParts } from "./pattern";
export { allPresets, getPreset } from "./presets";
export type { Preset } from "./presets";
export type { BlockArgs, BlockData } from "./types";

const DOMINANCE_FLOOR = 0.5; // spec A-0012: threshold is user-adjustable down to 0.5, never lower

// Per-sample QC metrics as emitted by qc_report.py (result_qc.json), read by the analysisLog output
// and (per sample) by the Main grid's Quality + Read recovery columns (derived in ui/src/results.ts).
export type QcRow = {
  readsTotal: number;
  readsMatched: number;
  matchedFraction: number;
  cellsDetected: number;
  featuresDetected: number;
  totalUniqueUmis: number;
  medianUmisPerCell: number;
  panelAssignedFraction: number | ""; // "" when no refine report (qc_report leaves it blank)
};

// Panel-assigned fraction below this flags a sample in the analysis log (panel / read-geometry issue).
const PANEL_ASSIGNED_FLOOR = 0.5;

// Tag→feature CSV metadata emitted by the prerun's single emit-csv-meta exec (emit_csv_meta.py): the
// column headers (-> the barcode/feature column dropdowns) and each column's distinct values (-> the
// negative-control dropdown, indexed by the chosen feature column). One upload-triggered exec feeds all
// three CSV-derived dropdowns; picking the feature column is then a pure model recompute (no rerun).
type CsvMeta = { columns: string[]; valuesByColumn: Record<string, string[]> };

// mitool tag-stat emits these columns (the CELL/FEATURE/UMI tags — see pattern.ts — plus tag-stat's
// count/totalWeight/unique_<UMI> outputs). A user-mapped CSV barcode/feature column that names one of
// these would corrupt the join or crash group_by in per_cell_metrics.py — which guards it too, but only
// after the full mitool chain has run. args() rejects it here so Run is disabled up front.
const RESERVED_TAGSTAT_COLUMNS = new Set([
  CELL_TAG,
  FEATURE_TAG,
  "count",
  "totalWeight",
  "unique_" + UMI_TAG,
]);

function median(xs: number[]): number | undefined {
  if (xs.length === 0) return undefined;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// sampleId -> display name from the upstream pl7.app/label column whose axis matches the input FASTQ's
// sample axis. Shared by the sampleLabels and analysisLog outputs — kept as a module helper (not one
// output reading another) because each block output is an independent pure function of ctx.
function resolveSampleLabels(
  ctx: BlockRenderCtx<BlockArgs, BlockData>,
): Record<string, string> | undefined {
  const inputRef = ctx.data.fbFastqRef;
  if (inputRef === undefined) return undefined;
  const inputSpec = ctx.resultPool.getSpecByRef(inputRef);
  if (inputSpec === undefined || !isPColumnSpec(inputSpec)) return undefined;
  const sampleAxisSpec = inputSpec.axesSpec[0];
  const obj = ctx.resultPool.getData().entries.find((f) => {
    const spec = f.obj.spec;
    if (!isPColumnSpec(spec)) return false;
    if (spec.name !== "pl7.app/label" || spec.axesSpec.length !== 1) return false;
    const axisSpec = spec.axesSpec[0];
    if (axisSpec.name !== sampleAxisSpec.name) return false;
    if (sampleAxisSpec.domain === undefined || Object.keys(sampleAxisSpec.domain).length === 0)
      return true;
    if (axisSpec.domain === undefined) return false;
    for (const [k, v] of Object.entries(sampleAxisSpec.domain))
      if (axisSpec.domain[k] !== v) return false;
    return true;
  });
  if (obj === undefined) return undefined;
  return Object.fromEntries(
    Object.entries(obj.obj.data.getDataAsJson<{ data: Record<string, string> }>().data).map((e) => [
      JSON.parse(e[0])[0],
      e[1],
    ]),
  ) as Record<string, string>;
}

// Per-sample QC rows from qcJson (workflow saveFileContent -> inline JSON content, read synchronously),
// filtered to settled samples (qcJson is the last per-sample step, so a present entry = that sample
// finished). Shared by completedSamples / sampleQc / analysisLog. Returns [] when outputs haven't
// settled; callers that need to distinguish "not started" map that to undefined themselves.
function parseQcRows(ctx: BlockRenderCtx<BlockArgs, BlockData>) {
  const outputs = ctx.outputs;
  if (outputs === undefined) return [];
  const qcMap = parseResourceMap(
    outputs.resolve("qcJson"),
    (acc) => acc.getDataAsJsonOrUndefined<QcRow>(),
    false,
  );
  return (qcMap?.data ?? []).filter((e) => e.value != null);
}

// Tag→feature CSV metadata from the prerun (emit-csv-meta), or undefined until staging has produced it.
// Shared by the two column dropdowns, the control dropdown, and the csvColumnsLoading signal.
function readCsvMeta(ctx: BlockRenderCtx<BlockArgs, BlockData>): CsvMeta | undefined {
  return ctx.prerun
    ?.resolve({ field: "csvMeta", allowPermanentAbsence: true })
    ?.getDataAsJsonOrUndefined<CsvMeta>();
}

// v1 (pre-preset) data shape: read geometry was three explicit length fields. v2 replaces them with a
// preset selector + a mitool tag-pattern string (see model/src/pattern.ts, model/src/presets).
type BlockDataV1 = Omit<BlockData, "presetId" | "pattern"> & {
  cellLen: number;
  umiLen: number;
  featureLen: number;
};

const dataModel = new DataModelBuilder()
  .from<BlockDataV1>("v1")
  .migrate<BlockData>("v2", ({ cellLen, umiLen, featureLen, ...rest }) => {
    // The shipped default (16/10/15) maps to the fixed BEAM preset; any other geometry maps to the
    // generic preset carrying the assembled pattern (offset 0 — the only layout the v1 UI could express).
    const isBeamDefault = cellLen === 16 && umiLen === 10 && featureLen === 15;
    return isBeamDefault
      ? { ...rest, presetId: "tenx-beam" }
      : {
          ...rest,
          presetId: "generic-fb-umi",
          pattern: assemblePattern({
            cellLen,
            umiLen,
            featureLen,
            featureOffset: 0,
            r1TrailingWildcard: true,
          }),
        };
  })
  .init(() => ({
    dominanceThreshold: 0.6,
    // Default preset = the geometry the block shipped with: 10x 5' v2 BEAM (16 / 10 / 15).
    presetId: "tenx-beam",
    cellWhitelist: "", // de-novo CELL correction by default (spec A-0018 defers the scheme)
    defaultBlockLabel: "",
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
  }));

export const platforma = BlockModelV3.create(dataModel)
  .args((data): BlockArgs => {
    if (!data.fbFastqRef) throw new Error("Select the feature-barcode FASTQ");
    if (!data.tagFeatureCsvHandle) throw new Error("Upload the tag→feature CSV");
    if (!data.barcodeSeqColumn) throw new Error("Select the barcode-sequence column in the CSV");
    if (!data.featureNameColumn) throw new Error("Select the feature-name column in the CSV");
    // The barcode-sequence and feature-name roles must map to different CSV columns. The Python guards
    // this too (per_cell_metrics.py), but only after the full mitool chain runs; rejecting it here
    // disables Run up front instead of burning the pipeline to fail at the end.
    if (data.barcodeSeqColumn === data.featureNameColumn)
      throw new Error("Barcode-sequence and feature-name columns must be different");
    // Reject a CSV column that collides with a reserved tag-stat column up front (same reason as the
    // barcode≠feature guard above: the Python guards it too, but only after the full mitool chain runs).
    for (const [role, col] of [
      ["Barcode-sequence", data.barcodeSeqColumn],
      ["Feature-name", data.featureNameColumn],
    ] as const) {
      if (RESERVED_TAGSTAT_COLUMNS.has(col))
        throw new Error(
          `${role} column "${col}" collides with a reserved tag-stat column; pick another`,
        );
    }
    // Read geometry: resolve the selected preset to its effective pattern (fixed preset owns it; the
    // generic preset carries it in data.pattern), validate it loosely (the required CELL/UMI/FEATURE
    // tags + R2 capture must be present — the workflow's refine-tags/tag-stat reference them by name;
    // anything else is passed to mitool verbatim), then hand the string to the workflow directly.
    const preset = getPreset(data.presetId);
    if (!preset) throw new Error("Select a read-geometry preset");
    const pattern = preset.userConfigurable ? (data.pattern ?? "") : preset.pattern;
    const patternError = validatePattern(pattern);
    if (patternError) throw new Error(patternError);

    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvHandle: data.tagFeatureCsvHandle,
      barcodeSeqColumn: data.barcodeSeqColumn,
      featureNameColumn: data.featureNameColumn,
      controlFeature: data.controlFeature,
      // canonicalize + clamp to the 0.5 floor
      dominanceThreshold: Math.max(DOMINANCE_FLOOR, data.dominanceThreshold ?? 0.6),
      pattern,
      tags: { cell: CELL_TAG, umi: UMI_TAG, feature: FEATURE_TAG },
      // CELL whitelist: "" = de-novo (default). See docs/dormant-features/cell-whitelist-correction-plan.md.
      cellWhitelist: data.cellWhitelist ?? "",
    };
  })
  // Staging depends only on the CSV: emit-csv-meta emits every column's values in one exec, so the
  // negative-control dropdown no longer needs a rerun when the feature column changes (the model indexes
  // the already-emitted map). featureNameColumn is deliberately NOT a prerun arg.
  .prerunArgs((data) => ({
    fbFastqRef: data.fbFastqRef,
    tagFeatureCsvHandle: data.tagFeatureCsvHandle,
  }))
  // NOTE on enrichments (.enriches): intentionally NOT declared. `.enriches(args => PlRef[])` is for a
  // block that produces columns sharing the key space of a ref it holds (clonotype-browser enriches its
  // inputAnchor; cell-browser enriches its countsRef). This block introduces a NEW cell/feature key
  // space [sampleId, cellId, featureId] off a FASTQ input, and holds no ref to the downstream VDJ
  // dataset it would enrich — so there is nothing to enrich here. VDJ Multiomic Integration discovers
  // these columns under its VDJ anchor via the pl7.app/sc/cellLinker (linker traversal), not via
  // enrichment. Revisit only if the live cross-block discovery check shows otherwise.

  // feature-barcode FASTQ options (file-valued sequencing columns, fastq / fastq.gz)
  .output("fastqOptions", (ctx) =>
    ctx.resultPool.getOptions((spec) => {
      if (!isPColumnSpec(spec)) return false;
      const ext = spec.domain?.["pl7.app/fileExtension"];
      return (
        spec.name === "pl7.app/sequencing/data" &&
        (spec.valueType as string) === "File" &&
        (ext === "fastq" || ext === "fastq.gz")
      );
    }),
  )
  // Suggested block label for the sidebar subtitle: "<dataset> · <barcode> - <feature>", derived from
  // the current inputs. Computed here (not in .subtitle) because the subtitle context has no result
  // pool; a UI watchEffect copies this into data.defaultBlockLabel. Each part is dropped until set.
  .output("suggestedBlockLabel", (ctx): string | undefined => {
    const parts: string[] = [];
    const ref = ctx.data?.fbFastqRef;
    if (ref) {
      const label = ctx.resultPool
        .getOptions((spec) => {
          if (!isPColumnSpec(spec)) return false;
          const ext = spec.domain?.["pl7.app/fileExtension"];
          return (
            spec.name === "pl7.app/sequencing/data" &&
            (spec.valueType as string) === "File" &&
            (ext === "fastq" || ext === "fastq.gz")
          );
        })
        .find((o) => o.ref.blockId === ref.blockId && o.ref.name === ref.name)?.label;
      if (label) parts.push(label);
    }
    if (ctx.data?.barcodeSeqColumn && ctx.data?.featureNameColumn) {
      parts.push(`${ctx.data.barcodeSeqColumn} - ${ctx.data.featureNameColumn}`);
    }
    return parts.length > 0 ? parts.join(" · ") : undefined;
  })
  // Negative-control dropdown options (spec A-0014): the distinct values of the chosen feature-name
  // column, from the prerun's emit-csv-meta valuesByColumn map. No rerun on column change — the map
  // already carries every column's values, so picking the feature column just re-indexes here.
  // Retentive avoids a flicker to [] on rerun; empty until the CSV is uploaded and staging completes.
  .retentiveOutput("controlOptions", (ctx): { value: string; label: string }[] => {
    const col = ctx.data.featureNameColumn;
    const names = col ? (readCsvMeta(ctx)?.valuesByColumn?.[col] ?? []) : [];
    return names.map((name) => ({ value: name, label: name }));
  })
  // CSV column headers (from the prerun emit-csv-meta step) → the barcode/feature column dropdowns
  // (D4). Retentive so the dropdowns don't blank on rerun; empty until the CSV is uploaded + parsed.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] =>
    (readCsvMeta(ctx)?.columns ?? []).map((c) => ({ value: c, label: c })),
  )
  // True while the uploaded CSV is still being parsed by staging (handle set, but emit-csv-meta hasn't
  // produced csvMeta yet) — lets the UI show a "reading columns…" state instead of silent empty
  // dropdowns. NOT retentive: it must report the live loading state, including on a CSV swap.
  .output(
    "csvColumnsLoading",
    (ctx): boolean => !!ctx.data.tagFeatureCsvHandle && readCsvMeta(ctx) === undefined,
  )
  // Drives the tag→feature CSV upload: getImportProgress() registers the import handle with the
  // middle-layer upload driver so the CSV bytes are actually pushed; isActive keeps it computing even
  // when the block isn't being viewed. Without this the CSV never uploads and every per-sample body
  // hangs on __extra_tagsCsv (mirrors immune-assay-data index.ts / samples-and-data).
  .output(
    "tagFeatureCsvImportHandle",
    (ctx) => ctx.outputs?.resolve("tagFeatureCsvImportHandle")?.getImportProgress(),
    { isActive: true },
  )
  // Same upload driver, but resolved from the PRERUN (staging) render — this is the one that fires
  // before Run. The D4 dropdowns (csvColumnOptions / controlOptions) are populated by the prerun
  // reading the uploaded CSV, and their values are REQUIRED by args(). The main driver above only
  // fires once args() passes, so on its own it deadlocks: no upload → empty dropdowns → args() throws
  // → no main render → no upload. Driving the upload from staging breaks the cycle (mirrors
  // samples-and-data's "Drives prerun file uploads" getImportProgress).
  .output(
    "tagFeatureCsvImportHandlePrerun",
    (ctx) =>
      ctx.prerun
        ?.resolve({ field: "tagFeatureCsvImportHandle", allowPermanentAbsence: true })
        ?.getImportProgress(),
    { isActive: true },
  )
  // True while the main run is executing (no output/context field settled yet) — drives the block
  // spinner via the app.ts progress callback.
  .output("isRunning", (ctx) => ctx.outputs?.getIsReadyOrError() === false)
  // True once the main workflow has begun producing outputs (ctx.outputs settles) — lets the Main page
  // swap the static "run the block" hint for the live per-sample progress grid.
  .output("started", (ctx) => ctx.outputs !== undefined)
  // NOTE: the live per-step progress outputs (sampleProgress / stepProgress) were removed together with
  // the mitool stdout streams (saveStdoutStream) that fed them — the stream drifted the exec's files map
  // and caused the CIDConflictError. The Main grid now derives per-sample Processing/Done from
  // completedSamples + the sampleLabels roster (ui/src/results.ts).
  // sampleIds whose per-sample pipeline has finished. qcJson is the LAST per-sample step and is inline
  // JSON content, so getDataAsJsonOrUndefined reads it synchronously — the done-set drives the grid's
  // "Done" state (a sample not in this set is still Processing).
  .output("completedSamples", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;
    return parseQcRows(ctx).map((e) => String(e.key[0]));
  })
  // Per-sample QC metrics (from qcJson) keyed by sampleId — drives the Main grid's Quality + Read
  // recovery columns (derived in ui/src/results.ts). Present per sample once its qc step settles (same
  // source as completedSamples), so the two columns fill in as each sample finishes.
  .output("sampleQc", (ctx): Record<string, QcRow> | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const out: Record<string, QcRow> = {};
    for (const e of parseQcRows(ctx)) out[String(e.key[0])] = e.value as QcRow;
    return out;
  })
  // sampleId -> display name (upstream pl7.app/label), for the progress grid's Sample column. Shares
  // resolveSampleLabels with analysisLog; kept as its own output because outputs cannot read one another.
  .output("sampleLabels", (ctx): Record<string, string> | undefined => resolveSampleLabels(ctx))
  // The block's single "Analysis logs" (lines shown in the UI's wide slide-over), built from the
  // per-sample QC JSON (qcJson), which settles incrementally as each sample's qc step finishes:
  //   • while the run is in progress → a live count of samples finished so far ("Processing… N …");
  //   • when every sample is done   → a run-level summary (aggregate reads/panel-assigned/cells +
  //     any samples flagged for a panel-assigned fraction below PANEL_ASSIGNED_FLOOR, by name).
  // One area regardless of sample count; detailed per-sample stats live on the QC page (qcSummaryTable).
  .output("analysisLog", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;

    // Sample labels (sampleId -> name) from the upstream pl7.app/label column — display names for
    // flagged samples. Shared resolver with the sampleLabels output.
    const labels = resolveSampleLabels(ctx);
    // Per-sample QC metrics; each entry appears as that sample's qc step finishes (shared with
    // completedSamples / sampleQc). qcJson is inline JSON content read synchronously.
    const entries = parseQcRows(ctx);
    const done = entries.length;
    const running = ctx.outputs.getIsReadyOrError() === false;

    // While the run is in progress → a live count of samples finished so far. No fixed denominator:
    // the block only processes the samples present in its feature-barcode dataset, which isn't reliably
    // known until the run completes (a project-wide sample total would over-count and make a finished
    // run look stuck). On a crash the count freezes at how far it got, next to the block's error state.
    if (running) {
      return done === 0
        ? ["Processing…"]
        : [`Processing… ${done} sample${done === 1 ? "" : "s"} complete.`];
    }
    if (done === 0) return undefined;

    // Run-level summary.
    const rows = entries.map((e) => e.value as QcRow);
    const num = (x: number | ""): number | undefined => (typeof x === "number" ? x : undefined);
    const pct = (x: number) => `${Math.round(x * 100)}%`;
    const nf = (x: number) => x.toLocaleString("en-US");

    const readsTotal = rows.reduce((s, r) => s + (r.readsTotal ?? 0), 0);
    const cellsTotal = rows.reduce((s, r) => s + (r.cellsDetected ?? 0), 0);
    const features = Math.max(...rows.map((r) => r.featuresDetected ?? 0));
    const matched = rows
      .map((r) => r.matchedFraction)
      .filter((x): x is number => typeof x === "number");
    const assigned = rows
      .map((r) => num(r.panelAssignedFraction))
      .filter((x): x is number => x !== undefined);
    const flagged = entries.filter((e) => {
      const a = num((e.value as QcRow).panelAssignedFraction);
      return (
        (a !== undefined && a < PANEL_ASSIGNED_FLOOR) ||
        ((e.value as QcRow).cellsDetected ?? 0) === 0
      );
    });

    const medMatched = median(matched);
    const medAssigned = median(assigned);
    const lines: string[] = ["Feature Integration — analysis log", ""];
    lines.push(`Processed ${done} sample${done === 1 ? "" : "s"}.`);
    lines.push(
      `Reads parsed: ${nf(readsTotal)} total` +
        (medMatched !== undefined
          ? ` · ${pct(medMatched)} matched the read pattern (median)`
          : "") +
        ".",
    );
    if (medAssigned !== undefined && assigned.length > 0) {
      lines.push(
        `Panel-assigned: ${pct(medAssigned)} of reads (median; range ${pct(Math.min(...assigned))}–${pct(Math.max(...assigned))}).`,
      );
    }
    lines.push(`Cells detected: ${nf(cellsTotal)} · ${features} features.`);
    lines.push("");
    if (flagged.length > 0) {
      const names = flagged.map((e) => labels?.[String(e.key[0])] ?? String(e.key[0]));
      lines.push(
        `${flagged.length} sample${flagged.length === 1 ? "" : "s"} flagged — panel-assigned fraction below ${pct(PANEL_ASSIGNED_FLOOR)} (or zero cells): ${names.join(", ")}.`,
      );
      lines.push("  See the QC page for per-sample detail.");
    } else {
      lines.push("No samples flagged.");
    }
    lines.push("", "Analysis complete. Full per-sample statistics are on the QC page.");
    return lines;
  })
  // DECISION (2026-07-02, operator): the Main table is now ONE ROW PER CELL [sampleId, cellId].
  // Supersedes the 2026-07-01 "single unified matrix table" decision — the per-(cell x feature) rows
  // moved OUT of this table into the collapsed workflow frame (consensus + the per-cell summary
  // columns: Max Feature UMI count, Max Feature Fraction, Max Specificity score, and a "Feature
  // breakdown" string listing every feature as "feature (fraction%, umi)" sorted by descending fraction). The
  // per-feature matrix is not lost: it is still exported to the result pool (perCellFeatures, the
  // A-0010 contract) for VDJ Multiomic Integration. This output resolves the workflow's collapsed
  // perCellTable PFrame; undefined until the workflow emits it (guarded by the UI).
  //
  // Uses createPlDataTableV2 (columns passed directly via getPColumns), NOT V3. This frame is our OWN
  // self-contained, non-batch processColumn output. createPlDataTableV3's discovery cannot render it:
  // the object (scoped-sources) form returns undefined for this frame regardless of anchor/maxHops
  // config (verified 2026-07-01), and the array-columns form runs discoverLabelColumnVariants over the
  // ENTIRE result pool and hangs forever on the upstream Samples&Data FASTQ File-dataset
  // (no_data:<sndBlock>:pf.dataset.*). V2 takes the columns as-is and auto-joins the sampleId label —
  // the pattern blocks/peptide-extraction uses for the same non-batch processColumn + samples-and-data
  // setup. retentive avoids blanking the grid on recompute; withStatus feeds PlAgDataTableV2 the
  // OutputWithStatus envelope it renders loading/error from.
  .output(
    "perCellTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("perCellTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.tableState);
    },
    { retentive: true, withStatus: true },
  )
  // Per-sample QC summary table: reads parsed/matched, cells/features detected, UMI totals, and
  // panel-assigned fraction. Uses createPlDataTableV2 (columns passed directly via getPColumns) like
  // perCellTable. The earlier V3 form used selector { mode: "enrichment", maxHops: 0 },
  // which never traverses to the upstream pl7.app/label column — so the sampleId axis rendered the raw
  // sample hash instead of the human sample name. createPlDataTableV2 runs getAllLabelColumns over the
  // result pool and auto-joins the matching sampleId label, giving the real sample name.
  .output(
    "qcSummaryTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("qcSummaryTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.qcSummaryTableState);
    },
    { retentive: true, withStatus: true },
  )
  .title(() => "Feature Integration")
  // Standard block-label subtitle. The subtitle render context is args-only (no result pool / outputs
  // — touching them renders "Invalid subtitle"), so the dynamic "<dataset> · <barcode> - <feature>"
  // string is derived in the `suggestedBlockLabel` OUTPUT (which HAS the pool) and copied into
  // `defaultBlockLabel` by a UI watchEffect (the sanctioned block-label pattern). The subtitle only
  // reads `ctx.data`. Guard `ctx.data` — it can be undefined before block storage is parsed.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // Main (the per-sample progress grid) is always shown. The result tabs — Per-sample QC and the
  // per-cell results table — appear only once the block has produced outputs, so a fresh/unrun block
  // shows only Main. ctx.outputs settles when the workflow starts emitting (the same signal as the
  // `started` output). The Graph and Raw tag-stat views were removed (2026-07-03, operator).
  .sections((ctx) => {
    const hasRun = ctx.outputs !== undefined;
    return [
      { type: "link" as const, href: "/" as const, label: "Main" },
      ...(hasRun
        ? [
            { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
            { type: "link" as const, href: "/results" as const, label: "Per-cell results" },
          ]
        : []),
    ];
  })
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
