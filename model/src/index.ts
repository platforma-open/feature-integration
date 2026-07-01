import type { InferOutputsType } from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
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
  // granularity. Per-cell aggregate columns (totalUmi, featuresDetected) were NOT added. Revisit only
  // if users ask for a one-row-per-cell overview; if so, add aggregates in per_cell_metrics and a
  // second createPlDataTableV3 output rather than reshaping this one.
  //
  // Per-cell results table. Resolves the workflow's exported perCellTable PFrame; undefined until the
  // workflow emits it, so the UI guards it.
  //
  // Use the discoverColumnOptions (object) form of createPlDataTableV3 with `sources` scoped to our
  // OWN exported PFrame. This is deliberate: the array-columns form runs discoverLabelColumnVariants,
  // which enumerates the ENTIRE result pool to find axis labels and blocks forever on the upstream
  // Samples&Data FASTQ File-dataset's non-PFrame-queryable data (unstable marker
  // no_data:<sndBlock>:pf.dataset.*). Passing `sources: [OutputColumnProvider(acc)]` confines column +
  // label discovery to this block's own columns (resolveProviders uses only the given sources), and
  // maxHops:0 disables linker traversal since the PFrame is self-contained. Mirrors
  // 3d-structure-prediction / 3d-structure-clustering.
  //
  // retentive + withStatus: retentive avoids blanking the grid to undefined on recompute (cell-browser
  // pattern; no V3 builder shortcut for the combo — long-form), withStatus feeds PlAgDataTableV2 the
  // OutputWithStatus envelope it renders loading/error from.
  .output(
    "perCellTable",
    (ctx) => {
      const acc = ctx.outputs?.resolve("perCellTable");
      if (acc === undefined) return undefined;
      const snapshots = new OutputColumnProvider(acc).getAllColumns();
      if (snapshots.length === 0) return undefined;
      // Anchor on any value-bearing column — discovery is axis-driven, so only its axesSpec matters.
      const anchorSpec = (snapshots.find((s) => s.spec.name !== "pl7.app/label") ?? snapshots[0])
        .spec;
      return createPlDataTableV3(ctx, {
        columns: {
          sources: [new OutputColumnProvider(acc)],
          anchors: { main: anchorSpec },
          selector: { mode: "enrichment", maxHops: 0 },
        },
        tableState: ctx.data.tableState,
      });
    },
    { retentive: true, withStatus: true },
  )
  // Raw tag-stat QC table: per (cell, feature-barcode) distinct-UMI counts before the CSV-driven
  // collapse to feature names. Same self-contained discovery form as perCellTable (avoids the
  // whole-pool label-discovery hang described above).
  .output(
    "tagstatQcTable",
    (ctx) => {
      const acc = ctx.outputs?.resolve("tagstatQcTable");
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
        tableState: ctx.data.tagstatTableState,
      });
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
