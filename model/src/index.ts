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

// Per-sample QC metrics as emitted by qc_report.py (result_qc.json), read by the analysisLog output.
type QcRow = {
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

function median(xs: number[]): number | undefined {
  if (xs.length === 0) return undefined;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[m] : (s[m - 1] + s[m]) / 2;
}

const dataModel = new DataModelBuilder().from<BlockData>("v1").init(() => ({
  dominanceThreshold: 0.6,
  // 10x 5' v2 read geometry defaults (cell 16 + UMI 10 on R1; feature barcode 15 on R2). DP-1:
  // configurable per-assay, confirm against real FASTQs (Task 0).
  cellLen: 16,
  umiLen: 10,
  featureLen: 15,
  cellWhitelist: "", // de-novo CELL correction by default (spec A-0018 defers the scheme)
  defaultBlockLabel: "",
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
    // The barcode-sequence and feature-name roles must map to different CSV columns. The Python guards
    // this too (per_cell_metrics.py), but only after the full mitool chain runs; rejecting it here
    // disables Run up front instead of burning the pipeline to fail at the end.
    if (data.barcodeSeqColumn === data.featureNameColumn)
      throw new Error("Barcode-sequence and feature-name columns must be different");
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
      // CELL whitelist: "" = de-novo (default). See docs/dormant-features/cell-whitelist-correction-plan.md.
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
  // Negative-control dropdown options: the feature/antigen names parsed from the uploaded tag→feature
  // CSV (spec A-0014). The prerun (staging) emit-features step writes them as a JSON array; staging
  // auto-reruns on CSV change so the list stays current without a Run. Empty until the CSV is uploaded
  // and staging completes. NOT isActive: this is a pure prerun read with no side-effecting accessor
  // (isActive is for upload drivers like getImportProgress); retentive avoids a flicker to [] on rerun.
  .retentiveOutput("controlOptions", (ctx): { value: string; label: string }[] => {
    const names = ctx.prerun
      ?.resolve({ field: "featureNames", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<string[]>();
    return (names ?? []).map((name) => ({ value: name, label: name }));
  })
  // CSV column headers (from the prerun emit-columns step) → the barcode/feature column dropdowns
  // (D4). Retentive so the dropdowns don't blank on rerun; empty until the CSV is uploaded + parsed.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] => {
    const cols = ctx.prerun
      ?.resolve({ field: "csvColumns", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<string[]>();
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
  // The block's single "Analysis logs" (lines shown in the UI's wide slide-over), built from the
  // per-sample QC JSON (qcJson), which settles incrementally as each sample's qc step finishes:
  //   • while the run is in progress → a live count of samples finished so far ("Processing… N …");
  //   • when every sample is done   → a run-level summary (aggregate reads/panel-assigned/cells +
  //     any samples flagged for a panel-assigned fraction below PANEL_ASSIGNED_FLOOR, by name).
  // One area regardless of sample count; detailed per-sample stats live on the QC page (qcSummaryTable).
  .output("analysisLog", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;

    // Sample labels (sampleId -> name) from the upstream pl7.app/label column — display names for
    // flagged samples and the total sample count (heartbeat denominator). Mirrors mixcr-clonotyping.
    let labels: Record<string, string> | undefined;
    const inputRef = ctx.data.fbFastqRef;
    if (inputRef !== undefined) {
      const inputSpec = ctx.resultPool.getSpecByRef(inputRef);
      if (inputSpec !== undefined && isPColumnSpec(inputSpec)) {
        const sampleAxisSpec = inputSpec.axesSpec[0];
        const obj = ctx.resultPool.getData().entries.find((f) => {
          const spec = f.obj.spec;
          if (!isPColumnSpec(spec)) return false;
          if (spec.name !== "pl7.app/label" || spec.axesSpec.length !== 1) return false;
          const axisSpec = spec.axesSpec[0];
          if (axisSpec.name !== sampleAxisSpec.name) return false;
          if (
            sampleAxisSpec.domain === undefined ||
            Object.keys(sampleAxisSpec.domain).length === 0
          )
            return true;
          if (axisSpec.domain === undefined) return false;
          for (const [k, v] of Object.entries(sampleAxisSpec.domain))
            if (axisSpec.domain[k] !== v) return false;
          return true;
        });
        if (obj !== undefined) {
          labels = Object.fromEntries(
            Object.entries(obj.obj.data.getDataAsJson<{ data: Record<string, string> }>().data).map(
              (e) => [JSON.parse(e[0])[0], e[1]],
            ),
          ) as Record<string, string>;
        }
      }
    }
    // Per-sample QC metrics; each entry appears as that sample's qc step finishes.
    // qcJson is per-sample inline JSON content (workflow saveFileContent + getFileContent), so
    // getDataAsJson reads it synchronously as the parsed row. (getDataAsJson on a saveFile'd file
    // *handle* returns nothing — the block-dev gotcha; the inline-content pattern is what fixes it.)
    const qcMap = parseResourceMap(
      ctx.outputs.resolve("qcJson"),
      (acc) => acc.getDataAsJsonOrUndefined<QcRow>(),
      false,
    );
    const entries = (qcMap?.data ?? []).filter((e) => e.value != null);
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
  // Standard block-label subtitle. The subtitle render context is args-only (no result pool / outputs
  // — touching them renders "Invalid subtitle"), so the dynamic "<dataset> · <barcode> - <feature>"
  // string is derived in the `suggestedBlockLabel` OUTPUT (which HAS the pool) and copied into
  // `defaultBlockLabel` by a UI watchEffect (the sanctioned block-label pattern). The subtitle only
  // reads `ctx.data`. Guard `ctx.data` — it can be undefined before block storage is parsed.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  .sections(() => [
    { type: "link" as const, href: "/" as const, label: "Main" },
    { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
    { type: "link" as const, href: "/tagstat" as const, label: "Raw tag-stat" },
  ])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
