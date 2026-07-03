import type { InferOutputsType, PColumnIdAndSpec } from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
  createPlDataTableV2,
  DataModelBuilder,
  getRelatedColumns,
  isHiddenFromGraphColumn,
  isHiddenFromUIColumn,
  isPColumnSpec,
  parseResourceMap,
} from "@platforma-sdk/model";
import type { BlockArgs, BlockData } from "./types";

export type { BlockArgs, BlockData } from "./types";

// mitool prints progress lines to stderr prefixed with this sentinel; the workflow sets it as
// MI_PROGRESS_PREFIX on the parse step (fb-pipeline.tpl.tengo) — keep the two in sync. ProgressPattern
// parses a prefix-stripped line ("<stage>: <pct>% ETA: <eta>") into parts for the UI progress cell.
export const ProgressPrefix = "[==PROGRESS==]";
export const ProgressPattern =
  /(?<stage>[^:]*):(?: *(?<progress>[0-9.]+)%)?(?: *ETA: *(?<eta>.+))?/;

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
  // Violin-plot graph tab: feature-fraction distribution per sample, faceted by feature. Defaults
  // (y = fraction, primary grouping = sample, facet = feature) are seeded by the Graph page's
  // default-options; here we only fix the chart template.
  graphState: { title: "Feature Fraction Distribution", template: "violin", currentTab: null },
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
  // True once the main workflow has begun producing outputs (ctx.outputs settles) — lets the Main page
  // swap the static "run the block" hint for the live per-sample progress grid.
  .output("started", (ctx) => ctx.outputs !== undefined)
  // Live per-sample progress for the Main-page grid. Resolves the flat per-sample parse-log stream
  // (workflow parseLogStream) and reads mitool's latest progress line per sample. Gated on
  // getInputsLocked so the full sample roster is enumerated before any row shows — every sample then
  // appears at once ("Queued" until its parse emits a line). The per-sample values are FutureRefs the
  // framework resolves on serialization; the UI reads the resolved { progressLine, live } (mirrors
  // blocks/peptide-extraction parseProgress).
  .output("sampleProgress", (ctx) => {
    const acc = ctx.outputs?.resolve("parseLogStream");
    if (!acc || !acc.getInputsLocked()) return undefined;
    return parseResourceMap(acc, (a) => a.getProgressLogWithInfo(ProgressPrefix), true);
  })
  // Per-sample × per-step progress (workflow stepLogs: 1-parse / 2-refine / 3-tagstat). Each mitool
  // step's stdout is progress-prefixed; getProgressLogWithInfo returns the latest line + live flag per
  // step. Keys flatten to [sampleId, step]; the UI picks the latest active step per sample so the grid
  // shows which stage each sample is in, not just "Processing…". (qc/metrics are Python — no progress —
  // so they are not tracked; completion comes from completedSamples.)
  .output("stepProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("stepLogs"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // sampleIds whose per-sample pipeline has finished. qcJson is the LAST per-sample step and is inline
  // JSON content, so getDataAsJsonOrUndefined reads it synchronously — the done-set is computed here
  // (unlike sampleProgress's FutureRefs) and drives the grid's "Done" state.
  .output("completedSamples", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const qcMap = parseResourceMap(
      ctx.outputs.resolve("qcJson"),
      (acc) => acc.getDataAsJsonOrUndefined<QcRow>(),
      false,
    );
    return qcMap.data.filter((e) => e.value != null).map((e) => String(e.key[0]));
  })
  // sampleId -> display name (upstream pl7.app/label), for the progress grid's Sample column. Mirrors
  // the label lookup inlined in analysisLog below; kept as its own output because each output is a pure
  // function of ctx and outputs cannot read one another.
  .output("sampleLabels", (ctx): Record<string, string> | undefined => {
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
      Object.entries(obj.obj.data.getDataAsJson<{ data: Record<string, string> }>().data).map(
        (e) => [JSON.parse(e[0])[0], e[1]],
      ),
    ) as Record<string, string>;
  })
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
  // DECISION (2026-07-02, operator): the Main table is now ONE ROW PER CELL [sampleId, cellId].
  // Supersedes the 2026-07-01 "single unified matrix table" decision — the per-(cell x feature) rows
  // moved OUT of this table into the collapsed workflow frame (consensus + the per-cell summary
  // columns: Max Feature UMI count, Max Feature Fraction, Max Specificity score, and a "Feature
  // breakdown" string listing every feature as "feature (fraction%, umi)" sorted by descending fraction). The
  // per-feature matrix is not lost: it is still exported to the result pool (perCellFeatures, the
  // A-0010 contract) for VDJ Multiomic Integration and still drives the violin graph tab (graphPf /
  // the `pf` output). This output resolves the workflow's collapsed perCellTable PFrame; undefined
  // until the workflow emits it (guarded by the UI).
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
  // panel-assigned fraction. Uses createPlDataTableV2 (columns passed directly via getPColumns) like
  // perCellTable/tagstatQcTable. The earlier V3 form used selector { mode: "enrichment", maxHops: 0 },
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
  // Violin graph tab (fix 4): the FULL per-(cell x feature) matrix (workflow graphPf output), NOT the
  // collapsed Main table — the violin plots each feature's per-cell fraction distribution, grouped by
  // sample. We build the frame from this block's columns plus axis-compatible pool columns (this is what
  // createPFrameForGraphs does under the hood — getRelatedColumns), so the sampleId pl7.app/label rides
  // along and the sample grouping shows real names, not the hash. The File-valued predicate drops
  // File-typed columns from the picker; note it does NOT prevent the upstream Samples & Data FASTQ
  // File-dataset (keyed on sampleId) from being pulled in via its linker, so pf resolves only after that
  // dataset's blob loads — a live await can transiently report no_data:<snd>:pf.dataset.* before the
  // block settles to Done. Verified: it does settle and the violin renders with sample labels.
  // outputWithStatus feeds GraphMaker its loading/error UI.
  .outputWithStatus("pf", (ctx) => {
    const pCols = ctx.outputs?.resolve("graphPf")?.getPColumns();
    if (pCols === undefined) return undefined;
    const graphColumn = (spec: (typeof pCols)[number]["spec"]) =>
      !isHiddenFromUIColumn(spec) &&
      !isHiddenFromGraphColumn(spec) &&
      (spec.valueType as string) !== "File";
    return ctx.createPFrame(getRelatedColumns(ctx, { columns: pCols, predicate: graphColumn }));
  })
  // Column id+spec list for the Graph page's default-options (find the fraction column, map its axes).
  .output("pfPcols", (ctx): PColumnIdAndSpec[] | undefined => {
    const pCols = ctx.outputs?.resolve("graphPf")?.getPColumns();
    if (pCols === undefined) return undefined;
    return pCols.map((c) => ({ columnId: c.id, spec: c.spec }) satisfies PColumnIdAndSpec);
  })
  .title(() => "Feature Integration")
  // Standard block-label subtitle. The subtitle render context is args-only (no result pool / outputs
  // — touching them renders "Invalid subtitle"), so the dynamic "<dataset> · <barcode> - <feature>"
  // string is derived in the `suggestedBlockLabel` OUTPUT (which HAS the pool) and copied into
  // `defaultBlockLabel` by a UI watchEffect (the sanctioned block-label pattern). The subtitle only
  // reads `ctx.data`. Guard `ctx.data` — it can be undefined before block storage is parsed.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // Operator feedback (2026-07-03): the block should not surface result data in its own UI — only
  // per-sample progress, like MiXCR Clonotyping (Main + a QC tab). The Raw tag-stat and Feature
  // Fraction Distribution (Graph) tabs are commented out (not deleted) so they can be re-enabled fast
  // if the team wants them back once they see it bare. Their pages (ui/src/pages/*), model outputs
  // (tagstatQcTable / pf / pfPcols), and routes (ui/src/app.ts) are all left intact. To restore a tab:
  // uncomment its line below.
  .sections(() => [
    { type: "link" as const, href: "/" as const, label: "Main" },
    { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
    // { type: "link" as const, href: "/tagstat" as const, label: "Raw tag-stat" },
    // { type: "link" as const, href: "/graph" as const, label: "Feature Fraction Distribution" },
  ])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
