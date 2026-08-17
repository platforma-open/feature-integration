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
export type { BlockArgs, BlockData, GroupingRule, ReferenceSource } from "./types";

// The reading's shipped defaults. They restate the Python's own (verdict.py DEFAULT_FLOOR,
// BOUND_CUTOFF, DEFAULT_PANEL_MIN_MEMBERS, DEFAULT_REFERENCE_THIN_LINE,
// DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE, combine.py DEFAULT_MIN_VOTERS) so the value that produced a
// run is a value the user can see and change, not an argparse default nobody chose. Every one of them
// is a declared default rather than a calibrated line: nothing published sets any of them.
const DEFAULT_COUNT_FLOOR = 4;
const DEFAULT_BOUND_CUTOFF = 75;
const DEFAULT_MIN_VOTING_CELLS = 1;
const DEFAULT_PANEL_REFERENCE_MIN_MEMBERS = 8;
const DEFAULT_REFERENCE_THIN_LINE = 2;
const DEFAULT_HIGH_REFERENCE_LINE = 100;

// Ordinal step key -> the step a sample is CURRENTLY on once that report has settled. A stepReports entry
// appears when its step finishes, so the furthest-present report implies the next running step
export type SampleStep = "parsing" | "refining" | "counting" | "metrics";
const STEP_AFTER: Record<string, SampleStep> = {
  "1-parse": "refining",
  "2-refine": "counting",
  "3-tagstat": "metrics",
};
const STEP_ORDER = ["1-parse", "2-refine", "3-tagstat"];

// mitool prefixes its progress lines with this marker (set via MI_PROGRESS_PREFIX in the workflow
// step templates); the model scrapes matching lines for a live per-sample 0–100% bar. ProgressPattern
// pulls the stage name + percent + ETA out of a marked line. Same values as blocks/peptide-extraction.
export const ProgressPrefix = "[==PROGRESS==]";
export const ProgressPattern =
  /(?<stage>[^:]*):(?: *(?<progress>[0-9.]+)%)?(?: *ETA: *(?<eta>.+))?/;

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
// rowCount (total data rows) is emitted by emit-csv-meta so the model can detect a feature barcode that
// appears on more than one row (distinct barcode values < rowCount) — the sample-specific-mapping case
// per_cell_metrics.py guards at the end of the run. Optional so a prerun output predating rowCount still
// parses (the duplicate check then simply skips).
type CsvMeta = {
  columns: string[];
  valuesByColumn: Record<string, string[]>;
  rowCount?: number;
};

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
  // Selected dataset's own sampleId keys (axis 0), from the dataset spec's axisKeys annotation.
  let datasetSampleIds: Set<string> | undefined;
  const axisKeys0 = inputSpec.annotations?.["pl7.app/axisKeys/0"];
  if (axisKeys0 !== undefined) {
    try {
      datasetSampleIds = new Set((JSON.parse(axisKeys0) as unknown[]).map(String));
    } catch {
      datasetSampleIds = undefined; // malformed → don't scope (fall back to the full map below)
    }
  }
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
  const full = Object.fromEntries(
    Object.entries(obj.obj.data.getDataAsJson<{ data: Record<string, string> }>().data).map((e) => [
      JSON.parse(e[0])[0],
      e[1],
    ]),
  ) as Record<string, string>;
  // Restrict to the selected dataset's samples (fall back to the full map if the annotation was missing).
  return datasetSampleIds
    ? Object.fromEntries(
        Object.entries(full).filter(([sampleId]) => datasetSampleIds.has(sampleId)),
      )
    : full;
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

// The tag CSV column that looks like it names the dataset's samples, or undefined. A CSV is sample-aware
// when the same barcode maps to different features per sample; the tell is a column whose distinct values
// cover the dataset's sample names. Return the column whose distinct values are a SUPERSET of the dataset
// sample names (preferring exact set-equality, then fewest extra values), excluding the columns already
// bound to the barcode / feature roles. Shared by the suggestedSampleColumn output (UI suggestion) and
// barcodeMappingIssue (names the fix in the duplicate-barcode message) — kept a module helper because a
// block output cannot read another output. undefined until both the CSV meta and sample labels resolve.
function suggestSampleColumn(ctx: BlockRenderCtx<BlockArgs, BlockData>): string | undefined {
  const meta = readCsvMeta(ctx);
  const labels = resolveSampleLabels(ctx);
  if (!meta || !labels) return undefined;
  const datasetNames = new Set(Object.values(labels));
  if (datasetNames.size === 0) return undefined;
  const excluded = new Set(
    [ctx.data.barcodeSeqColumn, ctx.data.featureNameColumn].filter(
      (c): c is string => c !== undefined,
    ),
  );
  let best: { col: string; exact: boolean; extra: number } | undefined;
  for (const col of meta.columns) {
    if (excluded.has(col)) continue;
    const values = new Set(meta.valuesByColumn?.[col] ?? []);
    let isSuperset = true;
    for (const n of datasetNames)
      if (!values.has(n)) {
        isSuperset = false;
        break;
      }
    if (!isSuperset) continue;
    const exact = values.size === datasetNames.size;
    const extra = values.size - datasetNames.size;
    // Prefer exact set-equality; among equals, prefer the fewest extra values.
    if (
      best === undefined ||
      (exact && !best.exact) ||
      (exact === best.exact && extra < best.extra)
    )
      best = { col, exact, extra };
  }
  return best?.col;
}

// v2 data shape: the preset selector + pattern string, with the dominance-era parameters still on it.
// The dominant-feature readout, the off-target designation and the specificity score they fed are gone
// from per_cell_metrics.py, so nothing consumes these three any more.
type BlockDataV2 = Omit<
  BlockData,
  | "datasetRef"
  | "roleColumn"
  | "referenceValues"
  | "referenceSource"
  | "panelReferenceMinMembers"
  | "referenceThinLine"
  | "countFloor"
  | "boundCutoff"
  | "minVotingCells"
  | "minAgreement"
  | "gateThreshold"
  | "highReferenceLine"
  | "grouping"
  | "contendingGroups"
  | "verdictTableState"
> & {
  dominanceThreshold: number;
  offtargetProperty?: string;
  offtargetValues?: string[];
};

// v1 (pre-preset) data shape: read geometry was three explicit length fields. v2 replaces them with a
// preset selector + a mitool tag-pattern string (see model/src/pattern.ts, model/src/presets).
type BlockDataV1 = Omit<BlockDataV2, "presetId" | "pattern"> & {
  cellLen: number;
  umiLen: number;
  featureLen: number;
};

const dataModel = new DataModelBuilder()
  .from<BlockDataV1>("v1")
  .migrate<BlockDataV2>("v2", ({ cellLen, umiLen, featureLen, ...rest }) => {
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
  // v2 -> v3: the dominance parameters go and the reading's own arrive. The three dropped fields are
  // dropped rather than carried: the rule they parameterised no longer exists, and a field kept "just in
  // case" would still travel in the args hash and stale the block on an edit that changes no computation.
  // The new numeric parameters are seeded with the shipped defaults so a migrated project renders the
  // same run a fresh one would — a parameter left undefined here would reach the CLI as its argparse
  // default, which is the same number arrived at without anyone choosing it.
  .migrate<BlockData>(
    "v3",
    ({ dominanceThreshold: _d, offtargetProperty: _p, offtargetValues: _v, ...rest }) => ({
      ...rest,
      countFloor: DEFAULT_COUNT_FLOOR,
      boundCutoff: DEFAULT_BOUND_CUTOFF,
      minVotingCells: DEFAULT_MIN_VOTING_CELLS,
      panelReferenceMinMembers: DEFAULT_PANEL_REFERENCE_MIN_MEMBERS,
      referenceThinLine: DEFAULT_REFERENCE_THIN_LINE,
      highReferenceLine: DEFAULT_HIGH_REFERENCE_LINE,
      verdictTableState: createPlDataTableStateV2(),
    }),
  )
  .init(() => ({
    runMode: "full" as const, // full run by default; "dry" = read-limited Preview
    // Default preset = the geometry the block shipped with: 10x 5' v2 BEAM (16 / 10 / 15).
    presetId: "tenx-beam",
    cellWhitelist: "", // de-novo CELL correction by default
    defaultBlockLabel: "",
    // The reading's parameters. minAgreement and gateThreshold are deliberately absent: both are off by
    // default, and off means absent rather than zero (see the args projection).
    countFloor: DEFAULT_COUNT_FLOOR,
    boundCutoff: DEFAULT_BOUND_CUTOFF,
    minVotingCells: DEFAULT_MIN_VOTING_CELLS,
    panelReferenceMinMembers: DEFAULT_PANEL_REFERENCE_MIN_MEMBERS,
    referenceThinLine: DEFAULT_REFERENCE_THIN_LINE,
    highReferenceLine: DEFAULT_HIGH_REFERENCE_LINE,
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
    verdictTableState: createPlDataTableStateV2(),
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
    // Optional combine-mode column: its values are per-feature modes ("sum"/"all"), so it must be its
    // OWN CSV column — distinct from the barcode-sequence and feature-name roles, and not a reserved
    // tag-stat column. Python guards this too, but only after the mitool chain runs; reject up front so a
    // mis-picked column (e.g. the barcode column, whose values are DNA sequences) disables Run with a
    // clear message instead of failing the pipeline at the end.
    if (data.combineColumn) {
      if (
        data.combineColumn === data.barcodeSeqColumn ||
        data.combineColumn === data.featureNameColumn
      )
        throw new Error(
          "Combine-mode column must be a separate CSV column from the barcode-sequence and feature-name columns",
        );
      if (RESERVED_TAGSTAT_COLUMNS.has(data.combineColumn))
        throw new Error(
          `Combine-mode column "${data.combineColumn}" collides with a reserved tag-stat column; pick another`,
        );
    }
    // Preview (dry-run) needs a read limit. Same up-front gate as mixcr-clonotyping: disable Run with a
    // clear message rather than start a run with no reads to cap.
    if (data.runMode === "dry" && (data.limitInput == null || data.limitInput < 1))
      throw new Error("Enter a read limit (≥ 1) for Preview mode, or switch to a full run");
    // Read geometry: resolve the selected preset to its effective pattern (fixed preset owns it; the
    // generic preset carries it in data.pattern), validate it loosely (the required CELL/UMI/FEATURE
    // tags + R2 capture must be present — the workflow's refine-tags/tag-stat reference them by name;
    // anything else is passed to mitool verbatim), then hand the string to the workflow directly.
    const preset = getPreset(data.presetId);
    if (!preset) throw new Error("Select a read-geometry preset");
    const pattern = preset.userConfigurable ? (data.pattern ?? "") : preset.pattern;
    const patternError = validatePattern(pattern);
    if (patternError) throw new Error(patternError);

    // Sample-aware mapping (optional): when a sample column is chosen, the per-sample workflow body
    // filters the CSV to its own sample's rows, so pass the column name + the sampleId→name snapshot it
    // needs to translate its iteration key. The snapshot is taken on the same gesture that sets the
    // column (MainPage.setSampleColumn); require it here so a stale/half-set state disables Run.
    const sampleAware = !!data.sampleColumn;
    if (sampleAware) {
      if (!data.sampleLabelSnapshot || Object.keys(data.sampleLabelSnapshot).length === 0)
        throw new Error("Re-select the sample column (sample labels not captured)");
      // Block Run when a dataset sample has no rows in the CSV's sample column — it would silently get
      // no features. Gate purely from the snapshots taken when the column was picked (args is data-only).
      const csvValues = new Set(data.sampleColumnValues ?? []);
      const missing = Object.values(data.sampleLabelSnapshot).filter((n) => !csvValues.has(n));
      if (missing.length > 0)
        throw new Error(
          `${missing.length} dataset sample(s) have no rows in the tag CSV's "${data.sampleColumn}" column ` +
            `(${missing.slice(0, 5).join(", ")}${missing.length > 5 ? "…" : ""}). ` +
            `Add rows for them, or clear the sample column to use one mapping for all samples.`,
        );
    }

    // The reading's own parameters. The single-cell V(D)J dataset is deliberately NOT required: without
    // it the block still emits the tag counts, the per-cell scalars, the panel-versus-reads check and the
    // per-sample QC, none of which need a clonotype set. A missing input narrows what can be answered
    // and nothing more.
    if (data.countFloor < 0) throw new Error("The count floor cannot be negative");
    if (data.boundCutoff < 0 || data.boundCutoff > 100)
      throw new Error("The bound cutoff is a score between 0 and 100");
    if (data.minVotingCells < 1) throw new Error("At least one cell must vote");
    // "declared" reads counts against a tag the panel marks as the comparator, and nothing marks one
    // without the role values. Asking for it anyway would degrade to no comparator inside the run, where
    // the choice is recorded but the user never sees they lost it.
    if (data.referenceSource === "declared" && !data.referenceValues?.length)
      throw new Error("Choose which role values mark the reference, or pick another source");

    // Contending groups, canonicalised here rather than in the editor: the args value is a cache key, so
    // the same declaration written in a different order must produce the same string or the block goes
    // stale and re-runs the whole reading for nothing. A group of fewer than two members is dropped —
    // one identity contends with nothing, and an empty group is that same case.
    const contendingGroups = (data.contendingGroups ?? [])
      .map((group) => [...new Set(group)].sort())
      .filter((group) => group.length > 1)
      .sort((a, b) => a.join(" ").localeCompare(b.join(" ")));

    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvHandle: data.tagFeatureCsvHandle,
      barcodeSeqColumn: data.barcodeSeqColumn,
      featureNameColumn: data.featureNameColumn,
      controlFeature: data.controlFeature,
      // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
      // feature's mode (sum = OR, the default; all = AND, feature called only when every member barcode
      // fires). Projected only when set so the workflow default (every feature OR) is untouched otherwise.
      // minUmi is the AND per-barcode "fired" floor (integer >= 1; default 1 in the workflow/Python),
      // projected only alongside combineColumn — the workflow passes --min-umi only with --combine-col,
      // so without a combine mode it would only stale the block with no computational effect.
      ...(data.combineColumn ? { combineColumn: data.combineColumn } : {}),
      ...(data.combineColumn && typeof data.minUmi === "number" && data.minUmi >= 1
        ? { minUmi: Math.round(data.minUmi) }
        : {}),
      // --- the binding reading ---
      // The dataset anchor. Absent is a legitimate state, not a half-filled form, so it projects as
      // absent and the workflow skips the verdict stage alone.
      datasetRef: data.datasetRef,
      // Empty and absent are the same claim for both of these, so an empty selection projects as absent
      // rather than as "" / [] — two spellings of one request would otherwise be two cache keys.
      roleColumn: data.roleColumn || undefined,
      // Sorted + de-duplicated: the Python reads these as a set, so re-picking the same values in a
      // different order must not re-run the reading.
      referenceValues: data.referenceValues?.length
        ? [...new Set(data.referenceValues)].sort()
        : undefined,
      referenceSource: data.referenceSource,
      panelReferenceMinMembers: Math.round(data.panelReferenceMinMembers),
      referenceThinLine: Math.round(data.referenceThinLine),
      countFloor: Math.round(data.countFloor),
      boundCutoff: data.boundCutoff,
      minVotingCells: Math.round(data.minVotingCells),
      // Off by default, and off means ABSENT: a minimum agreement of 0 passes every majority instead of
      // skipping the check, and a gate of 0 sets aside every cell instead of gating none. Both are
      // different claims from "off", so neither is projected as zero.
      minAgreement: data.minAgreement,
      gateThreshold:
        typeof data.gateThreshold === "number" && data.gateThreshold > 0
          ? Math.round(data.gateThreshold)
          : undefined,
      highReferenceLine: Math.round(data.highReferenceLine),
      // A rule over declared panel properties, never a tag→identity map. Absent means one identity per
      // tag, which is the reading's own default, so no hand-built { by: "tag" } is sent in its place.
      grouping: data.grouping,
      contendingGroups: contendingGroups.length > 0 ? contendingGroups : undefined,
      // Preview: cap reads only in dry mode; a full run omits it (all reads). Projected only when dry, so
      // toggling back to full changes the args hash and re-runs on the complete input.
      ...(data.runMode === "dry" && data.limitInput
        ? { limitInput: Math.round(data.limitInput) }
        : {}),
      pattern,
      tags: { cell: CELL_TAG, umi: UMI_TAG, feature: FEATURE_TAG },
      ...(sampleAware
        ? { sampleColumn: data.sampleColumn, sampleLabels: data.sampleLabelSnapshot }
        : {}),
      // CELL whitelist: "" = de-novo CELL correction (default; no external whitelist).
      cellWhitelist: data.cellWhitelist ?? "",
      // Optional mitool resource overrides (Advanced Settings). Project only positive integers so a blank
      // or zero field falls through to the workflow defaults (4 CPUs; formula-sized RAM) instead of
      // sending a meaningless request or staling the block on an empty edit.
      ...(typeof data.perProcessCPUs === "number" && data.perProcessCPUs >= 1
        ? { perProcessCPUs: Math.round(data.perProcessCPUs) }
        : {}),
      ...(typeof data.perProcessMemGB === "number" && data.perProcessMemGB >= 1
        ? { perProcessMemGB: Math.round(data.perProcessMemGB) }
        : {}),
    };
  })
  // Staging depends only on the CSV: emit-csv-meta emits every column's values in one exec, so the
  // negative-control dropdown no longer needs a rerun when the feature column changes (the model indexes
  // the already-emitted map). featureNameColumn is deliberately NOT a prerun arg — and neither is
  // fbFastqRef: the CSV metadata is independent of the FASTQ, so keying staging on it would re-run the
  // emit-csv-meta step and blank the column dropdowns (csvColumnsLoading → tagMappingDisabled) every time
  // the FASTQ changes or a PlRef re-resolves on reload. Key on the CSV alone.
  .prerunArgs((data) => ({
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
  // The single-cell V(D)J dataset the verdicts are keyed by: columns on [sampleId, scClonotypeKey]
  // flagged as anchors — the same query VDJ Multiomic Integration uses, so the two blocks offer the user
  // the same list. There is deliberately no linkerOptions beside it: the cell linker carries
  // pl7.app/isLinkerColumn and is hidden in tables, so it is not a column a user can pick, and the
  // workflow resolves it from this anchor by name.
  .output("datasetOptions", (ctx) =>
    ctx.resultPool.getOptions([
      {
        axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/scClonotypeKey" }],
        annotations: { "pl7.app/isAnchor": "true" },
      },
    ]),
  )
  // The identities the contending-groups editor picks from, live from the uploaded panel. An identity is
  // whatever the grouping rule groups tags by: the tag itself under the default per-tag rule, and the
  // property's value under a property rule — so the option list is the distinct values of the barcode
  // column or of the chosen property column. Under the per-tag rule the ids ARE the barcode sequences,
  // and they are their own labels: the panel metadata is column-wise (each column's distinct values), so
  // it carries no tag→name pairing to name them by. Retentive so the editor does not blank on a rerun.
  //
  // This output exists so that only the USER'S PICKS are ever written to data. A watcher copying this
  // list into data would make the output depend on data derived from it, and two open clients would race
  // to write it.
  .retentiveOutput("identityOptions", (ctx): { value: string; label: string }[] => {
    const grouping = ctx.data.grouping;
    const column = grouping?.by === "property" ? grouping.column : ctx.data.barcodeSeqColumn;
    if (!column) return [];
    return (readCsvMeta(ctx)?.valuesByColumn?.[column] ?? []).map((v) => ({ value: v, label: v }));
  })
  // Suggested block label for the sidebar subtitle: "<dataset> / <barcode> - <feature>", derived from
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
    if (parts.length === 0) return undefined;
    // The default subtitle must never render with dots (Stan's request, S1). Periods come from a dotted
    // dataset/file label; the " / " and " - " separators are a slash and hyphen, not periods, so
    // stripping "." leaves them intact. Replace periods with spaces and collapse the doubles they create.
    // A subtitle the user types in the sidebar is not routed through this output, so overrides are safe.
    return parts.join(" / ").replace(/\./g, " ").replace(/ {2,}/g, " ").trim();
  })
  // Negative-control dropdown options: the distinct values of the chosen feature-name
  // column, from the prerun's emit-csv-meta valuesByColumn map. No rerun on column change — the map
  // already carries every column's values, so picking the feature column just re-indexes here.
  // Retentive avoids a flicker to [] on rerun; empty until the CSV is uploaded and staging completes.
  .retentiveOutput("controlOptions", (ctx): { value: string; label: string }[] => {
    const col = ctx.data.featureNameColumn;
    const names = col ? (readCsvMeta(ctx)?.valuesByColumn?.[col] ?? []) : [];
    return names.map((name) => ({ value: name, label: name }));
  })
  // CSV column headers (from the prerun emit-csv-meta step) → the barcode/feature column dropdowns
  // Retentive so the dropdowns don't blank on rerun; empty until the CSV is uploaded + parsed.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] =>
    (readCsvMeta(ctx)?.columns ?? []).map((c) => ({ value: c, label: c })),
  )
  // Every CSV column's distinct values (from the prerun emit-csv-meta step). The UI reads this when the
  // sample column is picked, to snapshot that column's values into data (args gates Run on them).
  .retentiveOutput(
    "csvValuesByColumn",
    (ctx): Record<string, string[]> => readCsvMeta(ctx)?.valuesByColumn ?? {},
  )
  // Sample-aware mapping sanity check (UI warning only; args is the authoritative gate). When a sample
  // column is chosen, compare its CSV values against the dataset's sample names: flag dataset samples
  // absent from the CSV (they would get no features) and CSV values matching no dataset sample (typos).
  .retentiveOutput("sampleMappingWarning", (ctx): string[] | undefined => {
    const col = ctx.data.sampleColumn;
    if (!col) return undefined;
    const meta = readCsvMeta(ctx);
    const labels = resolveSampleLabels(ctx);
    if (!meta || !labels) return undefined; // CSV or labels not resolved yet
    const csvSamples = new Set(meta.valuesByColumn?.[col] ?? []);
    const datasetNames = new Set(Object.values(labels));
    const fmt = (xs: string[]) => `${xs.slice(0, 5).join(", ")}${xs.length > 5 ? "…" : ""}`;
    const missing = [...datasetNames].filter((n) => !csvSamples.has(n));
    const extra = [...csvSamples].filter((s) => !datasetNames.has(s));
    // One line per issue (the UI renders each on its own line). Missing samples block Run (args throws);
    // extra CSV values are only informational (those rows are simply never used).
    const lines: string[] = [];
    if (missing.length > 0)
      lines.push(
        `${missing.length} dataset sample(s) have no rows in the CSV — Run is blocked until every sample is mapped (or the sample column is cleared): ${fmt(missing)}.`,
      );
    if (extra.length > 0)
      lines.push(
        `${extra.length} CSV sample value(s) match no dataset sample (ignored): ${fmt(extra)}.`,
      );
    return lines.length > 0 ? lines : undefined;
  })
  // The tag CSV column that looks like it names the dataset's samples (or undefined). The UI offers it as
  // a one-click "use sample-aware mapping" suggestion. Purely advisory — the user must still pick it (a
  // gesture that snapshots the sample map into data); this output never writes data. Excludes the columns
  // already bound to the barcode / feature roles. See suggestSampleColumn for the superset/equality rule.
  .retentiveOutput("suggestedSampleColumn", (ctx): string | undefined => suggestSampleColumn(ctx))
  // Duplicate-barcode detection at config time (UI warning only; the Python guards it authoritatively at
  // the end of the run). Fires when a CSV is uploaded, the barcode column is chosen, no sample column is
  // set, and that barcode column has fewer distinct values than the CSV has data rows — i.e. some barcode
  // maps on more than one row, which would fan the per-cell join and double molecule counts. Names the
  // fix (set the Sample column, suggesting the likely one; else remove the duplicate rows). Skipped when
  // rowCount is absent (prerun predates it) — then the check can't run and we defer to the Python guard.
  .retentiveOutput("barcodeMappingIssue", (ctx): string | undefined => {
    if (!ctx.data.tagFeatureCsvHandle) return undefined;
    const barcodeCol = ctx.data.barcodeSeqColumn;
    if (!barcodeCol) return undefined;
    if (ctx.data.sampleColumn) return undefined; // already sample-aware — the per-sample filter fixes it
    const meta = readCsvMeta(ctx);
    if (!meta || meta.rowCount === undefined) return undefined;
    const distinct = meta.valuesByColumn?.[barcodeCol]?.length ?? 0;
    if (distinct >= meta.rowCount) return undefined; // no duplicate barcodes
    const suggested = suggestSampleColumn(ctx);
    return (
      "Some feature barcodes appear on multiple rows, so a single mapping is ambiguous. " +
      `If this CSV is sample-specific, set the Sample column${suggested ? ` (looks like "${suggested}")` : ""}; ` +
      "otherwise remove the duplicate rows."
    );
  })
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
  // before Run. The CSV-derived dropdowns (csvColumnOptions / controlOptions) are populated by the prerun
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
  // Per-sample current step, derived from which stepReports entries have settled (a report appears when
  // its step finishes).
  .output("sampleStep", (ctx): Record<string, SampleStep> | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const reports = parseResourceMap(
      ctx.outputs.resolve("stepReports"),
      (acc) => acc.getFileHandle(),
      false,
    );
    if (!reports) return {};
    // Per sample, the furthest step whose report is present.
    const furthest: Record<string, string> = {};
    for (const e of reports.data) {
      if (e.value == null) continue;
      const sampleId = String(e.key[0]);
      const step = String(e.key[1]);
      if (
        furthest[sampleId] === undefined ||
        STEP_ORDER.indexOf(step) > STEP_ORDER.indexOf(furthest[sampleId])
      )
        furthest[sampleId] = step;
    }
    const out: Record<string, SampleStep> = {};
    for (const [sampleId, step] of Object.entries(furthest)) out[sampleId] = STEP_AFTER[step];
    return out;
  })
  // Per-[sampleId, step] live log handles (parse / refine / tag-stat stdout streams), bound by the
  // per-sample Logs tab (PlLogView) so the user can read each mitool step's output as it runs. A no-match
  // sample carries only its 1-parse entry (the map key set is variable — see fb-refine-tagstat).
  .output("stepLogs", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(ctx.outputs.resolve("stepLogs"), (acc) => acc.getLogHandle(), false)
      : undefined,
  )
  // Per-[sampleId] log handle for the Python per-cell-metrics step (the "4-metrics" step). Surfaced
  // separately from stepLogs because it's produced after the mitool stepLogs map is built; the UI's
  // per-step Logs panel reads it when the "4-metrics" step is selected.
  .output("metricsLog", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("metricsLogStream"),
          (acc) => acc.getLogHandle(),
          false,
        )
      : undefined,
  )
  // Live per-sample parse progress (0–100%) — reads the flat parseLogStream Log, registered the moment
  // the per-sample body runs (before parse finishes). Kept mainly as an EARLY roster signal (it appears
  // before the stepLogs map fills); the per-step bar detail comes from stepProgress below.
  .output("parseProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("parseLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // Per-[sampleId, step] live progress line (parse / refine / tag-stat), scraped from each step's stdout
  // stream. Drives the rich per-step text in the grid Progress cell (which tag is being corrected, sort
  // vs write phase, live %). ui/src/progress.ts composes these into a MONOTONIC cumulative bar (each step
  // owns a quarter of the bar) so it never resets to zero between steps. Same source as stepLogs.
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
  //   - while the run is in progress → a live count of samples finished so far ("Processing… N …");
  //   - when every sample is done   → a run-level summary (aggregate reads/panel-assigned/cells +
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
    const lines: string[] = ["Feature Barcode Profiling — analysis log", ""];
    lines.push(`Processed ${done} sample${done === 1 ? "" : "s"}.`);
    lines.push(
      `Reads parsed: ${nf(readsTotal)} total` +
        (medMatched !== undefined ? `, ${pct(medMatched)} matched the read pattern (median)` : "") +
        ".",
    );
    if (medAssigned !== undefined && assigned.length > 0) {
      lines.push(
        `Panel-assigned: ${pct(medAssigned)} of reads (median; range ${pct(Math.min(...assigned))}–${pct(Math.max(...assigned))}).`,
      );
    }
    lines.push(`Cells detected: ${nf(cellsTotal)} across ${features} features.`);
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
  // The Main table is ONE ROW PER CELL [sampleId, cellId]. The per-(cell x feature) rows
  // moved OUT of this table into the collapsed workflow frame (consensus + the per-cell summary
  // columns: Max Feature UMI count, Max Feature Fraction, Max Specificity score, and a "Feature
  // breakdown" string listing every feature as "feature (fraction%, umi)" sorted by descending fraction). The
  // per-feature matrix is not lost: it is still exported to the result pool (perCellFeatures, the
  // per-cell export contract) for VDJ Multiomic Integration. This output resolves the workflow's collapsed
  // perCellTable PFrame; undefined until the workflow emits it (guarded by the UI).
  //
  // Uses createPlDataTableV2 (columns passed directly via getPColumns), NOT V3. This frame is our OWN
  // self-contained, non-batch processColumn output. createPlDataTableV3's discovery cannot render it:
  // the object (scoped-sources) form returns undefined for this frame regardless of anchor/maxHops
  // config, and the array-columns form runs discoverLabelColumnVariants over the
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
  .title(() => "Feature Barcode Profiling")
  // Standard block-label subtitle. The subtitle render context is args-only (no result pool / outputs
  // — touching them renders "Invalid subtitle"), so the dynamic "<dataset> / <barcode> - <feature>"
  // string is derived in the `suggestedBlockLabel` OUTPUT (which HAS the pool) and copied into
  // `defaultBlockLabel` by a UI watchEffect (the sanctioned block-label pattern). The subtitle only
  // reads `ctx.data`. Guard `ctx.data` — it can be undefined before block storage is parsed.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // Main (the per-sample progress grid) is always shown. The result tabs — Per-sample QC and the
  // per-cell results table — appear only once the block has produced outputs, so a fresh/unrun block
  // shows only Main. ctx.outputs settles when the workflow starts emitting (the same signal as the
  // `started` output). The Graph and Raw tag-stat views were removed.
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
