import type {
  AxisId,
  BlockRenderCtx,
  InferOutputsType,
  PlDataTableStateV2,
} from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPFrameForGraphs,
  createPlDataTableStateV2,
  createPlDataTableV2,
  createPlDataTableV3,
  DataColumn,
  DataModelBuilder,
  getAxisId,
  isPColumnSpec,
  parseResourceMap,
} from "@platforma-sdk/model";
import { assemblePattern, CELL_TAG, FEATURE_TAG, UMI_TAG, validatePattern } from "./pattern";
import { getPreset } from "./presets";
import type { BlockArgs, BlockData, CsvMeta, GroupingRule, ReferenceSource } from "./types";

export { assemblePattern, parsePattern, validatePattern } from "./pattern";
export type { PatternParts } from "./pattern";
export { allPresets, getPreset } from "./presets";
export type { Preset } from "./presets";
export type { BlockArgs, BlockData, CsvMeta, GroupingRule, ReferenceSource } from "./types";

// ui-vue does not re-export this factory, and the ui package depends on ui-vue alone.
export { createPlDataTableStateV2 } from "@platforma-sdk/model";
export type { PTableKey } from "@platforma-sdk/model";

// The reading's own defaults, in one exported map. Exported so a test can compare it against the other
// two copies: this map is what a workflow-driven run is actually answered under, because
// verdict-args.lib.tengo emits these flags UNCONDITIONALLY, substituting its own copy wherever the
// stored value is undefined. The argparse defaults in the Python never govern such a run.
// `test/src/qcDefaults.test.ts` asserts each value against verdict-args.lib.tengo and the Python module
// that owns it, the same way it asserts the QC lines below.
export const VERDICT_DEFAULTS = {
  // verdict.py DEFAULT_FLOOR
  countFloor: 4,
  // verdict.py BOUND_CUTOFF
  boundCutoff: 75,
  // verdict.py DISTRIBUTION_BOUND_PROBABILITY. Both the default and the FLOOR: `args()` refuses below it.
  boundProbability: 0.9,
  // combine.py DEFAULT_MIN_VOTERS
  minVotingCells: 1,
  // verdict.py DEFAULT_PANEL_MIN_MEMBERS. Gates rather than tunes: keep above the fifteen-tag cap of an
  // antibody kit, so a panel that small uses the tag-distribution rung.
  panelReferenceMinMembers: 25,
  // tag_distribution.py DEFAULT_DISTRIBUTION_MIN_CELLS
  distributionMinCells: 300,
} as const;

// The two maps below are DISPLAY ONLY. They show the number already in force where the stored value is
// undefined. args() never projects them, so a field that shows 0.75 and a field that holds 0.75 produce the
// same command line and the same args hash. An undefined line reaches the CLI as the shipped default that
// verdict-args.lib.tengo substitutes through `_num`. An undefined knob reaches the argparse default in
// qc_report.py.
//
// Each value MUST equal its counterpart in verdict-args.lib.tengo (the lines) and qc_measures.py (the knobs).
// `test/src/qcDefaults.test.ts` asserts both sets against those files.
export const QC_LINE_DEFAULTS = {
  cellBarcodeValidWarn: 0.75,
  cellBarcodeValidError: 0.5,
  readsPerCellWarn: 5000,
  aggregateBarcodeWarn: 0.05,
  aggregateBarcodeError: 1.0,
  undeclaredBarcodeWarn: 0.01,
  undeclaredBarcodeError: 0.05,
  usableReadWarn: 0.2,
  usableReadError: 0.0,
} as const;

export const AGGREGATE_DETECTION_DEFAULTS = {
  aggregateBarcodeIqrMultiplier: 3.0,
  aggregateBarcodeMinUmiThreshold: 1000.0,
  aggregateBarcodeTopN: 100,
} as const;

// The punchcard's frame is keyed on the clonotype set alone, so each identity is a COLUMN and not an axis
// value. The identity travels in the column's DOMAIN. One column per identity, its value carrying the state
// and both support counts together, because a grid pairs one column's cell with another's by position alone.
//
// Identify a punch column by its NAME plus the domain key, read off the spec the grid returns on
// `colDef.context`. Never by its column id: `substituteSpecialCharacters` mangles an id, and a substring
// test lets `SpikeWT` match `SpikeWT_alt`.
export const PUNCH_COLUMN_NAME = "pl7.app/antigen/identityPunch";
export const PUNCH_IDENTITY_DOMAIN = "pl7.app/antigen/identityId";
// Carries the same identity domain key, so one column-matching helper serves both cards.
export const CELL_PUNCH_COLUMN_NAME = "pl7.app/antigen/cellPunch";
// A block's own exports are not in its own result pool, so the copy in the exported setCounts family is
// unreachable here.
export const PUNCH_CELL_COUNT_COLUMN = "pl7.app/antigen/cellCount";

// User-facing names only. The DATA layer keeps `declared`/`panel`/`none`, which are p-column domain values,
// and domain is part of column identity. These strings match the labels `referenceSources` offers.
export const REFERENCE_SOURCE_LABELS: Record<ReferenceSource, string> = {
  declared: "Declared baseline tag",
  panel: "The panel's own readings",
  distribution: "Each tag's own distribution",
};

// result_run_meta.json, as emit_verdicts.py writes it. Only the fields the UI states back are typed here.
export type VerdictRunMeta = {
  /** The comparator that SERVED. A request the panel cannot honour degrades to none. */
  referenceChoice: ReferenceSource;
  /**
   * Whether the run established a baseline, and where it did not, why.
   *
   * Only the tag-distribution rung can reach false: its conditions are properties of the DATA. The settings
   * refuse the other rungs before the run reads anything. False means the run finished and read no verdicts,
   * and the block shows the reason in place of the punchcard.
   */
  baselineEstablished: boolean;
  noBaselineReason: string | null;
  /** The comparator that was ASKED for. A degraded run can then state what it lost. */
  referenceSourceRequested: ReferenceSource;
  referenceTags: string[];
  identityCount: number;
  setCount: number;
  cellsAnalysed: number;
  /**
   * The two export gates and each gate's own limit. The identity limit bounds the pivot's WIDTH, at one
   * p-column per identity. The cell limit bounds its ROWS. Optional: a run record written before these fields
   * existed does not carry them.
   */
  identitySummaryEmitted?: boolean;
  identitySummaryLimit?: number;
  cellPunchEmitted?: boolean;
  cellPunchCells?: number;
  cellPunchLimit?: number;
  /** Tags the grouping column said nothing about. Each stands as its own identity, under a bare barcode. */
  tagsWithoutGroupingValue: string[];
  /**
   * How many DISTINCT panels the run carried. One means every sample carried the same tags. Optional: a
   * reader treats absent as one.
   */
  samplePanelCount?: number;
  /**
   * The read limit the run applied. Absent or null where the run declared none, which is the one signal for
   * "a gate was declared". A gate that set nothing aside must still say so.
   */
  gateThreshold?: number | null;
  /**
   * How many of each clonotype's cells the gate set aside, keyed by set id. SPARSE: an absent key reads as
   * zero. Absent where the run declared no gate. Set grain, and not a column of the expansion table: a
   * per-identity subtraction would imply a per-identity failure that did not happen.
   */
  cellsSetAsideBySet?: Record<string, number>;
  /**
   * Which cell list every figure was computed against. Two runs whose lists came from different sources do
   * not share a denominator. Optional: a rename in `emit_verdicts.py` without a rename here leaves this field
   * undefined rather than failing.
   */
  cellListSource?: CellListSource;
};

/** Mirrors `emit_verdicts.py`'s `cell_list_source`. */
export type CellListSource = "cell list" | "clonotype linker" | "none";

// Null covers both "no line stands behind the measurement" and "nothing computed one". The value tells the
// two apart: a number means computed and unjudged.
export type QcMeasurementStatus = "OK" | "warn" | "alert";

// As emit_verdicts.py writes it into result_qc_by_sample.json.
export type SampleQcMeasurement = {
  /** Stable: it is also a value on the `measurement` axis and a p-column name. */
  id: string;
  label: string;
  /** Null where the run could not compute one. `reason` then says why, and is never empty. */
  value: number | null;
  detail: string | null;
  /** Non-null exactly when `value` is null. */
  reason: string | null;
  /** Null where no line stands behind the measurement, or where there is no value to judge. */
  status: QcMeasurementStatus | null;
  counts: string;
  implies: string | null;
  /**
   * Whether this measurement's status reaches the sample's rollup. False for a measurement whose finding
   * belongs to a reagent and not to the sample it was measured on.
   */
  rollsUp: boolean;
};

export type SampleQcReport = {
  /** The worst status among the measurements that carry one. Null where none did. */
  status: QcMeasurementStatus | null;
  judged: number;
  /** How many were computed with no line to judge them against. */
  unjudged: number;
  /** How many the run could not compute at all. */
  notEvaluated: number;
  /** Declaration order, including the measurements nothing computed. */
  measurements: SampleQcMeasurement[];
};

/**
 * Binned count distributions, per sample and tag, and the one edge list they share.
 *
 * Drawn from the RAW counts: before the minimum, and with the reference tag kept. A plot read in order to
 * SET the minimum cannot have the minimum already applied to it.
 *
 * Counted over the CELL LIST, not over every observed barcode. A run that supplied no cell list counts
 * barcodes instead, and `verdictRunMeta.cellListSource` says which.
 *
 * `edges` holds `weights.length + 1` log-spaced boundaries, shared across the run. A tag with no reading in
 * a sample carries NO entry rather than a list of zeros.
 */
export type TagCountBins = {
  edges: number[];
  bySample: Record<string, Record<string, number[]>>;
  /**
   * Tag -> the name a reader knows the reagent by. A tag with no entry renders as its own barcode sequence.
   * Here rather than through the tag axis's label column, because these plots are drawn from this JSON and a
   * label column reaches p-frame surfaces only. Absent in full for a run that finished before this key.
   */
  tagLabels?: Record<string, string>;
  /**
   * The barcodes in the order the PANEL declares them, deduplicated on first appearance. A per-sample view
   * reads in it, so that a barcode holds the same slot in every sample.
   */
  tagOrder?: string[];
  /**
   * The fit's two means and the background's share of cells, at the same (sample, tag) grain as the bins.
   * Here rather than in the p-frame beside them, so a grid of panels costs no driver query per panel.
   *
   * Absent for a run read against a declared baseline tag, which fits nothing, and for a (sample, tag) the fit
   * could not score.
   */
  fitsBySample: Record<
    string,
    Record<
      string,
      {
        backgroundMean: number;
        signalMean: number;
        backgroundWeight: number;
        boundAtCount?: number | null;
      }
    >
  >;
  /**
   * The run's own two spreads, each on its own LINEAR edges: `score` and `referenceReading`. Linear, unlike
   * the count bins, because a score is a 0-100 scale and a reference reading is read against a gate typed in
   * the same units.
   *
   * One distribution for the whole run, never per sample, because the cutoff and the gate are each one number
   * for the run. A key is absent where the served rung produces no such quantity.
   */
  spreads: Record<string, { edges: number[]; weights: number[] }>;
};

// The rungs the dropdown offers. Nothing below the model derives one: `--reference-source` is required
// and verdict.py deliberately carries no function that could pick a rung, which a test pins.
export type ReferenceSourceChoices = {
  /** EVERY rung, always, in ladder order, and every rung selectable. */
  options: {
    value: ReferenceSource;
    label: string;
    description: string;
    /** What this rung still needs before it can serve, as a sentence. Undefined once it can. */
    needs?: string;
  }[];
  /** What a run with nothing chosen is answered under. Constant, because nothing derives it. */
  fallback: string;
};

// A stepReports entry appears when its step finishes, so the furthest present report implies the next
// running step.
export type SampleStep = "parsing" | "refining" | "counting" | "metrics";
const STEP_AFTER: Record<string, SampleStep> = {
  "1-parse": "refining",
  "2-refine": "counting",
  "3-tagstat": "metrics",
};
const STEP_ORDER = ["1-parse", "2-refine", "3-tagstat"];

// mitool prefixes its progress lines with this marker. The workflow step templates set it through
// MI_PROGRESS_PREFIX. Same values as blocks/peptide-extraction.
export const ProgressPrefix = "[==PROGRESS==]";
export const ProgressPattern =
  /(?<stage>[^:]*):(?: *(?<progress>[0-9.]+)%)?(?: *ETA: *(?<eta>.+))?/;

// As qc_report.py emits them (result_qc.json).
export type QcRow = {
  readsTotal: number;
  readsMatched: number;
  matchedFraction: number;
  cellsDetected: number;
  featuresDetected: number;
  totalUniqueUmis: number;
  medianUmisPerCell: number;
  // "" where the run wrote no refine report.
  panelAssignedFraction: number | "";
  // The same blank rule. From the refine report's CELL step.
  cellBarcodeValidFraction: number | "";
};

// mitool tag-stat emits these columns. A CSV barcode or feature column that names one corrupts the join, or
// stops group_by in per_cell_metrics.py. That script guards it too, but only after the full mitool chain has
// run, so args() rejects it here and the app disables Run up front.
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

// From the upstream pl7.app/label column whose axis matches the input FASTQ's sample axis. A module helper,
// because each block output is an independent pure function of ctx and cannot read another output.
function resolveSampleLabels(
  ctx: BlockRenderCtx<BlockArgs, BlockData>,
): Record<string, string> | undefined {
  const inputRef = ctx.data.fbFastqRef;
  if (inputRef === undefined) return undefined;
  const inputSpec = ctx.resultPool.getSpecByRef(inputRef);
  if (inputSpec === undefined || !isPColumnSpec(inputSpec)) return undefined;
  const sampleAxisSpec = inputSpec.axesSpec[0];
  let datasetSampleIds: Set<string> | undefined;
  const axisKeys0 = inputSpec.annotations?.["pl7.app/axisKeys/0"];
  if (axisKeys0 !== undefined) {
    try {
      datasetSampleIds = new Set((JSON.parse(axisKeys0) as unknown[]).map(String));
    } catch {
      // Malformed annotation. Do not scope.
      datasetSampleIds = undefined;
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
  return datasetSampleIds
    ? Object.fromEntries(
        Object.entries(full).filter(([sampleId]) => datasetSampleIds.has(sampleId)),
      )
    : full;
}

// Filtered to settled samples: qcJson is the last per-sample step, so a present entry means that sample
// finished. Returns [] where the outputs have not settled.
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

// Read only while the snapshot's handle matches the CSV currently picked. That comparison is the whole guard
// against a stale read. Where a path fails to clear the snapshot, the mismatch makes the metadata absent
// rather than wrong.
function readCsvMeta(ctx: BlockRenderCtx<BlockArgs, BlockData>): CsvMeta | undefined {
  const snap = ctx.data.csvMetaSnapshot;
  if (snap === undefined) return undefined;
  return snap.handle === ctx.data.tagFeatureCsvHandle ? snap.meta : undefined;
}

// A project saved before the rule took a list carries `column` and not `columns`. Every reader goes through
// this function, so no data migration runs against stored projects.
export function groupingColumns(rule: GroupingRule | undefined): string[] {
  if (rule === undefined || rule.by !== "property") return [];
  if (rule.columns !== undefined) return rule.columns.filter((c: string) => c !== "");
  return rule.column ? [rule.column] : [];
}

// Which rungs this data could serve is display material. Never share one helper between a display of the
// rung and a projection of it.

/**
 * The scientist's choice, and nothing else. Never derive it from what the panel can serve.
 *
 * `args()` throws on undefined. This function only reports it. A stored choice passes through even where
 * this data cannot serve it, and nothing here writes to `data`, so a choice that becomes serviceable again
 * revives on its own.
 */
export function resolveReferenceSource(data: BlockData): ReferenceSource | undefined {
  return data.referenceSource;
}

const isDnaValue = (v: string) => /^[ACGTN]+$/i.test(v);

// Blank cells are ignored rather than counted against the column: a trailing empty row is a CSV artefact. A
// module helper, because a block output cannot read another output. barcodeMappingIssue stays silent while
// this holds, so the two never give the reader contradictory fixes.
function barcodeAlphabetProblem(
  ctx: BlockRenderCtx<BlockArgs, BlockData>,
): { offenders: string[]; checked: number; alternative: string | undefined } | undefined {
  if (!ctx.data.tagFeatureCsvHandle) return undefined;
  const barcodeCol = ctx.data.barcodeSeqColumn;
  if (!barcodeCol) return undefined;
  const meta = readCsvMeta(ctx);
  if (!meta) return undefined;
  const values = meta.valuesByColumn?.[barcodeCol];
  if (values === undefined) return undefined;
  const clean = (xs: string[]) => xs.map((v) => v.trim()).filter((v) => v !== "");
  const checked = clean(values);
  if (checked.length === 0) return undefined;
  const offenders = checked.filter((v) => !isDnaValue(v));
  if (offenders.length === 0) return undefined;
  // The usual mistake is a pick of the identifier column where the sequences are one column over.
  const alternative = meta.columns.find((c) => {
    if (c === barcodeCol || c === ctx.data.featureNameColumn) return false;
    const candidate = clean(meta.valuesByColumn?.[c] ?? []);
    return candidate.length > 0 && candidate.every(isDnaValue);
  });
  return { offenders, checked: checked.length, alternative };
}

// A CSV is sample-aware where the same barcode maps to different features per sample. The tell is a column
// whose distinct values are a SUPERSET of the dataset sample names. Undefined until both the CSV meta and
// the sample labels resolve.
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
    if (
      best === undefined ||
      (exact && !best.exact) ||
      (exact === best.exact && extra < best.extra)
    )
      best = { col, exact, extra };
  }
  return best?.col;
}

// One entry per plot. `init` and the v4 -> v5 migration share these values, so a migrated project opens on
// the chart a new project opens on.
const INITIAL_GRAPH_STATES = {
  scoreDistributionGraphState: { title: "Spread of the run's scores", template: "line" },
  referenceReadingGraphState: { title: "Reference reading across cells", template: "line" },
  fittedBackgroundGraphState: { title: "Fitted background per tag", template: "dots" },
} as const satisfies Pick<
  BlockData,
  "scoreDistributionGraphState" | "referenceReadingGraphState" | "fittedBackgroundGraphState"
>;

// v8 data shape: the current shape with the panel-versus-reads grid's state, which v9 strips.
type BlockDataV8 = BlockData & { runQualityMismatchTableState: PlDataTableStateV2 };

// v6 data shape: v8 without the undeclared-barcode grid's state. Every shape below hangs off V8: they all
// predate v9, so they all still carry the panel-versus-reads grid state that v9 strips.
type BlockDataV6 = Omit<BlockDataV8, "undeclaredBarcodesTableState">;

// v5 data shape: v6 without the reagent grid's state, which arrived with the reagent table.
type BlockDataV5 = Omit<BlockDataV6, "reagentTableState">;

// v4 data shape: v5 without the three GraphMaker states, which arrived with the distribution plots.
type BlockDataV4 = Omit<
  BlockDataV5,
  "scoreDistributionGraphState" | "referenceReadingGraphState" | "fittedBackgroundGraphState"
>;

// v3 data shape: the three grid states the two removed result views owned. `punchcardIdentities` is dead on
// the right-hand side of the Omit and harmless on the left.
type BlockDataV3 = Omit<BlockDataV4, "punchcardTableState" | "punchcardIdentities"> & {
  verdictTableState: PlDataTableStateV2;
  antigenQcTableState: PlDataTableStateV2;
  panelMismatchTableState: PlDataTableStateV2;
};

// v2 data shape: the preset selector and pattern string, with the dominance-era parameters still present.
// Nothing consumes those three fields.
type BlockDataV2 = Omit<
  BlockDataV3,
  | "datasetRef"
  | "roleColumn"
  | "referenceValues"
  | "referenceSource"
  | "panelReferenceMinMembers"
  | "distributionMinCells"
  | "countFloor"
  | "boundCutoff"
  | "boundProbability"
  | "minVotingCells"
  | "minAgreement"
  | "expectedBinderFraction"
  | "gateThreshold"
  | "grouping"
  | "contendingGroups"
  | "verdictTableState"
  | "antigenQcTableState"
  | "panelMismatchTableState"
> & {
  dominanceThreshold: number;
  offtargetProperty?: string;
  offtargetValues?: string[];
};

// v1 data shape, before presets: read geometry as three explicit length fields.
type BlockDataV1 = Omit<BlockDataV2, "presetId" | "pattern"> & {
  cellLen: number;
  umiLen: number;
  featureLen: number;
};

const dataModel = new DataModelBuilder()
  .from<BlockDataV1>("v1")
  .migrate<BlockDataV2>("v2", ({ cellLen, umiLen, featureLen, ...rest }) => {
    // The shipped default (16/10/15) maps to the fixed BEAM preset. Offset 0 is the only layout the v1 UI could
    // express.
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
  // v2 -> v3. The three dominance fields are dropped rather than carried: a field kept "just in case" still
  // travels in the args hash and stales the block on an edit that changes no computation. A parameter left
  // undefined reaches the CLI as its argparse default.
  .migrate<BlockDataV3>(
    "v3",
    ({ dominanceThreshold: _d, offtargetProperty: _p, offtargetValues: _v, ...rest }) => ({
      ...rest,
      ...VERDICT_DEFAULTS,
      verdictTableState: createPlDataTableStateV2(),
      antigenQcTableState: createPlDataTableStateV2(),
      panelMismatchTableState: createPlDataTableStateV2(),
    }),
  )
  // v3 -> v4. The three grid states go with the removed views rather than being carried: a saved column set or
  // filter is meaningful only against the frame it was saved on. The block still EMITS what those pages
  // showed.
  //
  // Never reuse a stripped key. `runQualityTableState` is NOT one of the two this migration strips.
  .migrate<BlockDataV4>(
    "v4",
    ({ verdictTableState: _v, antigenQcTableState: _q, panelMismatchTableState: _m, ...rest }) => ({
      ...rest,
      punchcardTableState: createPlDataTableStateV2(),
    }),
  )
  // v4 -> v5. `init` seeds a NEW project only, so a migration is the only route that reaches a stored project.
  .migrate<BlockDataV5>("v5", (data) => ({
    ...data,
    ...INITIAL_GRAPH_STATES,
  }))
  // v5 -> v6. A `PlAgDataTableV2` bound to an undefined state renders nothing and reports no error.
  .migrate<BlockDataV6>("v6", (data) => ({
    ...data,
    reagentTableState: createPlDataTableStateV2(),
  }))
  // v6 -> v7. Same reason as v6.
  .migrate<BlockDataV8>("v7", (data) => ({
    ...data,
    undeclaredBarcodesTableState: createPlDataTableStateV2(),
  }))
  // v7 -> v8. `:default-options` seeds a plot that has no saved state and never overwrites one, so this
  // resets the fitted-background plot alone.
  .migrate<BlockDataV8>("v8", (data) => ({
    ...data,
    fittedBackgroundGraphState: INITIAL_GRAPH_STATES.fittedBackgroundGraphState,
  }))
  // v8 -> v9. The panel-versus-reads view is gone and its grid state goes with it. Never reuse the stripped
  // key: a saved column set and filter means something only against the frame it was saved on.
  .migrate<BlockData>("v9", ({ runQualityMismatchTableState: _m, ...rest }) => ({ ...rest }))
  // v10. A stored `columnOrder.orderedColIds` is an explicit list and beats anything the model asks for. Reset
  // rather than rewritten: the saved filters and column set were saved against axes that no longer exist in
  // that order.
  .migrate<BlockData>("v10", (data) => ({ ...data, reagentTableState: createPlDataTableStateV2() }))
  .init(() => ({
    runMode: "full" as const, // full run by default. "dry" = read-limited Preview
    // The geometry the block shipped with, 10x 5' v2 BEAM (16 / 10 / 15).
    presetId: "tenx-beam",
    cellWhitelist: "", // de-novo CELL correction by default
    defaultBlockLabel: "",
    // minAgreement and gateThreshold are absent by design. Off means absent rather than zero.
    ...VERDICT_DEFAULTS,
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
    punchcardTableState: createPlDataTableStateV2(),
    // These names avoid the two keys the v3 -> v4 migration strips.
    runQualityTableState: createPlDataTableStateV2(),
    ...INITIAL_GRAPH_STATES,
    reagentTableState: createPlDataTableStateV2(),
    undeclaredBarcodesTableState: createPlDataTableStateV2(),
  }));

export const platforma = BlockModelV3.create(dataModel)
  .args((data): BlockArgs => {
    if (!data.fbFastqRef) throw new Error("Select the feature-barcode FASTQ");
    if (!data.tagFeatureCsvHandle) throw new Error("Upload the tag→feature CSV");
    if (!data.barcodeSeqColumn) throw new Error("Select the barcode-sequence column in the CSV");
    if (!data.featureNameColumn) throw new Error("Select the panel column naming each antigen");
    // REQUIRED. A run without a V(D)J dataset emits no verdicts. The panel rung is retired, and this projection
    // refuses a project stored under it rather than moving it.
    if (data.referenceSource === "panel")
      throw new Error(
        "The baseline source \u201CThe panel's own readings\u201D is no longer available. It read each " +
          "count against the median of the cell's other tags, which needs a decision at the clonotype " +
          "rather than at the cell, and every other rule here reads a cell first. Choose a declared " +
          "baseline tag, or each tag's own distribution, under \u201CBaseline source\u201D.",
      );
    if (!data.datasetRef)
      throw new Error(
        "Select the single-cell V(D)J dataset the verdicts are about. Every verdict is about one " +
          "clonotype, so the block cannot produce any without it.",
      );
    // The Python guards this too, but only after the full mitool chain runs.
    if (data.barcodeSeqColumn === data.featureNameColumn)
      throw new Error("Barcode-sequence and feature-name columns must be different");
    // Same reason as the guard above.
    for (const [role, col] of [
      ["Barcode-sequence", data.barcodeSeqColumn],
      ["Feature-name", data.featureNameColumn],
    ] as const) {
      if (RESERVED_TAGSTAT_COLUMNS.has(col))
        throw new Error(
          `${role} column "${col}" collides with a reserved tag-stat column; pick another`,
        );
    }
    // Its values are per-feature modes ("sum"/"all"), so it must be its OWN column. The Python guards this too,
    // but only after the mitool chain runs.
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
    // Never start a run with no reads to cap. The same up-front gate as mixcr-clonotyping.
    if (data.runMode === "dry" && (data.limitInput == null || data.limitInput < 1))
      throw new Error("Enter a read limit (≥ 1) for Preview mode, or switch to a full run");
    // Validate loosely: only the CELL/UMI/FEATURE tags and the R2 capture must be present, because refine-tags
    // and tag-stat reference them by name. mitool receives anything else verbatim.
    const preset = getPreset(data.presetId);
    if (!preset) throw new Error("Select a read-geometry preset");
    const pattern = preset.userConfigurable ? (data.pattern ?? "") : preset.pattern;
    const patternError = validatePattern(pattern);
    if (patternError) throw new Error(patternError);

    // The per-sample workflow body filters the CSV to its own sample's rows. MainPage.setSampleColumn takes the
    // snapshot, and this check requires it, so a half-set state disables Run.
    const sampleAware = !!data.sampleColumn;
    if (sampleAware) {
      if (!data.sampleLabelSnapshot || Object.keys(data.sampleLabelSnapshot).length === 0)
        throw new Error("Re-select the sample column (sample labels not captured)");
      // A dataset sample with no rows in the CSV's sample column would get no features and no message. The gate
      // reads the snapshots taken when the user picked the column, because args reads data only.
      const csvValues = new Set(data.sampleColumnValues ?? []);
      const missing = Object.values(data.sampleLabelSnapshot).filter((n) => !csvValues.has(n));
      if (missing.length > 0)
        throw new Error(
          `${missing.length} dataset sample(s) have no rows in the tag CSV's "${data.sampleColumn}" column ` +
            `(${missing.slice(0, 5).join(", ")}${missing.length > 5 ? "…" : ""}). ` +
            `Add rows for them, or clear the sample column to use one mapping for all samples.`,
        );
    }

    // per_cell_metrics.py refuses to map one barcode to two antigens, and it refuses at the END, after every
    // sample is parsed. Both numbers are snapshots taken when the user picked the barcode column. Absent means
    // the meta had not resolved then, and the gate stays quiet.
    if (
      !sampleAware &&
      data.panelRowCount !== undefined &&
      data.panelBarcodeDistinct !== undefined &&
      data.panelBarcodeDistinct < data.panelRowCount
    )
      throw new Error(
        `The tag CSV has ${data.panelRowCount} rows but only ${data.panelBarcodeDistinct} distinct ` +
          `barcodes, so one barcode maps to more than one antigen. Set the sample column if the CSV ` +
          `lists each barcode once per sample, or remove the duplicate rows.`,
      );

    if (data.countFloor < 0) throw new Error("The count floor cannot be negative");
    if (
      typeof data.boundProbability === "number" &&
      (data.boundProbability < VERDICT_DEFAULTS.boundProbability || data.boundProbability > 1)
    )
      throw new Error(
        `The fitted baseline's probability is at least ${VERDICT_DEFAULTS.boundProbability} and at most 1`,
      );
    if (data.boundCutoff < 0 || data.boundCutoff > 100)
      throw new Error("The bound cutoff is a score between 0 and 100");
    if (data.minVotingCells < 1) throw new Error("At least one cell must vote");
    // A majority is above half by construction, so a floor at or below half can never fire and the run
    // would record an agreement limit that did nothing. The settings field starts just above 50%; this is
    // the same line for a project stored before it existed, or written by another client.
    if (
      typeof data.minAgreement === "number" &&
      data.minAgreement > 0 &&
      (data.minAgreement <= 0.5 || data.minAgreement > 1)
    )
      throw new Error("The agreement floor is a share above 50% and at most 100%");
    // Strictly inside (0, 1). At either end the split hands every cell to one side and the fit silently
    // falls back, so the run would record a fraction it never used.
    if (
      typeof data.expectedBinderFraction === "number" &&
      (data.expectedBinderFraction <= 0 || data.expectedBinderFraction >= 1)
    )
      throw new Error("The expected binder fraction is a share above 0% and below 100%");
    // The cell condition GATES the fitted rung rather than tuning it, so it is a real population size.
    if (data.distributionMinCells < 1)
      throw new Error("A fitted baseline needs at least one cell to be fitted over");
    // The gate is a count of unique baseline counts. A stored fraction rounds to zero on projection,
    // which reaches the workflow as "off" while the settings field still shows a number.
    if (
      typeof data.gateThreshold === "number" &&
      data.gateThreshold > 0 &&
      Math.round(data.gateThreshold) < 1
    )
      throw new Error(
        "The admissibility gate is a count of unique baseline counts, so it is at least 1",
      );
    // A baseline is required, and a run without one does not happen. Refused HERE, before the run reads
    // anything, because the chosen rung and the declared baseline tag are both properties of the settings.
    //
    // A role column named on its own is inert: emit_verdicts.py reads it only under
    // `if args.role_column and reference_values`.
    if (!data.referenceSource)
      throw new Error(
        "Choose what the counts are read against, under “Baseline source”. Every verdict is a reading " +
          "against a baseline, so a run without one produces no answers at all. Which baselines this " +
          "panel can serve is listed with each option.",
      );
    if (data.referenceSource === "declared" && !data.roleColumn)
      throw new Error(
        "The declared-baseline option reads every count against one tag marked as the baseline, and no " +
          "panel column is set to say which tag that is. Choose the column under “Role column”, or pick " +
          "a different baseline source.",
      );
    // This projection does NOT check the panel-size condition, and cannot: that condition needs the count of
    // distinct barcodes, which lives in the CSV metadata this projection must not read. The software refuses the
    // run instead, and names the same condition.
    //
    // Nothing checks the cell-count condition before the run, and nothing can. A run on that rung proceeds and
    // reports afterwards that it established no baseline.
    //
    // Scoped to the DECLARED rung, and that scope is load-bearing. The Role column field is hidden under the
    // other rungs, so an unscoped check refuses a run over a value the scientist cannot see or clear.
    if (data.referenceSource === "declared" && data.roleColumn && !data.referenceValues?.length)
      throw new Error(
        `The panel column "${data.roleColumn}" declares each tag's role, but no value of it is marked ` +
          `as the baseline, so the column changes nothing. Under "Baseline value", choose the value that ` +
          `marks it, or change the baseline source.`,
      );
    // Both checks below walk this list, and each checks a grouping column on its own. A grouping may name
    // several columns, and a joined string such as "Identity, Channel" matches no panel header. It throws here,
    // and takes the whole block to Limbo, refs and all.
    const named: [string, string | undefined][] = [
      ["Baseline role", data.roleColumn],
      ...groupingColumns(data.grouping).map((c): [string, string] => ["Grouping", c]),
    ];

    // A column the panel reader consumes as a KEY is not a property column. Naming one here ends the run at the
    // exec. The way in is a reassignment of a key column WITHIN one panel file: the dropdowns stop offering it,
    // the stored pick survives, and the field reads empty while the data holds a value. Checked against data and
    // not against the header snapshot, because a key column IS a real header.
    const keyColumns: [string, string | undefined][] = [
      ["barcode sequence", data.barcodeSeqColumn],
      ["sample", data.sampleColumn],
    ];
    for (const [role, column] of named) {
      if (!column) continue;
      for (const [key, keyColumn] of keyColumns) {
        if (column === keyColumn)
          throw new Error(
            `The ${role} column "${column}" is also the ${key} column. The panel reader consumes that ` +
              `column as a key rather than a property, so choose a different column for one of them.`,
          );
      }
    }

    // A role column or a grouping column the panel does not carry ends the whole run at the exec, with no hint
    // of which setting caused it. Reads the headers snapshotted when the user picked the column, because args
    // reads data only.
    const panelColumns = data.panelColumnSnapshot;
    if (panelColumns?.length) {
      for (const [role, column] of named) {
        if (column && !panelColumns.includes(column))
          throw new Error(
            `The ${role} column "${column}" is not in the uploaded panel file. Select a column from the new panel.`,
          );
      }
    }

    // Canonicalised here and not in the editor. The args value is a cache key: the same declaration written in a
    // different order must produce the same string, or the block goes stale and re-runs the whole reading. A
    // group of fewer than two members is dropped.
    const contendingGroups = (data.contendingGroups ?? [])
      .map((group) => [...new Set(group)].sort())
      .filter((group) => group.length > 1)
      .sort((a, b) => a.join(" ").localeCompare(b.join(" ")));

    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvHandle: data.tagFeatureCsvHandle,
      barcodeSeqColumn: data.barcodeSeqColumn,
      featureNameColumn: data.featureNameColumn,
      // sum = OR, the default. all = AND, where a feature is called only when every member barcode fires. minUmi
      // is the AND per-barcode "fired" floor. Projected only alongside combineColumn, because the workflow passes
      // --min-umi only with --combine-col.
      ...(data.combineColumn ? { combineColumn: data.combineColumn } : {}),
      ...(data.combineColumn && typeof data.minUmi === "number" && data.minUmi >= 1
        ? { minUmi: Math.round(data.minUmi) }
        : {}),
      // Undefined projects as undefined, and the workflow's own default stands. Never gated on a positivity check:
      // none of these three is an "off means absent" switch.
      aggregateBarcodeIqrMultiplier: data.aggregateBarcodeIqrMultiplier,
      aggregateBarcodeMinUmiThreshold: data.aggregateBarcodeMinUmiThreshold,
      aggregateBarcodeTopN:
        typeof data.aggregateBarcodeTopN === "number"
          ? Math.round(data.aggregateBarcodeTopN)
          : undefined,
      // --- the binding reading ---
      datasetRef: data.datasetRef,
      // Empty and absent are the same claim for both fields. Two spellings of one request would otherwise be two
      // cache keys. Sent only for the rung that reads one, so a stale value from an abandoned rung cannot change
      // the cache key and re-run the reading.
      roleColumn: data.referenceSource === "declared" ? data.roleColumn || undefined : undefined,
      // The Python reads these values as a set, so the same values picked in a different order must not re-run the
      // reading.
      referenceValues:
        data.referenceSource === "declared" && data.referenceValues?.length
          ? [...new Set(data.referenceValues)].sort()
          : undefined,
      // Always concrete. --reference-source is required by the software, which has no default, and nothing below
      // this line picks a rung. `served_source` only ever drops a rung to none. It never substitutes a different
      // rung, so what this projects is what the run is answered under.
      referenceSource: resolveReferenceSource(data),
      panelReferenceMinMembers: Math.round(data.panelReferenceMinMembers),
      distributionMinCells: Math.round(data.distributionMinCells),
      countFloor: Math.round(data.countFloor),
      boundCutoff: data.boundCutoff,
      boundProbability: data.boundProbability,
      expectedBinderFraction: data.expectedBinderFraction,
      minVotingCells: Math.round(data.minVotingCells),
      // Off by default, and off means ABSENT. A minimum agreement of 0 passes every majority instead of skipping
      // the check. A gate of 0 sets aside every cell instead of gating none.
      minAgreement:
        typeof data.minAgreement === "number" && data.minAgreement > 0
          ? data.minAgreement
          : undefined,
      gateThreshold:
        typeof data.gateThreshold === "number" && data.gateThreshold > 0
          ? Math.round(data.gateThreshold)
          : undefined,
      // A rule over declared panel properties, and never a tag->identity map. Absent means one identity per tag.
      // Normalised to a list here, so the software receives one shape, though it still reads the older `column`.
      grouping:
        data.grouping?.by === "property"
          ? { by: "property" as const, columns: groupingColumns(data.grouping) }
          : data.grouping,
      contendingGroups: contendingGroups.length > 0 ? contendingGroups : undefined,
      // Each undefined projects as undefined, and emit_verdicts.py's own shipped default stands. Passed through
      // raw rather than gated on positivity: 0.0 is a real published threshold (usableReadError).
      cellBarcodeValidWarn: data.cellBarcodeValidWarn,
      cellBarcodeValidError: data.cellBarcodeValidError,
      readsPerCellWarn: data.readsPerCellWarn,
      aggregateBarcodeWarn: data.aggregateBarcodeWarn,
      aggregateBarcodeError: data.aggregateBarcodeError,
      undeclaredBarcodeWarn: data.undeclaredBarcodeWarn,
      undeclaredBarcodeError: data.undeclaredBarcodeError,
      usableReadWarn: data.usableReadWarn,
      usableReadError: data.usableReadError,
      // Projected only when dry, so a switch back to full changes the args hash and re-runs on the complete input.
      ...(data.runMode === "dry" && data.limitInput
        ? { limitInput: Math.round(data.limitInput) }
        : {}),
      pattern,
      tags: { cell: CELL_TAG, umi: UMI_TAG, feature: FEATURE_TAG },
      ...(sampleAware
        ? { sampleColumn: data.sampleColumn, sampleLabels: data.sampleLabelSnapshot }
        : {}),
      // "" = de-novo CELL correction, with no external whitelist.
      cellWhitelist: data.cellWhitelist ?? "",
      // Project positive integers only. A blank or zero field then reaches the workflow defaults (4 CPUs,
      // formula-sized RAM), and does not stale the block on an empty edit.
      ...(typeof data.perProcessCPUs === "number" && data.perProcessCPUs >= 1
        ? { perProcessCPUs: Math.round(data.perProcessCPUs) }
        : {}),
      ...(typeof data.perProcessMemGB === "number" && data.perProcessMemGB >= 1
        ? { perProcessMemGB: Math.round(data.perProcessMemGB) }
        : {}),
    };
  })
  // Staging depends on the CSV alone. featureNameColumn is NOT a prerun arg, and neither is fbFastqRef: a
  // staging key on the FASTQ re-imports the CSV on every FASTQ change and every PlRef re-resolve.
  //
  // THIS PROJECTION MUST NOT GROW TO INCLUDE csvMetaSnapshot, csvImportError, OR ANYTHING DERIVED FROM THEM.
  // The UI reads the panel from the blob this staging exports and writes the result into csvMetaSnapshot. A
  // data write re-renders staging only where the canonical JSON of THIS projection changes. Add the snapshot
  // here and the write re-renders staging, staging re-exports the blob, and the export re-triggers the write.
  // A staging re-render calls resetStaging first, so every turn of that loop DISCARDS the uploaded CSV.
  .prerunArgs((data) => ({
    tagFeatureCsvHandle: data.tagFeatureCsvHandle,
  }))
  // Enrichments (.enriches): NOT declared, by design. This block introduces a NEW [sampleId, cellId,
  // featureId] key space off a FASTQ input, and holds no ref to the downstream VDJ dataset. VDJ Multiomic
  // Integration discovers these columns under its VDJ anchor through the pl7.app/sc/cellLinker.

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
  // Columns on [sampleId, scClonotypeKey] flagged as anchors. VDJ Multiomic Integration uses the same query,
  // so the two blocks offer the user the same list. No linkerOptions beside it, by design: the cell linker
  // carries pl7.app/isLinkerColumn and tables hide it, so the workflow resolves it from this anchor by name.
  .output("datasetOptions", (ctx) =>
    ctx.resultPool.getOptions([
      {
        axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/scClonotypeKey" }],
        annotations: { "pl7.app/isAnchor": "true" },
      },
    ]),
  )
  // An identity is whatever the grouping rule groups tags by, so the options are the distinct values of the
  // barcode column or of the chosen property column. The panel metadata is column-wise and carries no
  // tag->name pairing. Retentive, so the editor does not blank on a rerun.
  //
  // This output exists so that only the USER'S PICKS ever reach data. A watcher that copied this list into
  // data would make the output depend on data derived from it, and two open clients would race.
  .retentiveOutput("identityOptions", (ctx): { value: string; label: string }[] => {
    const grouped = groupingColumns(ctx.data.grouping);
    // Nothing to offer under a grouping on SEVERAL columns. The prerun CSV meta is column-wise, with no pairing
    // between columns, so a cross of the columns invents combinations the panel never declared.
    if (grouped.length > 1) return [];
    const column = grouped[0] ?? ctx.data.barcodeSeqColumn;
    if (!column) return [];
    return (readCsvMeta(ctx)?.valuesByColumn?.[column] ?? []).map((v) => ({ value: v, label: v }));
  })
  // Computed here and not in .subtitle, because the subtitle context has no result pool. A UI watchEffect
  // copies this value into data.defaultBlockLabel.
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
    // The default subtitle must never render with dots. Periods come from a dotted dataset or file label, and
    // the " / " and " - " separators survive a strip of ".". A subtitle the user types in the sidebar does not
    // pass through this output.
    return parts.join(" / ").replace(/\./g, " ").replace(/ {2,}/g, " ").trim();
  })
  // Retentive, so the dropdowns do not blank on a rerun.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] =>
    (readCsvMeta(ctx)?.columns ?? []).map((c) => ({ value: c, label: c })),
  )
  // The UI reads this when the user picks the sample column, and snapshots that column's values into data.
  // args() gates Run on that snapshot.
  .retentiveOutput(
    "csvValuesByColumn",
    (ctx): Record<string, string[]> => readCsvMeta(ctx)?.valuesByColumn ?? {},
  )
  // A UI warning only. args() is the authoritative gate.
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
    // Missing samples block Run, because args() throws. Extra CSV values are informational, because nothing
    // reads those rows.
    const lines: string[] = [];
    if (missing.length > 0)
      lines.push(
        `${missing.length} ${missing.length === 1 ? "sample" : "samples"} in your dataset have no rows in the CSV: ${fmt(missing)}. The block disables Run until every sample has rows, or until you clear the sample column.`,
      );
    if (extra.length > 0)
      lines.push(
        `${extra.length} sample ${extra.length === 1 ? "value" : "values"} in the CSV match no sample in your dataset: ${fmt(extra)}. The block ignores those rows.`,
      );
    return lines.length > 0 ? lines : undefined;
  })
  // Advisory only. The user must still pick it, which is the gesture that snapshots the sample map into data.
  .retentiveOutput("suggestedSampleColumn", (ctx): string | undefined => suggestSampleColumn(ctx))
  // A UI warning only. mitool guards the same condition, but by failing refine-tags in the middle of the run.
  //
  // A panel CSV often carries BOTH an identifier column and the nucleotide column: "Barcode" holds T0100 and
  // "Sequence" holds CGATGCCGGACGATC. The wrong pick fails inside barcode correction, several stages after the
  // reads are parsed, with "Error while loading sequence set from ./panel.txt" and a Java stack trace.
  //
  // The args guard cannot catch this: it sees `data` only, and the values live in the prerun CSV meta. Not
  // gated on sampleColumn, because a per-sample filter never turns an identifier into a sequence.
  .retentiveOutput("barcodeAlphabetIssue", (ctx): string | undefined => {
    const problem = barcodeAlphabetProblem(ctx);
    if (problem === undefined) return undefined;
    const { offenders, checked, alternative } = problem;
    return (
      `Column "${ctx.data.barcodeSeqColumn}" does not hold nucleotide sequences. ${offenders.length} ` +
      `of ${checked} distinct values contain characters outside A/C/G/T/N, for example ` +
      `"${offenders[0]}". The block builds the feature-barcode panel from this column, so the run ` +
      "would fail during barcode correction. " +
      (alternative !== undefined
        ? `Column "${alternative}" holds sequences. Pick that one.`
        : "Pick the column that holds the barcode nucleotide sequences.")
    );
  })
  // A UI warning only. The Python guards it authoritatively at the end of the run. A barcode on more than one
  // row fans the per-cell join and doubles molecule counts. Skipped where rowCount is absent.
  .retentiveOutput("barcodeMappingIssue", (ctx): string | undefined => {
    if (!ctx.data.tagFeatureCsvHandle) return undefined;
    const barcodeCol = ctx.data.barcodeSeqColumn;
    if (!barcodeCol) return undefined;
    if (ctx.data.sampleColumn) return undefined; // already sample-aware: the per-sample filter fixes it
    // Silent while the column holds no sequences at all. The fault is then in the barcode column itself, and
    // "Some barcode sits on two rows" would direct the reader to the sample column.
    if (barcodeAlphabetProblem(ctx) !== undefined) return undefined;
    const meta = readCsvMeta(ctx);
    if (!meta || meta.rowCount === undefined) return undefined;
    const distinct = meta.valuesByColumn?.[barcodeCol]?.length ?? 0;
    if (distinct >= meta.rowCount) return undefined; // no duplicate barcodes
    const suggested = suggestSampleColumn(ctx);
    return (
      `The CSV has ${meta.rowCount} rows but only ${distinct} distinct barcodes. ` +
      "The block cannot map one barcode to two antigens, so the run will stop. " +
      `If the CSV lists each barcode once per sample, set the Sample column${suggested ? ` ("${suggested}")` : ""}. ` +
      "If it does not, remove the duplicate rows."
    );
  })
  // The case the two checks either side of this one cannot see: the panel IS sample-keyed, no sample column is
  // set, and no repeated barcode gives it away. `barcodeMappingIssue` needs a duplicate barcode, which a fully
  // disjoint panel never supplies. Guarded against that check's condition, so the two never fire together.
  //
  // Read as one panel, every sample is offered every antigen, and an antigen a sample was never stained with
  // reads NOT BOUND instead of NEVER ASKED. Nothing else on the page reports it.
  .retentiveOutput("unkeyedSamplePanel", (ctx): string | undefined => {
    if (!ctx.data.tagFeatureCsvHandle || !ctx.data.barcodeSeqColumn) return undefined;
    if (ctx.data.sampleColumn) return undefined;
    const meta = readCsvMeta(ctx);
    if (!meta || meta.rowCount === undefined) return undefined;
    const distinct = meta.valuesByColumn?.[ctx.data.barcodeSeqColumn]?.length ?? 0;
    if (distinct < meta.rowCount) return undefined; // barcodeMappingIssue owns this one
    const suggested = suggestSampleColumn(ctx);
    if (!suggested) return undefined;
    return (
      `The CSV has a column that names your samples ("${suggested}"), and you have not set the sample ` +
      "column. The block therefore reads the CSV as a single panel and applies that one panel to every " +
      "sample. It then judges every sample on antigens it was never stained with, and those antigens " +
      'come back as "not bound" instead of "never asked". If the CSV is sample-specific, set the ' +
      "Sample column."
    );
  })
  // args() needs this and the barcode column's distinct count to refuse a duplicate mapping.
  .retentiveOutput("csvRowCount", (ctx): number | undefined => readCsvMeta(ctx)?.rowCount)
  // A local pick closes this window within a tick. A remote pick holds it until the upload lands and the UI
  // parses the exported blob. NOT retentive: it must report the live loading state, including on a CSV swap.
  .output(
    "csvColumnsLoading",
    (ctx): boolean => !!ctx.data.tagFeatureCsvHandle && readCsvMeta(ctx) === undefined,
  )
  // getImportProgress() registers the import handle with the middle-layer upload driver, which pushes the CSV
  // bytes. Without this output the CSV never uploads, and every per-sample body hangs on __extra_tagsCsv.
  // Mirrors immune-assay-data index.ts and samples-and-data.
  .output(
    "tagFeatureCsvImportHandle",
    (ctx) => ctx.outputs?.resolve("tagFeatureCsvImportHandle")?.getImportProgress(),
    { isActive: true },
  )
  // The same upload driver, resolved from the PRERUN render. args() REQUIRES the CSV-derived dropdowns the
  // prerun populates, and the main driver above fires only once args() passes, so on its own it deadlocks.
  // Mirrors samples-and-data.
  .output(
    "tagFeatureCsvImportHandlePrerun",
    (ctx) =>
      ctx.prerun
        ?.resolve({ field: "tagFeatureCsvImportHandle", allowPermanentAbsence: true })
        ?.getImportProgress(),
    { isActive: true },
  )
  // Lets the UI read the CSV's bytes for a REMOTE (index://) pick, which it cannot read from the user's disk.
  // The prerun already exports csvFile to make staging demand the blob, so this adds no work to the workflow.
  // `traverse` rather than `resolve`, because it does not assert a field type.
  .output("csvFileHandle", (ctx) => ctx.prerun?.traverse({ field: "csvFile" })?.getFileHandle())
  // Drives the block spinner through the app.ts progress callback.
  .output("isRunning", (ctx) => ctx.outputs?.getIsReadyOrError() === false)
  .output("started", (ctx) => ctx.outputs !== undefined)
  // A report appears when its step finishes.
  .output("sampleStep", (ctx): Record<string, SampleStep> | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const reports = parseResourceMap(
      ctx.outputs.resolve("stepReports"),
      (acc) => acc.getFileHandle(),
      false,
    );
    if (!reports) return {};
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
  // The per-sample Logs tab (PlLogView) binds them. A no-match sample carries its 1-parse entry alone, so the
  // map key set varies.
  .output("stepLogs", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(ctx.outputs.resolve("stepLogs"), (acc) => acc.getLogHandle(), false)
      : undefined,
  )
  // Surfaced apart from stepLogs, because the workflow produces it after it builds the mitool stepLogs map.
  .output("metricsLog", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("metricsLogStream"),
          (acc) => acc.getLogHandle(),
          false,
        )
      : undefined,
  )
  .output("metricsProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("metricsLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // The workflow registers that log the moment the per-sample body runs, and before parse finishes, so this
  // appears before the stepLogs map fills. stepProgress carries the per-step bar detail.
  .output("parseProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("parseLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // ui/src/progress.ts composes these into a MONOTONIC cumulative bar, each step owning a quarter. The bar
  // never resets to zero between steps. Same source as stepLogs.
  .output("stepProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("stepLogs"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // qcJson is the LAST per-sample step, and is inline JSON content, so getDataAsJsonOrUndefined reads it
  // synchronously. A sample outside this set is still Processing.
  .output("completedSamples", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;
    return parseQcRows(ctx).map((e) => String(e.key[0]));
  })
  // Present per sample once its qc step settles. Same source as completedSamples, so the two grid columns fill
  // as each sample finishes.
  .output("sampleQc", (ctx): Record<string, QcRow> | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const out: Record<string, QcRow> = {};
    for (const e of parseQcRows(ctx)) out[String(e.key[0])] = e.value as QcRow;
    return out;
  })
  // Shares resolveSampleLabels with analysisLog. Its own output, because one output cannot read another.
  .output("sampleLabels", (ctx): Record<string, string> | undefined => resolveSampleLabels(ctx))
  // One area, whatever the sample count. Detailed per-sample statistics live on the QC page (qcSummaryTable).
  .output("analysisLog", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;

    const labels = resolveSampleLabels(ctx);
    const entries = parseQcRows(ctx);
    const done = entries.length;
    const running = ctx.outputs.getIsReadyOrError() === false;

    // No fixed denominator. The block processes only the samples present in its feature-barcode dataset, and
    // that set is not reliably known until the run completes. On a crash the count freezes where it reached.
    if (running) {
      return done === 0
        ? ["Processing…"]
        : [`Processing… ${done} sample${done === 1 ? "" : "s"} complete.`];
    }
    if (done === 0) return undefined;

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
    // Zero cells detected is a fact about the sample. The panel-assigned fraction is NOT a second condition
    // here: its complement is the share of reads in barcodes the panel never declares, and that status stays on
    // the barcode.
    const flagged = entries.filter((e) => ((e.value as QcRow).cellsDetected ?? 0) === 0);

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
        `${flagged.length} sample${flagged.length === 1 ? "" : "s"} flagged — no cell barcodes detected: ${names.join(", ")}.`,
      );
      lines.push("  See the QC page for per-sample detail.");
    } else {
      lines.push("No samples flagged.");
    }
    lines.push("", "Analysis complete. Full per-sample statistics are on the QC page.");
    return lines;
  })
  // ONE ROW PER CELL [sampleId, cellId]. The per-feature matrix still reaches the result pool
  // (perCellFeatures) for VDJ Multiomic Integration.
  //
  // createPlDataTableV2 and NOT V3. This frame is our OWN self-contained, non-batch processColumn output, and
  // createPlDataTableV3's discovery cannot render it: the scoped-sources form returns undefined under every
  // anchor and maxHops config, and the array-columns form runs discoverLabelColumnVariants over the ENTIRE
  // result pool and hangs forever on the upstream Samples & Data FASTQ File-dataset
  // (no_data:<sndBlock>:pf.dataset.*). blocks/peptide-extraction uses the same pattern for the same setup.
  .output(
    "perCellTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("perCellTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.tableState);
    },
    { retentive: true, withStatus: true },
  )
  // createPlDataTableV2 like perCellTable, because V2 runs getAllLabelColumns over the result pool and
  // auto-joins the matching sampleId label. A V3 selector of { mode: "enrichment", maxHops: 0 } never
  // traverses to the upstream pl7.app/label column, and the sampleId axis then renders the raw sample hash.
  .output(
    "qcSummaryTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("qcSummaryTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.qcSummaryTableState);
    },
    { retentive: true, withStatus: true },
  )
  // Two parts of the card read this, and neither narrows anything: the punch hover, and the card's empty
  // state, which tells "the pivot emitted no identity columns" apart from "this run has no rows".
  //
  // Read from the pivot's own columns, and not from the run record's identity list. The pivot is size-gated
  // upstream, so a run over a large panel names its identities in the record and emits no columns at all.
  .retentiveOutput("punchcardIdentityOptions", (ctx): { value: string; label: string }[] => {
    const pCols = ctx.outputs
      ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
      ?.getPColumns();
    if (pCols === undefined) return [];
    const seen = new Set<string>();
    const options: { value: string; label: string }[] = [];
    for (const c of pCols) {
      if (c.spec.name !== PUNCH_COLUMN_NAME) continue;
      const identity = c.spec.domain?.[PUNCH_IDENTITY_DOMAIN];
      if (identity === undefined || seen.has(identity)) continue;
      seen.add(identity);
      // For a merged identity that label is the joined name, and not the barcode.
      const label = c.spec.annotations?.["pl7.app/label"];
      options.push({ value: identity, label: label ?? identity });
    }
    return options;
  })
  // One row per clonotype set, one column per identity, the whole panel every time. The pivoted shape comes
  // from the workflow, because a table cannot pivot a (set, identity) frame into columns.
  //
  // V3 here, and V2 everywhere else in this block, for a reason particular to this table. V2 fails with
  // `Cannot produce a Vec1 with a length of zero`: these columns are keyed on ONE axis,
  // `pl7.app/vdj/scClonotypeKey`, the result pool holds a label column for exactly that axis, and consuming
  // the frame's only axis leaves the engine an empty key vector. V3's `primaryColumns` form takes the columns
  // as given and runs NO data-column discovery, so it also cannot hang on the upstream Samples & Data FASTQ
  // dataset -- the hazard that keeps the other tables here on V2. Each of those needs its own check against
  // that hazard before it follows.
  .output(
    "punchcardTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      const identityOf = (c: (typeof pCols)[number]) => c.spec.domain?.[PUNCH_IDENTITY_DOMAIN];
      // Every identity the pivot produced, always. Narrowing is the grid's job.
      const cols = pCols.filter((c) => identityOf(c) !== undefined);
      if (cols.length === 0) return undefined;
      // The clonotype's cell count. It carries no identity domain, so the filter above drops it. `primaryColumns`
      // runs no discovery, so a column absent from this list never reaches the grid. Keyed on the same single axis
      // as the punch columns, which is what makes it safe to add.
      const cellCount = pCols.filter((c) => c.spec.name === PUNCH_CELL_COUNT_COLUMN);
      // Alphabetical by the name a READER sees. The workflow emits these columns sorted by identity, which under
      // the per-tag grouping is the barcode, and a panel's names never sort as its sequences do. Numeric
      // collation, so `antigen_9` precedes `antigen_10`. `columns: null` adds the clonotype column separately.
      const labelOf = (c: (typeof cols)[number]) =>
        c.spec.annotations?.["pl7.app/label"] ?? (identityOf(c) as string);
      const ordered = [...cols].sort((a, b) =>
        labelOf(a).localeCompare(labelOf(b), undefined, { sensitivity: "base", numeric: true }),
      );
      // Headers carry the identity's full name, and never a truncation.
      //
      // Column ORDER comes from the `pl7.app/table/orderPriority` annotation on each spec, and from nothing else.
      // A `displayOptions.ordering` rule and this array's own order are BOTH inert here. The cell count carries
      // 96000, between the clonotype label's 100000 and the punches' 92000. To fix a column that "renders last",
      // measure with `aria-colindex`: `querySelectorAll('[role="columnheader"]')` returns AG Grid's recycled
      // header nodes in an order unrelated to column position.
      return createPlDataTableV3(ctx, {
        primaryColumns: [...cellCount, ...ordered].map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.punchcardTableState,
      });
    },
    { retentive: true, withStatus: true },
  )
  // DERIVED from an emitted column and never written by hand. `isJsonEqual` matches `showCellButtonForAxisId`
  // on exact JSON equality, domain and all, so a hand-written `{type, name}` matches nothing and renders no
  // button and no error.
  .output("clonotypeAxisId", (ctx): AxisId | undefined => {
    const pCols = ctx.outputs
      ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
      ?.getPColumns();
    const axis = pCols?.[0]?.spec.axesSpec[0];
    return axis === undefined ? undefined : getAxisId(axis);
  })
  // ONE clonotype's identities, read down the page.
  //
  // Reads `antigenVerdictsTable`: the LONG verdicts family at (set, identity) grain, which main.tpl holds open
  // for exactly this, so the page costs no workflow change and no second import.
  //
  // NOT gated on the identity count that gates the card's pivots. That gate exists because a pivot costs a
  // COLUMN per identity. Here an identity costs a ROW.
  //
  // The filter is pushed down, and not applied afterwards. `createPTableDefV3` wraps the join in a
  // `{type:"filter", predicate}` node, which the engine lowers into the data query (pframes-rs
  // `visit_filter`), so one clonotype's rows cross the boundary whatever the run's size.
  .output(
    "expansionTable",
    (ctx) => {
      // A table built with no filter is EVERY clonotype's identities at once.
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenVerdictsTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // NAMED explicitly, which is the whole correctness of this call. `antigenVerdictsTable` surfaces the entire
      // export frame, whose families are keyed on five different axes: tag, panel, sample, set, and
      // (set, identity). One table over all of them is a malformed join, and the SDK answers `discoverColumns
      // failed` out of `discoverLabelColumns`. Only the (set, identity) family belongs here.
      //
      // The identity's readable name is NOT named here. It is emitted twice under one spec -- `identityLabels`
      // into `exportFb`, and `reagentIdentityLabels` into `reagentFb` -- and `reagentFb` reaches this block as
      // the `antigenReagentTable` output, so `columns: null` discovers it. Supplying the `exportFb` copy as well
      // rendered two identical "Antigen" columns. The two specs carry no domain, so no visibility rule separates
      // them; dropping one supplier is the only route. `reagentFb` is built unconditionally beside the verdicts.
      //
      // Could-answer is CONDITIONAL. Under one panel it is the clonotype's own cell count at every identity, which
      // the grid already carries beside its name.
      //
      // Read from the run RECORD, and not from current args. What panels a run carried is a fact about that run.
      const runMeta = ctx.outputs
        ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
        ?.getDataAsJsonOrUndefined<VerdictRunMeta>();
      // Absent reads as one panel.
      const panelsDiffer = (runMeta?.samplePanelCount ?? 1) > 1;
      // Not-bound is absent by design. A cell's vote is exactly one of bound or not-bound, so a third column is
      // `answered - bound` printed out. It is not in the export either.
      const WANTED = [
        "pl7.app/antigen/verdict",
        ...(panelsDiffer ? ["pl7.app/antigen/cellsAsked"] : []),
        "pl7.app/antigen/cellsAnswered",
        "pl7.app/antigen/cellsBound",
      ];
      const pCols = WANTED.flatMap((name) => frame.filter((c) => c.spec.name === name));
      // Named, never positional. The filter above preserves WANTED's order, and a conditional member shifts it.
      const verdictCol = pCols.find((c) => c.spec.name === "pl7.app/antigen/verdict");
      // Checked directly, and before `setAxis` is derived from it. The filter below reads `verdictCol.id`.
      if (verdictCol === undefined) return undefined;
      const setAxis = verdictCol.spec.axesSpec[0];
      if (setAxis === undefined) return undefined;
      return createPlDataTableV3(ctx, {
        primaryColumns: pCols.map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.expansionTableState,
        // `PlAgDataTableV2` drops any axis that has a label column, and renders the label column in that axis's
        // place. The identity's label arrives by discovery, not as a primary column.
        //
        // Both columns the table would show as a name are called `pl7.app/label`, and the axis each one labels tells
        // them apart. The FIRST match wins, so the order of these two rules matters.
        displayOptions: {
          visibility: [
            // The identity's name, the row's subject. Required although the spec already annotates "default": the
            // catch-all rule below matches this column too, and the first match wins.
            {
              match: {
                name: "^pl7\\.app/label$",
                axes: [{ name: "^pl7\\.app/antigen/identityId$" }],
                partialAxesMatch: false,
              },
              visibility: "default",
            },
            // Any other label column here labels the CLONOTYPE axis. Optional rather than hidden, so the Columns picker
            // can restore it.
            { match: { name: "^pl7\\.app/label$" }, visibility: "optional" },
          ],
          // Cells-that-answered sits LAST, behind the count it contains. Its annotation puts it at 98000, ahead of
          // cells-that-read-bound at 97500, and in this panel the two swap. Overridden here and not in the workflow
          // spec, whose columns are EXPORTS with downstream readers and whose priorities are global.
          //
          // This rule only reaches a clonotype the grid has not drawn before. `expansionTableState.stateCache` keeps
          // one grid state PER `sourceId`, which here is the expanded clonotype, and a stored
          // `columnOrder.orderedColIds` beats anything the model asks for. A reorder that must reach already-opened
          // clonotypes needs the cache invalidated, which is not done here.
          ordering: [{ match: { name: "^pl7\\.app/antigen/cellsAnswered$" }, priority: 90000 }],
        },
        filters: {
          type: "and",
          filters: [
            {
              type: "patternEquals",
              // The FULL axis id, domain included. A dropped domain leaves an id that `remapFilterColumnIds` cannot
              // resolve, and the SDK's unresolved-leaf path then calls `console`, which the model's QuickJS sandbox lacks.
              // The symptom is `ReferenceError: 'console' is not defined` from deep inside the SDK.
              column: {
                type: "axis",
                id:
                  setAxis.domain === undefined
                    ? { name: setAxis.name, type: setAxis.type }
                    : { name: setAxis.name, type: setAxis.type, domain: setAxis.domain },
              },
              value: String(chosen[0]),
            },
            {
              type: "patternNotEquals",
              // A never-asked position is not a reading. Filtered by the verdict's own value, and not by a count: a bound
              // count of 0 is a real reading and must stay.
              column: { type: "column", id: verdictCol.id },
              value: "never asked",
            },
          ],
        },
      });
    },
    { retentive: true, withStatus: true },
  )
  // ONE row per cell of the chosen clonotype, one column per identity, each position carrying the cell's own
  // reading rather than its set's verdict. An `unreliable` on the card is cells that disagree, and nothing but
  // this face shows the disagreement.
  //
  // Filtered on `setId`, which is a COLUMN here and not an axis: the frame is keyed (sampleId, cellId), so the
  // clonotype is a property of the row. The filter leaf takes `PColumn.id`, and never a hand-built id.
  .output(
    "cellExpansionTable",
    (ctx) => {
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenCellReference", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // Without the set column there is no filter, and an unfiltered table here is every cell in the run against
      // every identity. Absent means the software gated the pivot away, which is a legitimate state and not an
      // error, so the page states the reason from the run record.
      const setCol = frame.find((c) => c.spec.name === "pl7.app/antigen/cellSetId");
      if (setCol === undefined) return undefined;
      const punchCols = frame.filter((c) => c.spec.name === "pl7.app/antigen/cellPunch");
      if (punchCols.length === 0) return undefined;
      const boundCount = frame.filter((c) => c.spec.name === "pl7.app/antigen/boundIdentities");
      // No ordering rule, by design. The bound count's own annotation priority (95000) outranks every identity
      // column at 94000 and below.
      return createPlDataTableV3(ctx, {
        primaryColumns: [setCol, ...boundCount, ...punchCols].map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.cellExpansionTableState,
        filters: {
          type: "and",
          filters: [
            {
              type: "patternEquals",
              column: { type: "column", id: setCol.id },
              value: String(chosen[0]),
            },
          ],
        },
      });
    },
    { retentive: true, withStatus: true },
  )
  // Read from `outputs` and not from the exports, because a block's own exports are not in its own result
  // pool.
  //
  // `allowPermanentAbsence` for the same reason punchcardTable needs it: a chosen V(D)J dataset gates the whole
  // verdict stage, and a resolve that treats a permanent absence as a pending one waits forever.
  //
  // A frame with no rows is NOT folded into undefined. Absent means the verdict stage did not run. Empty means
  // it ran and had nothing to report, which for the mismatch check is the good outcome.
  .output(
    "runQualityTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenQcTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.runQualityTableState);
    },
    { retentive: true, withStatus: true },
  )
  // ONE p-frame for GraphMaker, and not rows in the measurement table.
  .output(
    "runQualityDistributions",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenQcDistributions", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      return createPFrameForGraphs(ctx, pCols);
    },
    { retentive: true, withStatus: true },
  )
  // One row per (panel, tag, identity). A tag that carries two identities takes a row under each.
  .output(
    "reagentTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenReagentTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.reagentTableState);
    },
    { retentive: true, withStatus: true },
  )
  // Carries two shares: each sequence's own, and the sample's whole undeclared share, which is what the status
  // reads. That status is the barcode's, and never rolled into any sample's own. Rows are the pre-refine pass,
  // so they include sequences correction later snapped onto the panel. Usually empty, which is the wanted outcome.
  .output(
    "undeclaredBarcodesTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenUndeclaredBarcodesTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.undeclaredBarcodesTableState);
    },
    { retentive: true, withStatus: true },
  )
  // The comparator that SERVED, and not the one that was requested. The software degrades a request it cannot
  // honour.
  .output("verdictRunMeta", (ctx): VerdictRunMeta | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<VerdictRunMeta>(),
  )
  // The single source for both the Main grid's Quality tag and the sample detail view's Quality Checks tab, so
  // the two cannot disagree about one sample. The verdict step writes the sample report.
  .output("sampleQcReport", (ctx): Record<string, SampleQcReport> | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenSampleQc", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<Record<string, SampleQcReport>>(),
  )
  // Read as content, because the chart takes its bins as values.
  .output("tagCountBins", (ctx): TagCountBins | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenTagBins", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<TagCountBins>(),
  )
  // The same call `args()` projects, so the field cannot show one rule while the workflow receives another.
  // Keep it that way. Nothing writes back, so there is no hairpin.
  .output("effectiveReferenceSource", (ctx): ReferenceSource | undefined =>
    resolveReferenceSource(ctx.data),
  )
  // The panel's own readings are retired, and empty droplets need gene expression this block does not read.
  //
  // Whether the declared rung can serve is knowable before a run, from the panel metadata staging already
  // emits. The distribution rung's conditions are properties of the DATA, so its description states them and
  // the run reports them.
  .retentiveOutput("referenceSources", (ctx): ReferenceSourceChoices => {
    const meta = readCsvMeta(ctx);
    const roleColumn = ctx.data.roleColumn;
    const roleValues = new Set(roleColumn ? (meta?.valuesByColumn?.[roleColumn] ?? []) : []);
    const declaredTags = (ctx.data.referenceValues ?? []).filter((v) => roleValues.has(v));

    // EVERY rung is offered and every rung is selectable, whether or not it can serve yet. `needs` carries what
    // is still missing, and the form shows it against the chosen rung.
    const options: ReferenceSourceChoices["options"] = [
      {
        value: "declared",
        label: "Declared baseline tag",
        description:
          "The block reads each count against the tag your panel marks as the baseline, in the " +
          "same cell. Verdicts read this way compare across runs.",
        needs:
          declaredTags.length > 0
            ? undefined
            : "Select the panel column that declares each tag's role. Then select the value that " +
              "marks the baseline tag. Both fields are below.",
      },
      // No `needs`. This rung turns on the sample's cell count and on whether each tag's counts separate. This
      // block has read neither, and the second is answered per tag rather than per run.
      {
        value: "distribution",
        label: "Each tag's own distribution",
        description:
          `The block splits each tag's counts across a sample's cells into two components. It reads ` +
          `each count against the lower component. The sample needs at least ` +
          `${Math.round(ctx.data.distributionMinCells)} cells. Nothing checks that the two ` +
          `components stand apart. The run shows each fit on the Run quality page, and you judge it. ` +
          `Where no two-component fit can be computed, that tag gets no baseline, and only the ` +
          `antigens it carries read unreliable. Select this where your panel declares no baseline tag.`,
      },
    ];

    // `none` is NOT offered, and there is no fourth option. A baseline is required, and "no baseline" is a
    // configuration `args()` refuses rather than a position a scientist can select.

    const fallback = "no baseline -- every verdict that needs one reads unreliable";
    return { options, fallback };
  })
  .title(() => "Feature Barcode Profiling")
  // The subtitle render context is args-only. A touch of the result pool or of outputs renders "Invalid
  // subtitle". The dynamic string is derived in the `suggestedBlockLabel` OUTPUT, which HAS the pool, and a UI
  // watchEffect copies it into `defaultBlockLabel`. Guard `ctx.data`: it can be undefined before the app
  // parses block storage.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // ctx.outputs settles when the workflow starts emitting, the same signal as the `started` output.
  .sections((ctx) => {
    const hasRun = ctx.outputs !== undefined;
    return [
      { type: "link" as const, href: "/" as const, label: "Main" },
      ...(hasRun
        ? [
            { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
            { type: "link" as const, href: "/results" as const, label: "Per-cell results" },
            // Shown for every run, including a run with no V(D)J dataset. That run produces no antigen columns, and
            // this page is the only place a user learns why.
            { type: "link" as const, href: "/punchcard" as const, label: "Explore readout" },
            // "Run quality" rather than "QC". That page is per SAMPLE and this one is per run.
            { type: "link" as const, href: "/antigen-qc" as const, label: "Run quality" },
          ]
        : []),
    ];
  })
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
