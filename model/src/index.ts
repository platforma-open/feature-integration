import type { InferOutputsType } from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
  createPlDataTableV2,
  createPlDataTableV3,
  DataModelBuilder,
  isPColumnSpec,
  OutputColumnProvider,
  parseResourceMap,
} from "@platforma-sdk/model";
import type { BlockArgs, BlockData } from "./types";

export type { BlockArgs, BlockData } from "./types";

const DOMINANCE_FLOOR = 0.5; // spec A-0012: threshold is user-adjustable down to 0.5, never lower

const dataModel = new DataModelBuilder().from<BlockData>("v1").init(() => ({
  dominanceThreshold: 0.6,
  // 10x 5' v2 read geometry defaults (cell 16 + UMI 10 on R1; feature barcode 15 on R2). DP-1:
  // configurable per-assay, confirm against real FASTQs (Task 0).
  cellLen: 16,
  umiLen: 10,
  featureLen: 15,
  cellWhitelist: "", // de-novo CELL correction by default (spec A-0018 defers the scheme)
  tableState: createPlDataTableStateV2(),
  tagstatTableState: createPlDataTableStateV2(),
  qcSummaryTableState: createPlDataTableStateV2(),
}));

export const platforma = BlockModelV3.create(dataModel)
  .args((data): BlockArgs => {
    if (!data.fbFastqRef) throw new Error("Select the feature-barcode FASTQ");
    if (!data.tagFeatureCsvHandle) throw new Error("Upload the tag→feature CSV");
    if (!data.barcodeSeqColumn) throw new Error("Select the barcode-sequence column in the CSV");
    if (!data.featureNameColumn) throw new Error("Select the feature-name column in the CSV");
    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvHandle: data.tagFeatureCsvHandle,
      barcodeSeqColumn: data.barcodeSeqColumn,
      featureNameColumn: data.featureNameColumn,
      controlFeature: data.controlFeature,
      // canonicalize + clamp to the 0.5 floor
      dominanceThreshold: Math.max(DOMINANCE_FLOOR, data.dominanceThreshold ?? 0.6),
      // read geometry (DP-1); fall back to 10x 5' v2 defaults if unset
      cellLen: data.cellLen ?? 16,
      umiLen: data.umiLen ?? 10,
      featureLen: data.featureLen ?? 15,
      // CELL whitelist: "" = de-novo (default). See docs/cell-whitelist-correction-plan.md.
      cellWhitelist: data.cellWhitelist ?? "",
    };
  })
  .prerunArgs((data) => ({
    fbFastqRef: data.fbFastqRef,
    tagFeatureCsvHandle: data.tagFeatureCsvHandle,
    featureNameColumn: data.featureNameColumn,
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
  // Negative-control dropdown options: the feature/antigen names parsed from the uploaded tag→feature
  // CSV (spec A-0014). The prerun (staging) emit-features step writes them as a JSON array; staging
  // auto-reruns on CSV change so the list stays current without a Run. Empty until the CSV is uploaded
  // and staging completes. NOT isActive: this is a pure prerun read with no side-effecting accessor
  // (isActive is for upload drivers like getImportProgress); retentive avoids a flicker to [] on rerun.
  .retentiveOutput("controlOptions", (ctx): { value: string; label: string }[] => {
    const names = ctx.prerun
      ?.resolve({ field: "featureNames", allowPermanentAbsence: true })
      ?.getDataAsJson<string[]>();
    return (names ?? []).map((name) => ({ value: name, label: name }));
  })
  // CSV column headers (from the prerun emit-columns step) → the barcode/feature column dropdowns
  // (D4). Retentive so the dropdowns don't blank on rerun; empty until the CSV is uploaded + parsed.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] => {
    const cols = ctx.prerun
      ?.resolve({ field: "csvColumns", allowPermanentAbsence: true })
      ?.getDataAsJson<string[]>();
    return (cols ?? []).map((c) => ({ value: c, label: c }));
  })
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
  // Per-sample × per-step mitool/Python log streams (workflow stepLogs ResourceMap keyed
  // [sampleId, step]). Surfaced so the run isn't a black box — the QC page renders one PlLogView per
  // entry. Shape: { isComplete, data: [{ key, value: logHandle }] }.
  .output("stepLogs", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(ctx.outputs.resolve("stepLogs"), (acc) => acc.getLogHandle(), false)
      : undefined,
  )
  // DECISION (2026-07-01, operator): the front-end plan proposed splitting results into a per-cell
  // SUMMARY table [sampleId, cellId] (consensus + aggregates like total UMI / # features) and a
  // separate feature-MATRIX table [sampleId, cellId, featureId]. We deliberately keep ONE unified
  // perCellTable instead: it is coherent, the (cell x feature) rows already let a user read off the
  // strongest features (spec A-0017 forbids a redundant top-N), and PlAgDataTableV2 handles the mixed
  // granularity (umiCount/fraction per [sampleId,cellId,featureId] + consensus broadcast per
  // [sampleId,cellId]). Per-cell aggregate columns (totalUmi, featuresDetected) were NOT added.
  //
  // Per-cell results table. Resolves the workflow's exported perCellTable PFrame; undefined until the
  // workflow emits it (guarded by the UI).
  //
  // Uses createPlDataTableV2 (columns passed directly via getPColumns), NOT V3. This frame is our OWN
  // self-contained, non-batch processColumn output with MIXED granularity. createPlDataTableV3's
  // discovery cannot render it: the object (scoped-sources) form returns undefined for this frame
  // regardless of anchor/maxHops config (verified 2026-07-01), and the array-columns form runs
  // discoverLabelColumnVariants over the ENTIRE result pool and hangs forever on the upstream
  // Samples&Data FASTQ File-dataset (no_data:<sndBlock>:pf.dataset.*). V2 takes the columns as-is and
  // renders the mixed-granularity join — the pattern blocks/peptide-extraction uses for the same
  // non-batch processColumn + samples-and-data setup. retentive avoids blanking the grid on recompute;
  // withStatus feeds PlAgDataTableV2 the OutputWithStatus envelope it renders loading/error from.
  .output(
    "perCellTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("perCellTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.tableState);
    },
    { retentive: true, withStatus: true },
  )
  // Raw tag-stat QC table: per (cell, feature-barcode) distinct-UMI counts before the CSV-driven
  // collapse to feature names. Same non-batch processColumn frame as perCellTable, so it uses
  // createPlDataTableV2 for the same reason (V3 discovery can't render these frames — see perCellTable).
  .output(
    "tagstatQcTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("tagstatQcTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.tagstatTableState);
    },
    { retentive: true, withStatus: true },
  )
  // Per-sample QC summary table: reads parsed/matched, cells/features detected, UMI totals, and
  // panel-assigned fraction. Same self-contained discovery form as perCellTable/tagstatQcTable.
  .output(
    "qcSummaryTable",
    (ctx) => {
      const acc = ctx.outputs?.resolve("qcSummaryTable");
      if (acc === undefined) return undefined;
      const snapshots = new OutputColumnProvider(acc).getAllColumns();
      if (snapshots.length === 0) return undefined;
      const anchorSpec = (snapshots.find((s) => s.spec.name !== "pl7.app/label") ?? snapshots[0])
        .spec;
      return createPlDataTableV3(ctx, {
        columns: {
          sources: [new OutputColumnProvider(acc)],
          anchors: { main: anchorSpec },
          selector: { mode: "enrichment", maxHops: 0 },
        },
        tableState: ctx.data.qcSummaryTableState,
      });
    },
    { retentive: true, withStatus: true },
  )
  .title(() => "Feature Integration")
  // Dynamic, pure-from-data subtitle (no block-label hairpin): reflects the current control choice.
  .subtitle((ctx) =>
    ctx.data.controlFeature
      ? `Control: ${ctx.data.controlFeature}`
      : "Feature-barcode → per-cell antigen counts",
  )
  .sections(() => [
    { type: "link" as const, href: "/" as const, label: "Main" },
    { type: "link" as const, href: "/qc" as const, label: "QC" },
  ])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
