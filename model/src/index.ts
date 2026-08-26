import type {
  AxisId,
  BlockRenderCtx,
  InferOutputsType,
  PlDataTableStateV2,
} from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
  createPFrameForGraphs,
  createPlDataTableV2,
  createPlDataTableV3,
  DataColumn,
  DataModelBuilder,
  isPColumnSpec,
  parseResourceMap,
  getAxisId,
} from "@platforma-sdk/model";
import { assemblePattern, CELL_TAG, FEATURE_TAG, UMI_TAG, validatePattern } from "./pattern";
import { getPreset } from "./presets";
import type { BlockArgs, BlockData, CsvMeta, GroupingRule, ReferenceSource } from "./types";

export { assemblePattern, parsePattern, validatePattern } from "./pattern";
export type { PatternParts } from "./pattern";
export { allPresets, getPreset } from "./presets";
export type { Preset } from "./presets";
export type { BlockArgs, BlockData, CsvMeta, GroupingRule, ReferenceSource } from "./types";

// Re-exported so the UI can seed a grid state. The ui package depends on ui-vue alone, and ui-vue does
// not carry this factory.
export { createPlDataTableStateV2 } from "@platforma-sdk/model";
export type { PTableKey } from "@platforma-sdk/model";

// The shipped defaults. Each one restates the Python default: verdict.py DEFAULT_FLOOR and BOUND_CUTOFF,
// combine.py DEFAULT_MIN_VOTERS. Each is a declared default and not a calibrated line. Nothing published
// sets any of them.
const DEFAULT_COUNT_FLOOR = 4;
const DEFAULT_BOUND_CUTOFF = 75;
const DEFAULT_MIN_VOTING_CELLS = 1;
// From one preprint. That preprint's own panels held fifty and a hundred members. Nothing validates a
// lower value. The value GATES rather than tunes. Below it, a count read against a handful of other
// antigens is not a background estimate. Keep the value above the fifteen-tag cap of an antibody kit. Such
// a panel then uses the tag-distribution rung.
const DEFAULT_PANEL_REFERENCE_MIN_MEMBERS = 25;
// Both values come from the study behind the tag-distribution rung. The first is that study's own
// bootstrapping figure. The second has no published value. The paper shows the trough in a figure and
// never states how deep a trough must be. It ships as a declared default. A run can change it and reports
// the change. Mirrors tag_distribution.py.
const DEFAULT_DISTRIBUTION_MIN_CELLS = 300;

// args() below projects the nine inherited lines and the three aggregate-barcode knobs straight through,
// undefined included. An undefined line reaches the CLI as the shipped default that verdict-args.lib.tengo
// substitutes through `_num`. An undefined knob reaches the argparse default in qc_report.py. Those two
// values are what a run is scored against.
//
// The two maps below are DISPLAY ONLY. The settings fields read them to show the number already in force
// where the stored value is undefined. Nothing seeds them into `data`. args() never projects them. A field
// that shows 0.75 and a field that holds 0.75 produce the same command line. They also produce the same args
// hash.
//
// Each value MUST equal its counterpart in verdict-args.lib.tengo (the lines) and qc_measures.py (the knobs).
// `test/src/qcDefaults.test.ts` asserts both sets against those files.
export const QC_LINE_DEFAULTS = {
  cellBarcodeValidWarn: 0.75,
  cellBarcodeValidError: 0.5,
  readsPerCellWarn: 5000,
  aggregateBarcodeWarn: 0.05,
  aggregateBarcodeError: 1.0,
  undeclaredBarcodeWarn: 0.5,
  undeclaredBarcodeError: 1.0,
  usableReadWarn: 0.2,
  usableReadError: 0.0,
} as const;

export const AGGREGATE_DETECTION_DEFAULTS = {
  aggregateBarcodeIqrMultiplier: 3.0,
  aggregateBarcodeMinUmiThreshold: 1000.0,
  aggregateBarcodeTopN: 100,
} as const;

// The punchcard's frame is keyed on the clonotype set alone. Each identity is a COLUMN and not an axis
// value. A (set, identity) frame cannot give a table that shape. The identity travels in the column's
// DOMAIN, so the model reads a column's identity without parsing a label.
//
// One column per identity. Its value carries the state and both support counts together. See
// identityPunchImportSpec. A grid pairs one column's cell with another's by position alone, and no import
// guarantees that position.
//
// The UI identifies a punch column by two values: the column name, and the domain key that carries the
// identity. It reads both from the spec the grid returns on `colDef.context`. Never identify a punch column
// by its column id. `substituteSpecialCharacters` mangles an id, and a substring test lets `SpikeWT` match
// `SpikeWT_alt` and name the wrong antigen.
export const PUNCH_COLUMN_NAME = "pl7.app/antigen/identityPunch";
export const PUNCH_IDENTITY_DOMAIN = "pl7.app/antigen/identityId";
// The by-cell face's punch column. It carries the same identity domain key, so one column-matching helper
// serves both cards. Only the column NAME separates a set's verdict from a cell's own reading.
export const CELL_PUNCH_COLUMN_NAME = "pl7.app/antigen/cellPunch";
// The clonotype's cell count. The punchcard's own frame carries it, so the grid can read it. A block's own
// exports are not in its own result pool. The copy in the exported setCounts family is therefore unreachable
// here.
export const PUNCH_CELL_COUNT_COLUMN = "pl7.app/antigen/cellCount";

// How each comparator choice reads to a user, and the single place that wording lives. The Python enum, the
// run-meta JSON and the p-column domain all carry the machine token. A reworded sentence here cannot break a
// branch. These strings match the labels `referenceSources` offers before a run, so a choice keeps its name
// once it has served. User-facing names only. The DATA layer keeps `declared`/`panel`/`none`. Those are
// p-column domain values, and domain is part of column identity.
export const REFERENCE_SOURCE_LABELS: Record<ReferenceSource, string> = {
  declared: "Declared baseline tag",
  panel: "The panel's own readings",
  distribution: "Each tag's own distribution",
};

// The run record emit_verdicts.py writes (result_run_meta.json), read as content. Only the fields the UI
// states back to the user are typed here. The file carries every parameter the reading used.
export type VerdictRunMeta = {
  /**
   * The comparator that SERVED. A request the panel cannot honour degrades to none. Typed as
   * `ReferenceSource` and not as `string`. The value crosses from the Python enum, through the run-meta
   * JSON, into a UI branch.
   */
  referenceChoice: ReferenceSource;
  /**
   * Whether the run established a baseline, and where it did not, why.
   *
   * Only the tag-distribution rung can reach false. Its conditions are properties of the DATA: enough cells
   * in the sample, and counts that separate. A run on that rung proceeds and reports afterwards. The
   * settings refuse the other rungs before the run reads anything.
   *
   * False means the run finished and read no verdicts. The block does not draw the punchcard. It shows the
   * reason in place of the punchcard.
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
   * The two export gates, and each gate's own limit. `emit_verdicts.py` writes all six. The workflow reads
   * the two flags and decides whether to build each frame.
   *
   * The identity limit bounds the pivot's WIDTH, at one p-column per identity. The cell limit bounds its
   * ROWS. The two are different quantities and neither substitutes for the other.
   *
   * Optional: a run record written before these fields existed does not carry them. A page that has the
   * number states it. A page without the number states only that a limit exists.
   */
  identitySummaryEmitted?: boolean;
  identitySummaryLimit?: number;
  cellPunchEmitted?: boolean;
  cellPunchCells?: number;
  cellPunchLimit?: number;
  /** Tags the grouping column said nothing about. Each stands as its own identity, under a bare barcode. */
  tagsWithoutGroupingValue: string[];
  /**
   * How many DISTINCT panels the run carried. One means every sample carried the same tags. How many of a
   * clonotype's cells can answer then does not vary by identity. It is the clonotype's own cell count, which
   * the grid carries beside its name. Optional: a run record written before this field existed does not have
   * it. A reader treats absent as one.
   */
  samplePanelCount?: number;
  /**
   * The read limit the run applied. Absent or null where the run declared none. Present because it is the one
   * signal for "a gate was declared". The readout page shows set-aside cells only then. A gate that set
   * nothing aside must still say so.
   */
  gateThreshold?: number | null;
  /**
   * How many of each clonotype's cells the gate set aside, keyed by set id. Absent where the run declared no
   * gate. SPARSE: a clonotype that lost nothing carries no entry, so an absent key reads as zero. The model
   * parses this record on every render.
   *
   * Set grain, and not a column of the expansion table. A set-aside cell answers nothing at any identity. A
   * per-identity subtraction would imply a per-identity failure that did not happen.
   */
  cellsSetAsideBySet?: Record<string, number>;
  /**
   * Which cell list every figure was computed against.
   *
   * Two runs whose lists came from different sources do not share a denominator. A fraction of cells then
   * carries no meaning. Optional: a run record written before this field existed does not carry it. The three
   * values are the values `emit_verdicts.py` writes. A rename there without a rename here leaves this field
   * undefined rather than failing.
   */
  cellListSource?: CellListSource;
};

/** Where the run's cell list came from. Mirrors `emit_verdicts.py`'s `cell_list_source`. */
export type CellListSource = "cell list" | "clonotype linker" | "none";

// A measurement's status. Three words and no fourth. A measurement carries a status only where a line stands
// behind it. Null covers both cases where none does. The value tells the two apart. A number means computed
// and unjudged. No number means nothing computed it, and the reason says which.
export type QcMeasurementStatus = "OK" | "warn" | "alert";

// One sample-level quality measurement, as emit_verdicts.py writes it into result_qc_by_sample.json.
export type SampleQcMeasurement = {
  /** The measurement id. Stable: it is also a value on the `measurement` axis and a p-column name. */
  id: string;
  /** The readable name, carried beside the id rather than instead of it. */
  label: string;
  /** The number, or null where the run could not compute one. Then `reason` says why, and is never empty. */
  value: number | null;
  /** What went in, carried alongside a number. Null where there is nothing to add. */
  detail: string | null;
  /** Why there is no number. Non-null exactly when `value` is null. */
  reason: string | null;
  /** Null where no line stands behind the measurement, or where there is no value to judge. */
  status: QcMeasurementStatus | null;
  /** What the measurement counts. */
  counts: string;
  /** What a bad value means, where a line exists to make that claim. */
  implies: string | null;
  /**
   * Whether this measurement's status reaches the sample's rollup. False for a measurement whose finding
   * belongs to a reagent and not to the sample it was measured on. A row can therefore carry a status the
   * sample's own tag does not.
   */
  rollsUp: boolean;
};

// One sample's quality report: every sample-level measurement, and the rollup over those that roll up.
export type SampleQcReport = {
  /** The worst status among the measurements that carry one. Null where none did. */
  status: QcMeasurementStatus | null;
  /** How many measurements carried a status. */
  judged: number;
  /** How many were computed with no line to judge them against. */
  unjudged: number;
  /** How many the run could not compute at all. */
  notEvaluated: number;
  /** Every sample-level measurement in declaration order, including the ones nothing computed. */
  measurements: SampleQcMeasurement[];
};

/**
 * Binned count distributions, per sample and tag, and the one edge list every one of them shares.
 *
 * The bins are what a reader judges "do these two humps stand apart" from, so they are drawn from the RAW
 * counts: before the minimum, and with the reference tag kept. A plot read in order to SET the minimum
 * cannot have the minimum already applied to it, and the reference tag is the run's own ambient floor.
 *
 * `edges` holds `weights.length + 1` boundaries, log-spaced, shared across the whole run so a grid of tags
 * can be scanned side by side. Empty where the run carried no counts at all.
 *
 * A tag with no reading in a sample carries NO entry, rather than a list of zeros: an absent tag and a tag
 * whose cells all read low are different findings.
 */
export type TagCountBins = {
  edges: number[];
  bySample: Record<string, Record<string, number[]>>;
  /**
   * The fit's two means and the background's share of cells, at the same (sample, tag) grain as the bins.
   * They travel here rather than through the p-frame beside them, so drawing a grid of panels costs no
   * driver query per panel.
   *
   * Absent for a run read against a declared baseline tag, which fits nothing. Absent for a (sample, tag)
   * the fit could not score, which is a different thing from a fit whose components sit on top of each
   * other -- and the block draws no verdict on either, because no published test separates them.
   */
  fitsBySample: Record<
    string,
    Record<string, { backgroundMean: number; signalMean: number; backgroundWeight: number }>
  >;
  /**
   * The run's own two spreads, each on its own LINEAR edges: `score` and `referenceReading`.
   *
   * Linear, unlike the count bins: a score is a 0-100 scale and a reference reading is read against a gate
   * typed in the same units, so a log axis puts the number being chosen somewhere the reader cannot find.
   *
   * One distribution for the whole run, never per sample, because the cutoff and the gate are each one
   * number for the run — so the plot must show every cell the number will act on. A key is absent where the
   * served rung produces no such quantity: a population baseline yields no score, and a baseline belonging
   * to a tag in a sample gives no cell a reference reading.
   */
  spreads: Record<string, { edges: number[]; weights: number[] }>;
};

// What the software resolves an unset reference source to, restated so the dropdown can say it. Mirrors
// verdict.py resolve_default_source: a declared reagent, else the panel's own readings where the panel is
// big enough, else nothing.
export type ReferenceSourceChoices = {
  /**
   * EVERY rung, always, in ladder order, and every rung selectable. An unmet requirement must not withhold
   * the option.
   */
  options: {
    value: ReferenceSource;
    label: string;
    description: string;
    /** What this rung still needs before it can serve, as a sentence. Undefined once it can. */
    needs?: string;
  }[];
  /**
   * What a run with nothing chosen is answered under, as a sentence. Constant, because nothing derives it.
   */
  fallback: string;
};

// Ordinal step key -> the step a sample is CURRENTLY on once that report has settled. A stepReports entry
// appears when its step finishes, so the furthest present report implies the next running step.
export type SampleStep = "parsing" | "refining" | "counting" | "metrics";
const STEP_AFTER: Record<string, SampleStep> = {
  "1-parse": "refining",
  "2-refine": "counting",
  "3-tagstat": "metrics",
};
const STEP_ORDER = ["1-parse", "2-refine", "3-tagstat"];

// mitool prefixes its progress lines with this marker. The workflow step templates set it through
// MI_PROGRESS_PREFIX. The model reads matching lines for a live per-sample 0-100% bar. ProgressPattern reads
// the stage name, percent and ETA out of one line. Same values as blocks/peptide-extraction.
export const ProgressPrefix = "[==PROGRESS==]";
export const ProgressPattern =
  /(?<stage>[^:]*):(?: *(?<progress>[0-9.]+)%)?(?: *ETA: *(?<eta>.+))?/;

// Per-sample QC metrics as qc_report.py emits them (result_qc.json). The analysisLog output reads them. The
// Main grid's Quality and Read recovery columns read them per sample. See ui/src/results.ts.
export type QcRow = {
  readsTotal: number;
  readsMatched: number;
  matchedFraction: number;
  cellsDetected: number;
  featuresDetected: number;
  totalUniqueUmis: number;
  medianUmisPerCell: number;
  // "" where the run wrote no refine report. qc_report leaves the field blank.
  panelAssignedFraction: number | "";
  // The same blank rule. The value comes from the refine report's CELL step.
  cellBarcodeValidFraction: number | "";
};

// CsvMeta lives in types.ts beside the data field that carries it. It holds the panel's headers, each
// header's distinct values, and its row count. It feeds the barcode and feature column dropdowns. It feeds
// the negative-control dropdown indexed by the chosen feature column. It feeds the duplicate-mapping gate.
// That gate compares distinct barcode values against rowCount, and finds a barcode declared on more than one
// row.

// mitool tag-stat emits these columns: the CELL/FEATURE/UMI tags (see pattern.ts), plus tag-stat's count,
// totalWeight and unique_<UMI> outputs. A user-mapped CSV barcode or feature column that names one of these
// corrupts the join, or stops group_by in per_cell_metrics.py. That script guards it too, but only after the
// full mitool chain has run. args() rejects it here, so the app disables Run up front.
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
// sample axis. The sampleLabels and analysisLog outputs share it. A module helper, because each block output
// is an independent pure function of ctx and cannot read another output.
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
      // Malformed annotation. Do not scope. The full map below applies.
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
  // Restrict to the selected dataset's samples. Where the annotation was absent, return the full map.
  return datasetSampleIds
    ? Object.fromEntries(
        Object.entries(full).filter(([sampleId]) => datasetSampleIds.has(sampleId)),
      )
    : full;
}

// Per-sample QC rows from qcJson. The workflow writes them with saveFileContent as inline JSON content, and
// the model reads them synchronously. Filtered to settled samples: qcJson is the last per-sample step, so a
// present entry means that sample finished. completedSamples, sampleQc and analysisLog share it. Returns []
// where the outputs have not settled. A caller that must tell "not started" apart maps that to undefined.
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

// Tag->feature CSV metadata, or undefined until the UI has read the file. The two column dropdowns, the
// control dropdown and the csvColumnsLoading signal share it.
//
// This function reads the snapshot only while its handle matches the CSV currently picked. That comparison
// is the whole guard against a stale read. Every path that swaps the CSV clears the snapshot. Where a path
// does not clear it, the mismatch makes the metadata absent rather than wrong.
function readCsvMeta(ctx: BlockRenderCtx<BlockArgs, BlockData>): CsvMeta | undefined {
  const snap = ctx.data.csvMetaSnapshot;
  if (snap === undefined) return undefined;
  return snap.handle === ctx.data.tagFeatureCsvHandle ? snap.meta : undefined;
}

// The grouping columns a rule names, in whichever shape the project stored. A project saved before the rule
// took a list carries `column` and not `columns`. This function reads both, so no data migration runs
// against stored projects. Every reader goes through this function.
export function groupingColumns(rule: GroupingRule | undefined): string[] {
  if (rule === undefined || rule.by !== "property") return [];
  if (rule.columns !== undefined) return rule.columns.filter((c: string) => c !== "");
  return rule.column ? [rule.column] : [];
}

// Which rungs this data could serve is display material. It must not decide anything. Never share one helper
// between a display of the rung and a projection of it.

/**
 * The baseline rung this run is answered under: the scientist's choice, and nothing else.
 *
 * The scientist selects among the rungs, and nothing selects for them. There is exactly one place a rung
 * comes from: `data.referenceSource`. Never derive it from what the panel can serve.
 *
 * An unselected run IS refused, and undefined carries that state. `args()` throws on undefined. This function
 * only reports it.
 *
 * A stored choice passes through even where this data cannot serve it. One example is a declared tag whose
 * values were cleared. `args()` then refuses the run, or the software refuses it. Nothing here writes to
 * `data`, so a choice that becomes serviceable again revives on its own.
 */
export function resolveReferenceSource(data: BlockData): ReferenceSource | undefined {
  return data.referenceSource;
}

// A/C/G/T plus N (ambiguous base), case-insensitive.
const isDnaValue = (v: string) => /^[ACGTN]+$/i.test(v);

// Evidence that the chosen barcode-sequence column does NOT hold nucleotide sequences. Undefined where it
// does, and where the CSV meta has not resolved. This function ignores blank cells rather than counting them
// against the column. A trailing empty row is a CSV artefact. A module helper, because a block output cannot
// read another output and two outputs need this. barcodeAlphabetIssue reports it. barcodeMappingIssue stays
// silent while it holds, so the two never give the reader contradictory fixes.
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
  // Name a column that would work, where the CSV has one. The usual mistake is a pick of the identifier
  // column where the sequences are one column over.
  const alternative = meta.columns.find((c) => {
    if (c === barcodeCol || c === ctx.data.featureNameColumn) return false;
    const candidate = clean(meta.valuesByColumn?.[c] ?? []);
    return candidate.length > 0 && candidate.every(isDnaValue);
  });
  return { offenders, checked: checked.length, alternative };
}

// The tag CSV column that appears to name the dataset's samples, or undefined. A CSV is sample-aware where
// the same barcode maps to different features per sample. The tell is a column whose distinct values cover
// the dataset's sample names. This function returns the column whose distinct values are a SUPERSET of the
// dataset sample names. It prefers exact set-equality, then the fewest extra values. It excludes the columns
// already bound to the barcode and feature roles. The suggestedSampleColumn output and barcodeMappingIssue
// share it. Undefined until both the CSV meta and the sample labels resolve.
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
    // Prefer exact set-equality. Among equals, prefer the fewest extra values.
    if (
      best === undefined ||
      (exact && !best.exact) ||
      (exact === best.exact && extra < best.extra)
    )
      best = { col, exact, extra };
  }
  return best?.col;
}

// GraphMaker keeps its own chart configuration here, one entry per plot. A chart's saved state is about that
// chart. `init` and the v4 -> v5 migration share these values. A migrated project opens on the chart a new
// project opens on.
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

// v6 data shape: the v8 shape without the undeclared-barcode grid's state, which arrived with that
// table. Every shape below hangs off V8 rather than off BlockData: they all predate v9, so they all still
// carry the panel-versus-reads grid state that v9 strips.
type BlockDataV6 = Omit<BlockDataV8, "undeclaredBarcodesTableState">;

// v5 data shape: v6 without the reagent grid's state, which arrived with the reagent table.
type BlockDataV5 = Omit<BlockDataV6, "reagentTableState">;

// v4 data shape: v5 without the three GraphMaker states, which arrived with the distribution plots.
type BlockDataV4 = Omit<
  BlockDataV5,
  "scoreDistributionGraphState" | "referenceReadingGraphState" | "fittedBackgroundGraphState"
>;

// v3 data shape: the reading's parameters, with the three grid states the two removed result views owned.
// v4 replaced them with the punchcard's own state. `punchcardIdentities` is dead on the right-hand side of
// the Omit and harmless on the left.
type BlockDataV3 = Omit<BlockDataV4, "punchcardTableState" | "punchcardIdentities"> & {
  verdictTableState: PlDataTableStateV2;
  antigenQcTableState: PlDataTableStateV2;
  panelMismatchTableState: PlDataTableStateV2;
};

// v2 data shape: the preset selector and pattern string, with the dominance-era parameters still present.
// per_cell_metrics.py no longer carries the dominant-feature readout, the off-target designation, or the
// specificity score they fed. Nothing consumes these three fields.
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
  | "minVotingCells"
  | "minAgreement"
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

// v1 data shape, before presets: read geometry was three explicit length fields. v2 replaces them with a
// preset selector and a mitool tag-pattern string. See model/src/pattern.ts and model/src/presets.
type BlockDataV1 = Omit<BlockDataV2, "presetId" | "pattern"> & {
  cellLen: number;
  umiLen: number;
  featureLen: number;
};

const dataModel = new DataModelBuilder()
  .from<BlockDataV1>("v1")
  .migrate<BlockDataV2>("v2", ({ cellLen, umiLen, featureLen, ...rest }) => {
    // The shipped default (16/10/15) maps to the fixed BEAM preset. Any other geometry maps to the generic
    // preset that carries the assembled pattern. Offset 0 is the only layout the v1 UI could express.
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
  // v2 -> v3: the dominance parameters go, and the reading's own parameters arrive. This migration drops the
  // three fields rather than carrying them. A field kept "just in case" still travels in the args hash. It
  // stales the block on an edit that changes no computation. The new numeric parameters carry the shipped
  // defaults, so a migrated project renders the run a fresh project renders. A parameter left undefined here
  // reaches the CLI as its argparse default.
  .migrate<BlockDataV3>(
    "v3",
    ({ dominanceThreshold: _d, offtargetProperty: _p, offtargetValues: _v, ...rest }) => ({
      ...rest,
      countFloor: DEFAULT_COUNT_FLOOR,
      boundCutoff: DEFAULT_BOUND_CUTOFF,
      minVotingCells: DEFAULT_MIN_VOTING_CELLS,
      panelReferenceMinMembers: DEFAULT_PANEL_REFERENCE_MIN_MEMBERS,
      distributionMinCells: DEFAULT_DISTRIBUTION_MIN_CELLS,
      verdictTableState: createPlDataTableStateV2(),
      antigenQcTableState: createPlDataTableStateV2(),
      panelMismatchTableState: createPlDataTableStateV2(),
    }),
  )
  // v3 -> v4: the flat verdict table and the quality-report tables are gone as VIEWS, and the punchcard takes
  // their place. The three grid states go with them rather than being carried. A saved column set or filter
  // is meaningful only against the frame it was saved on. The punchcard's own state starts fresh, on the
  // whole panel.
  //
  // The block still EMITS what the removed pages showed. The verdicts and the run's measurements are both
  // artifacts this block must produce. A dropped view does not release it from producing them.
  //
  // The Run quality page's own grid state is `runQualityTableState`. It is NOT one of the two keys this
  // migration strips. Never reuse a stripped key. A saved column set and filter from a removed view would
  // reappear under a new grid. They were never saved against that grid.
  .migrate<BlockDataV4>(
    "v4",
    ({ verdictTableState: _v, antigenQcTableState: _q, panelMismatchTableState: _m, ...rest }) => ({
      ...rest,
      punchcardTableState: createPlDataTableStateV2(),
    }),
  )
  // v4 -> v5: the three GraphMaker states arrive. `init` seeds a NEW project only. A project created before
  // these keys existed carries none of them. GraphMaker renders nothing where its model is undefined. A
  // migration is the only route that reaches a stored project.
  .migrate<BlockDataV5>("v5", (data) => ({
    ...data,
    ...INITIAL_GRAPH_STATES,
  }))
  // v5 -> v6: the reagent grid's state arrives. `init` seeds a NEW project only, and a `PlAgDataTableV2`
  // bound to an undefined state renders nothing and reports no error.
  .migrate<BlockDataV6>("v6", (data) => ({
    ...data,
    reagentTableState: createPlDataTableStateV2(),
  }))
  // v6 -> v7: the undeclared-barcode grid's state arrives, for the same reason the reagent grid's state
  // arrived one version earlier.
  .migrate<BlockDataV8>("v7", (data) => ({
    ...data,
    undeclaredBarcodesTableState: createPlDataTableStateV2(),
  }))
  // v7 -> v8: the fitted-background plot gains a sample facet. `:default-options` seeds a plot that has no
  // saved state. It never overwrites one. A reader who opened that tab keeps the pooled single-panel chart.
  // The fit runs per (tag, sample), and one panel reads every sample's fits as one population. This migration
  // resets that one plot's state. It leaves the other two alone.
  .migrate<BlockDataV8>("v8", (data) => ({
    ...data,
    fittedBackgroundGraphState: INITIAL_GRAPH_STATES.fittedBackgroundGraphState,
  }))
  // v8 -> v9: the panel-versus-reads view is gone, and its grid state goes with it rather than being
  // carried. The check reported one direction only -- a declared barcode no read carried -- and the reagent
  // table now reads that as `Seen in 0/N`, on the surface the quality view designs for reagent findings.
  // The other direction was structurally unreachable there and has always been the undeclared-barcode
  // table's.
  //
  // Never reuse the stripped key. A saved column set and filter means something only against the frame it
  // was saved on, and that frame no longer exists.
  .migrate<BlockData>("v9", ({ runQualityMismatchTableState: _m, ...rest }) => ({ ...rest }))
  // The reagent frame's axes were reordered to put the tag first, so its leading column is the reagent's
  // name. A stored `columnOrder.orderedColIds` is an explicit list and beats anything the model asks for,
  // so a project saved under the old order would keep showing the panel id first. Reset rather than
  // rewritten: the saved filters and column set were saved against axes that no longer exist in that order.
  .migrate<BlockData>("v10", (data) => ({ ...data, reagentTableState: createPlDataTableStateV2() }))
  .init(() => ({
    runMode: "full" as const, // full run by default. "dry" = read-limited Preview
    // Default preset: the geometry the block shipped with, 10x 5' v2 BEAM (16 / 10 / 15).
    presetId: "tenx-beam",
    cellWhitelist: "", // de-novo CELL correction by default
    defaultBlockLabel: "",
    // The reading's parameters. minAgreement and gateThreshold are absent by design. Both are off by default,
    // and off means absent rather than zero. See the args projection.
    countFloor: DEFAULT_COUNT_FLOOR,
    boundCutoff: DEFAULT_BOUND_CUTOFF,
    minVotingCells: DEFAULT_MIN_VOTING_CELLS,
    panelReferenceMinMembers: DEFAULT_PANEL_REFERENCE_MIN_MEMBERS,
    distributionMinCells: DEFAULT_DISTRIBUTION_MIN_CELLS,
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
    punchcardTableState: createPlDataTableStateV2(),
    // These names avoid the two keys the v3 -> v4 migration strips. Both keys were added before any surviving
    // project was created, so no migration carries them.
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
    // REQUIRED. A run without a V(D)J dataset emits counts, per-cell values and per-sample quality, and no
    // verdicts at all. The panel rung is retired. This projection refuses a project stored under it rather
    // than moving it. `args()` is where a settings-knowable refusal belongs.
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
    // The barcode-sequence and feature-name roles must map to different CSV columns. The Python guards this
    // too, but only after the full mitool chain runs. This check disables Run up front.
    if (data.barcodeSeqColumn === data.featureNameColumn)
      throw new Error("Barcode-sequence and feature-name columns must be different");
    // Reject a CSV column that collides with a reserved tag-stat column, for the same reason as the
    // barcode-not-feature guard above.
    for (const [role, col] of [
      ["Barcode-sequence", data.barcodeSeqColumn],
      ["Feature-name", data.featureNameColumn],
    ] as const) {
      if (RESERVED_TAGSTAT_COLUMNS.has(col))
        throw new Error(
          `${role} column "${col}" collides with a reserved tag-stat column; pick another`,
        );
    }
    // Optional combine-mode column. Its values are per-feature modes ("sum"/"all"), so it must be its OWN CSV
    // column. It must differ from the barcode-sequence and feature-name roles, and it must not be a reserved
    // tag-stat column. The Python guards this too, but only after the mitool chain runs. This check disables
    // Run with a clear message where the column is wrong. One wrong pick is the barcode column of DNA
    // sequences.
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
    // Preview (dry run) needs a read limit. The same up-front gate as mixcr-clonotyping. Disable Run with a
    // clear message. Never start a run with no reads to cap.
    if (data.runMode === "dry" && (data.limitInput == null || data.limitInput < 1))
      throw new Error("Enter a read limit (≥ 1) for Preview mode, or switch to a full run");
    // Read geometry. Resolve the selected preset to its effective pattern. A fixed preset owns the pattern.
    // The generic preset carries it in data.pattern. Validate the pattern loosely, then send the string to
    // the workflow. Loose means only the CELL/UMI/FEATURE tags and the R2 capture must be present.
    // refine-tags and tag-stat reference them by name. mitool receives anything else verbatim.
    const preset = getPreset(data.presetId);
    if (!preset) throw new Error("Select a read-geometry preset");
    const pattern = preset.userConfigurable ? (data.pattern ?? "") : preset.pattern;
    const patternError = validatePattern(pattern);
    if (patternError) throw new Error(patternError);

    // Sample-aware mapping, optional. Where the user chooses a sample column, the per-sample workflow body
    // filters the CSV to its own sample's rows. Send the column name and the sampleId->name snapshot it needs
    // to translate its iteration key. The gesture that sets the column takes that snapshot
    // (MainPage.setSampleColumn). This check requires the snapshot, so a half-set state disables Run.
    const sampleAware = !!data.sampleColumn;
    if (sampleAware) {
      if (!data.sampleLabelSnapshot || Object.keys(data.sampleLabelSnapshot).length === 0)
        throw new Error("Re-select the sample column (sample labels not captured)");
      // Block Run where a dataset sample has no rows in the CSV's sample column. That sample would get no
      // features and no message. The gate reads the snapshots taken when the user picked the column, because
      // args reads data only.
      const csvValues = new Set(data.sampleColumnValues ?? []);
      const missing = Object.values(data.sampleLabelSnapshot).filter((n) => !csvValues.has(n));
      if (missing.length > 0)
        throw new Error(
          `${missing.length} dataset sample(s) have no rows in the tag CSV's "${data.sampleColumn}" column ` +
            `(${missing.slice(0, 5).join(", ")}${missing.length > 5 ? "…" : ""}). ` +
            `Add rows for them, or clear the sample column to use one mapping for all samples.`,
        );
    }

    // A barcode on more than one row with no sample column stops the run. per_cell_metrics.py refuses to map
    // one barcode to two antigens, and it refuses at the END, after every sample is parsed. This check costs
    // the user a second instead of the whole run. Both numbers are snapshots taken when the user picked the
    // barcode column. Absent means the meta had not resolved then, and the gate stays quiet.
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

    // The reading's own parameters. The guard above requires the single-cell V(D)J dataset.
    if (data.countFloor < 0) throw new Error("The count floor cannot be negative");
    if (data.boundCutoff < 0 || data.boundCutoff > 100)
      throw new Error("The bound cutoff is a score between 0 and 100");
    if (data.minVotingCells < 1) throw new Error("At least one cell must vote");
    // A role column names WHERE each tag's role is written. The role values are what mark one. A column named
    // on its own is inert: emit_verdicts.py reads it only under `if args.role_column and reference_values`.
    // The run validates it, records it in the run meta, and changes no number. The run then reads against the
    // panel's own readings while the form states that a baseline tag is declared. A panel that declares no
    // baseline leaves this column blank.
    //
    // A baseline is required. A run without one does not happen.
    //
    // Refused HERE, before the run reads anything. Which rung the scientist chose, and whether a baseline tag
    // is declared, are properties of the settings. The message names the condition that failed.
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
    //
    // This projection does NOT check the panel-size condition, and cannot. That condition needs the count of
    // distinct barcodes, which lives in the CSV metadata. This projection must not read that metadata. See
    // the note on the staging projection below. The software refuses the run instead, and names the same
    // condition. The `referenceSources` output marks the option unserviceable.
    //
    // Nothing checks the cell-count condition before the run, and nothing can. Whether a sample holds enough
    // cells whose counts separate is a property of the DATA. A run on that rung proceeds, and reports
    // afterwards that it established no baseline.
    //
    // Scoped to the DECLARED rung, and that scope is load-bearing. A role column names a baseline tag only
    // for the rung that reads one. Under any other rung it changes nothing, and must not refuse a run. The
    // Role column field is hidden under the other rungs. An unscoped check then refuses a run over a hidden
    // value. The scientist cannot see or clear it.
    if (data.referenceSource === "declared" && data.roleColumn && !data.referenceValues?.length)
      throw new Error(
        `The panel column "${data.roleColumn}" declares each tag's role, but no value of it is marked ` +
          `as the baseline, so the column changes nothing. Under "Baseline value", choose the value that ` +
          `marks it, or change the baseline source.`,
      );
    // Every panel column the verdict settings name, each with the label the user sees. Two different faults
    // are possible, so both checks below walk this same list. Check each grouping column on its own. A
    // grouping may name several columns. A joined string such as "Identity, Channel" matches no panel header.
    // It throws here. It takes the whole block to Limbo, refs and all.
    const named: [string, string | undefined][] = [
      ["Baseline role", data.roleColumn],
      ...groupingColumns(data.grouping).map((c): [string, string] => ["Grouping", c]),
    ];

    // First check: a column the panel reader consumes as a KEY is not a property column. Naming one here ends
    // the run at the exec. emit_verdicts.py raises on a grouping column the panel does not declare. It also
    // raises on a role column wherever role values are set. Where they are NOT set it raises nothing, and the
    // baseline comes from the panel's own readings.
    //
    // The way in is a reassignment of a key column WITHIN one panel file. The settings dropdowns stop
    // offering it. The stored pick survives. The field reads empty while the data holds a value. Checked
    // against data and not against the header snapshot, because a key column IS a real header.
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

    // Second check: a role column or a grouping column the panel does not carry. It ends the whole run at the
    // exec too. The user meets a dead run with no hint of which setting caused it. The check reads the
    // headers snapshotted when the user picked the column, because args reads data only. A panel swap that
    // leaves the pick behind then disables Run with a message that names the column.
    const panelColumns = data.panelColumnSnapshot;
    if (panelColumns?.length) {
      for (const [role, column] of named) {
        if (column && !panelColumns.includes(column))
          throw new Error(
            `The ${role} column "${column}" is not in the uploaded panel file. Select a column from the new panel.`,
          );
      }
    }

    // Contending groups, canonicalised here and not in the editor. The args value is a cache key. The same
    // declaration written in a different order must produce the same string. Otherwise the block goes stale
    // and re-runs the whole reading. A group of fewer than two members is dropped. One identity contends with
    // nothing, and an empty group is the same case.
    const contendingGroups = (data.contendingGroups ?? [])
      .map((group) => [...new Set(group)].sort())
      .filter((group) => group.length > 1)
      .sort((a, b) => a.join(" ").localeCompare(b.join(" ")));

    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvHandle: data.tagFeatureCsvHandle,
      barcodeSeqColumn: data.barcodeSeqColumn,
      featureNameColumn: data.featureNameColumn,
      // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column that gives each
      // feature's mode. sum = OR, the default. all = AND, where a feature is called only when every member
      // barcode fires. Projected only when set, so the workflow default stands otherwise. minUmi is the AND
      // per-barcode "fired" floor, an integer >= 1 with a default of 1. Projected only alongside
      // combineColumn, because the workflow passes --min-umi only with --combine-col.
      ...(data.combineColumn ? { combineColumn: data.combineColumn } : {}),
      ...(data.combineColumn && typeof data.minUmi === "number" && data.minUmi >= 1
        ? { minUmi: Math.round(data.minUmi) }
        : {}),
      // The aggregate-barcode detection knobs. Undefined projects as undefined, and the workflow's own
      // default stands. Passed through raw, and never gated on a positivity check. None of these three is an
      // "off means absent" switch, unlike minAgreement and gateThreshold below.
      aggregateBarcodeIqrMultiplier: data.aggregateBarcodeIqrMultiplier,
      aggregateBarcodeMinUmiThreshold: data.aggregateBarcodeMinUmiThreshold,
      aggregateBarcodeTopN:
        typeof data.aggregateBarcodeTopN === "number"
          ? Math.round(data.aggregateBarcodeTopN)
          : undefined,
      // --- the binding reading ---
      // The dataset anchor. The guard above requires it, so it is always concrete here.
      datasetRef: data.datasetRef,
      // Empty and absent are the same claim for both of these fields. An empty selection projects as absent
      // rather than as "" or []. Two spellings of one request would otherwise be two cache keys.
      //
      // Sent only for the rung that reads one. A project that once chose a role column and later moved to
      // another rung keeps the stale value in `data`. A projection of that value puts it in the argument
      // vector. It changes the cache key. It re-runs the reading for a setting nothing consults.
      roleColumn: data.referenceSource === "declared" ? data.roleColumn || undefined : undefined,
      // Sorted and de-duplicated. The Python reads these values as a set. The same values picked in a
      // different order must not re-run the reading.
      referenceValues:
        data.referenceSource === "declared" && data.referenceValues?.length
          ? [...new Set(data.referenceValues)].sort()
          : undefined,
      // Always concrete, because the software has no default. --reference-source is required there, and
      // nothing below this line picks a rung. An unselected choice reaches the run as "none", which is a rung
      // rather than a refusal. `served_source` only ever drops a rung to none. It never substitutes a
      // different rung, so what this projects is what the run is answered under. The run record carries both.
      referenceSource: resolveReferenceSource(data),
      panelReferenceMinMembers: Math.round(data.panelReferenceMinMembers),
      distributionMinCells: Math.round(data.distributionMinCells),
      countFloor: Math.round(data.countFloor),
      // Always projected. `data.boundCutoff` is a required number, and the guard above holds it between 0 and
      // 100. The "off means absent" rule below applies to minAgreement and gateThreshold alone.
      boundCutoff: data.boundCutoff,
      minVotingCells: Math.round(data.minVotingCells),
      // Off by default, and off means ABSENT. A minimum agreement of 0 passes every majority instead of
      // skipping the check. A gate of 0 sets aside every cell instead of gating none. Both are different
      // claims from "off", so neither projects as zero.
      minAgreement:
        typeof data.minAgreement === "number" && data.minAgreement > 0
          ? data.minAgreement
          : undefined,
      gateThreshold:
        typeof data.gateThreshold === "number" && data.gateThreshold > 0
          ? Math.round(data.gateThreshold)
          : undefined,
      // A rule over declared panel properties, and never a tag->identity map. Absent means one identity per
      // tag, which is the reading's own default. Nothing sends a hand-built { by: "tag" } in its place.
      // Normalised to a list here, so the software receives one shape. The software reads the older `column`
      // too, but every future reader should see a run record that names `columns`.
      grouping:
        data.grouping?.by === "property"
          ? { by: "property" as const, columns: groupingColumns(data.grouping) }
          : data.grouping,
      contendingGroups: contendingGroups.length > 0 ? contendingGroups : undefined,
      // The nine inherited lines. Each undefined projects as undefined, and emit_verdicts.py's own shipped
      // default stands. Passed through raw rather than gated on positivity: 0.0 is a real published threshold
      // (usableReadError) and not an "off" state.
      cellBarcodeValidWarn: data.cellBarcodeValidWarn,
      cellBarcodeValidError: data.cellBarcodeValidError,
      readsPerCellWarn: data.readsPerCellWarn,
      aggregateBarcodeWarn: data.aggregateBarcodeWarn,
      aggregateBarcodeError: data.aggregateBarcodeError,
      undeclaredBarcodeWarn: data.undeclaredBarcodeWarn,
      undeclaredBarcodeError: data.undeclaredBarcodeError,
      usableReadWarn: data.usableReadWarn,
      usableReadError: data.usableReadError,
      // Preview: cap reads in dry mode only. A full run omits the cap. Projected only when dry, so a switch
      // back to full changes the args hash and re-runs on the complete input.
      ...(data.runMode === "dry" && data.limitInput
        ? { limitInput: Math.round(data.limitInput) }
        : {}),
      pattern,
      tags: { cell: CELL_TAG, umi: UMI_TAG, feature: FEATURE_TAG },
      ...(sampleAware
        ? { sampleColumn: data.sampleColumn, sampleLabels: data.sampleLabelSnapshot }
        : {}),
      // CELL whitelist: "" = de-novo CELL correction, with no external whitelist.
      cellWhitelist: data.cellWhitelist ?? "",
      // Optional mitool resource overrides (Advanced Settings). Project positive integers only. A blank or
      // zero field then reaches the workflow defaults (4 CPUs, formula-sized RAM). It sends no meaningless
      // request. It does not stale the block on an empty edit.
      ...(typeof data.perProcessCPUs === "number" && data.perProcessCPUs >= 1
        ? { perProcessCPUs: Math.round(data.perProcessCPUs) }
        : {}),
      ...(typeof data.perProcessMemGB === "number" && data.perProcessMemGB >= 1
        ? { perProcessMemGB: Math.round(data.perProcessMemGB) }
        : {}),
    };
  })
  // Staging depends on the CSV alone. It imports the file and exports the blob, and nothing else.
  // featureNameColumn is NOT a prerun arg, and neither is fbFastqRef. The panel is independent of the FASTQ.
  // A staging key on the FASTQ re-imports the CSV. Every FASTQ change and every PlRef re-resolve triggers
  // that import.
  //
  // THIS PROJECTION MUST NOT GROW TO INCLUDE csvMetaSnapshot, csvImportError, OR ANYTHING DERIVED FROM THEM.
  // The UI reads the panel from the blob this staging exports, and writes the result into csvMetaSnapshot. A
  // data write re-renders staging only where the canonical JSON of THIS projection changes. That comparison
  // in pl-middle-layer's setStates gates renderStagingFor, so today that write cannot re-run staging. Add the
  // snapshot here and it can. The write re-renders staging. Staging re-exports the blob. The export
  // re-triggers the write. A staging re-render calls resetStaging first, so every turn of that loop DISCARDS
  // the uploaded CSV.
  .prerunArgs((data) => ({
    tagFeatureCsvHandle: data.tagFeatureCsvHandle,
  }))
  // Enrichments (.enriches): NOT declared, by design. `.enriches(args => PlRef[])` is for a block that
  // produces columns sharing the key space of a ref it holds. clonotype-browser enriches its inputAnchor, and
  // cell-browser its countsRef. This block introduces a NEW cell/feature key space [sampleId, cellId,
  // featureId] off a FASTQ input. It holds no ref to the downstream VDJ dataset. There is nothing to enrich.
  // VDJ Multiomic Integration discovers these columns under its VDJ anchor through the pl7.app/sc/cellLinker,
  // and not through enrichment.

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
  // The single-cell V(D)J dataset the verdicts are keyed by: columns on [sampleId, scClonotypeKey] flagged as
  // anchors. VDJ Multiomic Integration uses the same query, so the two blocks offer the user the same list.
  // There is no linkerOptions beside it, by design. The cell linker carries pl7.app/isLinkerColumn and tables
  // hide it. A user cannot pick it. The workflow resolves it from this anchor by name.
  .output("datasetOptions", (ctx) =>
    ctx.resultPool.getOptions([
      {
        axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/scClonotypeKey" }],
        annotations: { "pl7.app/isAnchor": "true" },
      },
    ]),
  )
  // The identities the contending-groups editor picks from, live from the uploaded panel. An identity is
  // whatever the grouping rule groups tags by. Under the default per-tag rule it is the tag itself. Under a
  // property rule it is the property's value. The option list is therefore the distinct values of the barcode
  // column, or of the chosen property column. Under the per-tag rule the ids ARE the barcode sequences, and
  // are their own labels. The panel metadata is column-wise and carries no tag->name pairing. Retentive, so
  // the editor does not blank on a rerun.
  //
  // This output exists so that only the USER'S PICKS ever reach data. A watcher that copied this list into
  // data would make the output depend on data derived from it. Two open clients would then race.
  .retentiveOutput("identityOptions", (ctx): { value: string; label: string }[] => {
    const grouped = groupingColumns(ctx.data.grouping);
    // Nothing to offer under a grouping on SEVERAL columns. An identity is then the combination of their
    // values. The prerun CSV meta is column-wise, with no pairing between columns. A cross of the columns
    // invents combinations the panel never declared.
    if (grouped.length > 1) return [];
    const column = grouped[0] ?? ctx.data.barcodeSeqColumn;
    if (!column) return [];
    return (readCsvMeta(ctx)?.valuesByColumn?.[column] ?? []).map((v) => ({ value: v, label: v }));
  })
  // Suggested block label for the sidebar subtitle: "<dataset> / <barcode> - <feature>", derived from the
  // current inputs. Computed here and not in .subtitle, because the subtitle context has no result pool. A UI
  // watchEffect copies this value into data.defaultBlockLabel. Each part is dropped until the user sets it.
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
    // The default subtitle must never render with dots. Periods come from a dotted dataset or file label. The
    // " / " and " - " separators are a slash and a hyphen, so a strip of "." leaves them intact. This line
    // replaces periods with spaces, and collapses the doubles that creates. A subtitle the user types in the
    // sidebar does not pass through this output.
    return parts.join(" / ").replace(/\./g, " ").replace(/ {2,}/g, " ").trim();
  })
  // The panel's column headers, feeding the barcode and feature column dropdowns. Retentive, so the dropdowns
  // do not blank on a rerun. Empty until the block has read the panel.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] =>
    (readCsvMeta(ctx)?.columns ?? []).map((c) => ({ value: c, label: c })),
  )
  // Every panel column's distinct values. The UI reads this when the user picks the sample column, and
  // snapshots that column's values into data. args() gates Run on that snapshot.
  .retentiveOutput(
    "csvValuesByColumn",
    (ctx): Record<string, string[]> => readCsvMeta(ctx)?.valuesByColumn ?? {},
  )
  // Sample-aware mapping sanity check, a UI warning only. args() is the authoritative gate. Where the user
  // chooses a sample column, this output compares its CSV values against the dataset's sample names. It flags
  // dataset samples absent from the CSV, which would get no features. It also flags CSV values that match no
  // dataset sample.
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
    // One line per issue, each rendered on its own line. Missing samples block Run, because args() throws.
    // Extra CSV values are informational, because nothing reads those rows. Counted into a real plural, and
    // not written "sample(s)".
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
  // The tag CSV column that appears to name the dataset's samples, or undefined. The UI offers it as a
  // one-click "use sample-aware mapping" suggestion. Advisory only. The user must still pick it, which is the
  // gesture that snapshots the sample map into data. This output never writes data. It excludes the columns
  // already bound to the barcode and feature roles. See suggestSampleColumn for the rule.
  .retentiveOutput("suggestedSampleColumn", (ctx): string | undefined => suggestSampleColumn(ctx))
  // Alphabet check on the chosen barcode-sequence column, a UI warning only. mitool guards the same
  // condition, but by failing refine-tags in the middle of the run.
  //
  // A panel CSV often carries BOTH an identifier column and the nucleotide column: "Barcode" holds T0100 and
  // "Sequence" holds CGATGCCGGACGATC. That choice writes a panel.txt of non-nucleotide strings. The run then
  // fails several stages later, after the reads are parsed. It fails inside barcode correction. The message
  // is "Error while loading sequence set from ./panel.txt", with a Java stack trace.
  //
  // The args guard cannot catch this. It sees `data` only, and the values live in the prerun CSV meta. Not
  // gated on sampleColumn, by design. A per-sample filter narrows which rows reach the panel, and never turns
  // an identifier into a sequence.
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
  // Duplicate-barcode detection at config time, a UI warning only. The Python guards it authoritatively at
  // the end of the run. This output fires under four conditions. The user uploaded a CSV. The user chose the
  // barcode column. The user set no sample column. That barcode column has fewer distinct values than the CSV
  // has data rows. Some barcode then maps on more than one row, which fans the per-cell join and doubles
  // molecule counts. The message names the fix: set the Sample column, with the likely one suggested, or
  // remove the duplicate rows. Skipped where rowCount is absent, which leaves the check to the Python guard.
  .retentiveOutput("barcodeMappingIssue", (ctx): string | undefined => {
    if (!ctx.data.tagFeatureCsvHandle) return undefined;
    const barcodeCol = ctx.data.barcodeSeqColumn;
    if (!barcodeCol) return undefined;
    if (ctx.data.sampleColumn) return undefined; // already sample-aware: the per-sample filter fixes it
    // Silent while the column holds no sequences at all. "Some barcode sits on two rows" would direct the
    // reader to the sample column. The fault is in the barcode column itself.
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
  // The case the two checks either side of this one cannot see. The panel CSV IS sample-keyed. No sample
  // column is set. No repeated barcode gives it away. `barcodeMappingIssue` needs a duplicate barcode, which
  // a fully disjoint panel never supplies. A disjoint panel stains sample A with one set and sample B with
  // another. `sampleMappingWarning` validates a column the user has chosen, and returns nothing where the
  // user has chosen none.
  //
  // The tell is a column whose values cover every dataset sample, which is what `suggestSampleColumn` looks
  // for. Guarded against `barcodeMappingIssue`'s condition, so the two never fire together. That one is the
  // louder problem, and already names this fix.
  //
  // Read as one panel, every sample is offered every antigen. An antigen a sample was never stained with then
  // reads NOT BOUND instead of NEVER ASKED. That is the collapse of a non-answer into a negative. The
  // four-state verdict exists to prevent it. Nothing else on the page reports it.
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
  // Total data rows in the panel, so the UI can snapshot it alongside the barcode column's distinct count.
  // args() needs those two numbers to refuse a duplicate mapping.
  .retentiveOutput("csvRowCount", (ctx): number | undefined => readCsvMeta(ctx)?.rowCount)
  // True while the user has picked the panel and the block has not read it. The handle is set, and no
  // snapshot matches it. A local pick closes this window within a tick. A remote pick holds it until the
  // upload lands and the UI parses the exported blob. It lets the UI show a "reading columns..." state
  // instead of silent empty dropdowns. NOT retentive: it must report the live loading state, including on a
  // CSV swap.
  .output(
    "csvColumnsLoading",
    (ctx): boolean => !!ctx.data.tagFeatureCsvHandle && readCsvMeta(ctx) === undefined,
  )
  // Drives the tag->feature CSV upload. getImportProgress() registers the import handle with the middle-layer
  // upload driver, which pushes the CSV bytes. isActive keeps it computing while the user is not viewing the
  // block. Without this output the CSV never uploads, and every per-sample body hangs on __extra_tagsCsv.
  // Mirrors immune-assay-data index.ts and samples-and-data.
  .output(
    "tagFeatureCsvImportHandle",
    (ctx) => ctx.outputs?.resolve("tagFeatureCsvImportHandle")?.getImportProgress(),
    { isActive: true },
  )
  // The same upload driver, resolved from the PRERUN (staging) render, which is the render that fires before
  // Run. The prerun reads the uploaded CSV and populates the CSV-derived dropdowns (csvColumnOptions,
  // controlOptions), and args() REQUIRES their values. The main driver above fires only once args() passes.
  // On its own it deadlocks: no upload, empty dropdowns, args() throws, no main render, no upload. A
  // staging-driven upload breaks the cycle. Mirrors samples-and-data.
  .output(
    "tagFeatureCsvImportHandlePrerun",
    (ctx) =>
      ctx.prerun
        ?.resolve({ field: "tagFeatureCsvImportHandle", allowPermanentAbsence: true })
        ?.getImportProgress(),
    { isActive: true },
  )
  // The uploaded tag->feature CSV as a downloadable blob handle, resolved from the PRERUN's csvFile export.
  // It lets the UI read the CSV's bytes for a REMOTE (index://) pick. The UI cannot read the user's disk. The
  // same client-side parser then runs on these bytes. The prerun already exports csvFile to make staging
  // demand the blob, so this output adds no work to the workflow. `traverse` rather than `resolve`, because
  // it does not assert a field type.
  .output("csvFileHandle", (ctx) => ctx.prerun?.traverse({ field: "csvFile" })?.getFileHandle())
  // True while the main run executes and no output or context field has settled. Drives the block spinner
  // through the app.ts progress callback.
  .output("isRunning", (ctx) => ctx.outputs?.getIsReadyOrError() === false)
  // True once the main workflow starts producing outputs (ctx.outputs settles). The Main page then replaces
  // the static "run the block" hint with the live per-sample progress grid.
  .output("started", (ctx) => ctx.outputs !== undefined)
  // Per-sample current step, derived from which stepReports entries have settled. A report appears when its
  // step finishes.
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
  // Per-[sampleId, step] live log handles: the parse, refine and tag-stat stdout streams. The per-sample Logs
  // tab (PlLogView) binds them, so the user reads each mitool step's output as it runs. A no-match sample
  // carries its 1-parse entry alone, so the map key set varies. See fb-refine-tagstat.
  .output("stepLogs", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(ctx.outputs.resolve("stepLogs"), (acc) => acc.getLogHandle(), false)
      : undefined,
  )
  // Per-[sampleId] log handle for the Python per-cell-metrics step (the "4-metrics" step). Surfaced apart
  // from stepLogs, because the workflow produces it after it builds the mitool stepLogs map. The UI's
  // per-step Logs panel reads it when the user selects the "4-metrics" step.
  .output("metricsLog", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("metricsLogStream"),
          (acc) => acc.getLogHandle(),
          false,
        )
      : undefined,
  )
  // The Python per-cell-metrics step's live progress, read from the same stream `metricsLog` exposes as a
  // handle. That step is the slowest step on a large run.
  .output("metricsProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("metricsLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // Live per-sample parse progress (0-100%), read from the flat parseLogStream Log. The workflow registers
  // that log the moment the per-sample body runs, and before parse finishes. Mainly an EARLY roster signal:
  // it appears before the stepLogs map fills. stepProgress below carries the per-step bar detail.
  .output("parseProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("parseLogStream"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // Per-[sampleId, step] live progress line for parse, refine and tag-stat, read from each step's stdout
  // stream. Drives the rich per-step text in the grid Progress cell. The text names the tag the step
  // corrects. It names the sort or write phase, and the live percent. ui/src/progress.ts composes these into
  // a MONOTONIC cumulative bar, each step owning a quarter. The bar never resets to zero between steps. Same
  // source as stepLogs.
  .output("stepProgress", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(
          ctx.outputs.resolve("stepLogs"),
          (acc) => acc.getProgressLogWithInfo(ProgressPrefix),
          false,
        )
      : undefined,
  )
  // sampleIds whose per-sample pipeline has finished. qcJson is the LAST per-sample step, and is inline JSON
  // content, so getDataAsJsonOrUndefined reads it synchronously. This set drives the grid's "Done" state. A
  // sample outside this set is still Processing.
  .output("completedSamples", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;
    return parseQcRows(ctx).map((e) => String(e.key[0]));
  })
  // Per-sample QC metrics from qcJson, keyed by sampleId. They drive the Main grid's Quality and Read
  // recovery columns, derived in ui/src/results.ts. Present per sample once its qc step settles. Same source
  // as completedSamples, so the two columns fill as each sample finishes.
  .output("sampleQc", (ctx): Record<string, QcRow> | undefined => {
    if (ctx.outputs === undefined) return undefined;
    const out: Record<string, QcRow> = {};
    for (const e of parseQcRows(ctx)) out[String(e.key[0])] = e.value as QcRow;
    return out;
  })
  // sampleId -> display name from the upstream pl7.app/label column, for the progress grid's Sample column.
  // Shares resolveSampleLabels with analysisLog. Its own output, because one output cannot read another.
  .output("sampleLabels", (ctx): Record<string, string> | undefined => resolveSampleLabels(ctx))
  // The block's single "Analysis logs", shown in the UI's wide slide-over. Built from the per-sample QC JSON
  // (qcJson), which settles as each sample's qc step finishes:
  //   - while the run is in progress: a live count of samples finished so far ("Processing... N ...")
  //   - once every sample is done: a run-level summary of aggregate reads, panel-assigned share and cells
  //   - with that summary: the name of every sample flagged for detecting no cell barcodes
  // One area, whatever the sample count. Detailed per-sample statistics live on the QC page (qcSummaryTable).
  .output("analysisLog", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;

    // Sample labels (sampleId -> name) from the upstream pl7.app/label column, for flagged samples. Shared
    // resolver with the sampleLabels output.
    const labels = resolveSampleLabels(ctx);
    // Per-sample QC metrics. Each entry appears as that sample's qc step finishes. Shared with
    // completedSamples and sampleQc. qcJson is inline JSON content, read synchronously.
    const entries = parseQcRows(ctx);
    const done = entries.length;
    const running = ctx.outputs.getIsReadyOrError() === false;

    // While the run is in progress: a live count of samples finished so far. No fixed denominator. The block
    // processes only the samples present in its feature-barcode dataset. That set is not reliably known until
    // the run completes. A project-wide total would over-count, and make a finished run look stuck. On a
    // crash the count freezes where it reached, beside the block's error state.
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
    // Zero cells detected is a fact about the sample. Nothing downstream can be computed for it. The
    // panel-assigned fraction is NOT a second condition here. Its complement is the share of reads in
    // barcodes the panel never declares, and that status stays on the barcode. It never becomes a sample's
    // status. It appears on the undeclared-barcode rows under Run quality.
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
  // The Main table is ONE ROW PER CELL [sampleId, cellId]. The collapsed workflow frame carries three
  // per-cell summary columns. They are Max feature UMI count, Max feature fraction, and a "Feature breakdown"
  // string. That string lists every feature as "feature (fraction%, umi)", sorted by descending fraction. The
  // per-feature matrix still reaches the result pool (perCellFeatures) for VDJ Multiomic Integration. This
  // output resolves the workflow's collapsed perCellTable PFrame, and is undefined until the workflow emits
  // it.
  //
  // Uses createPlDataTableV2, which takes the columns directly through getPColumns, and NOT V3. This frame is
  // our OWN self-contained, non-batch processColumn output, and createPlDataTableV3's discovery cannot render
  // it. The object (scoped-sources) form returns undefined under every anchor and maxHops config. The
  // array-columns form runs discoverLabelColumnVariants over the ENTIRE result pool. It hangs forever on the
  // upstream Samples & Data FASTQ File-dataset (no_data:<sndBlock>:pf.dataset.*). V2 takes the columns as
  // given and auto-joins the sampleId label. blocks/peptide-extraction uses the same pattern for the same
  // non-batch processColumn plus samples-and-data setup. retentive keeps the grid from blanking on a
  // recompute. withStatus gives PlAgDataTableV2 the OutputWithStatus envelope it renders loading and error
  // states from.
  .output(
    "perCellTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("perCellTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.tableState);
    },
    { retentive: true, withStatus: true },
  )
  // Per-sample QC summary table: reads parsed and matched, cells and features detected, UMI totals, and the
  // panel-assigned fraction. Uses createPlDataTableV2 like perCellTable, because V2 runs getAllLabelColumns
  // over the result pool and auto-joins the matching sampleId label. A V3 selector of { mode: "enrichment",
  // maxHops: 0 } never traverses to the upstream pl7.app/label column. The sampleId axis then renders the raw
  // sample hash.
  .output(
    "qcSummaryTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("qcSummaryTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.qcSummaryTableState);
    },
    { retentive: true, withStatus: true },
  )
  // Every combined identity the punchcard could show, in the order the workflow gave them. Each identity
  // carries the label the workflow put on its column. Two parts of the card read it, and neither narrows
  // anything. The first is the punch hover. The second is the card's empty state. It tells "the pivot emitted
  // no identity columns" apart from "this run has no rows".
  //
  // Read from the pivot's own columns, and not from the run record's identity list. The pivot is size-gated
  // upstream. A run over a large panel names its identities in the record, and emits no columns at all. The
  // columns list what the punchcard can draw.
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
      // The label the workflow put on the column. For a merged identity that label is the joined name, and
      // not the barcode. The identity itself is a raw sequence under the per-tag grouping.
      const label = c.spec.annotations?.["pl7.app/label"];
      options.push({ value: identity, label: label ?? identity });
    }
    return options;
  })
  // The punchcard: one row per clonotype set, one column per identity. Each cell carries the four-state
  // verdict and the count of cells that answered it. The pivoted shape comes from the workflow, because a
  // table cannot pivot a (set, identity) frame into columns.
  //
  // The whole panel, every time.
  //
  // V3 here, and V2 everywhere else in this block, for a reason particular to this table. V2 cannot build it
  // at all: `Cannot produce a Vec1 with a length of zero`. These columns are keyed on ONE axis,
  // `pl7.app/vdj/scClonotypeKey`, and the result pool holds a label column for exactly that axis. V2
  // discovers the label, the frame's only axis is consumed, and the engine receives an empty key vector. One
  // axis is inherent to a punchcard, so there is nothing to tune. V3's `primaryColumns` form takes the
  // columns as given and runs NO data-column discovery. It never walks the result pool, so it cannot hang on
  // the upstream Samples & Data FASTQ dataset. That hazard is why the other tables here use V2, and why this
  // is not a blanket migration. V3 still resolves label columns for the axes it receives, which is wanted.
  //
  // The SDK deprecates V2 in favour of this call, so the rest of this model's tables will follow. Each one
  // needs its own check against the discovery hazard first.
  .output(
    "punchcardTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      const identityOf = (c: (typeof pCols)[number]) => c.spec.domain?.[PUNCH_IDENTITY_DOMAIN];
      // Every identity the pivot produced, always. Narrowing is the grid's job. PlAgDataTableV2 ships a
      // columns panel and a filters panel. A stored server-side filter cannot disagree with what the column
      // chooser shows.
      const cols = pCols.filter((c) => identityOf(c) !== undefined);
      if (cols.length === 0) return undefined;
      // The clonotype's cell count, beside its name. It carries no identity domain, so the filter above drops
      // it. `primaryColumns` runs no discovery, so a column absent from this list never reaches the grid. It
      // is keyed on the same single axis as the punch columns, which is what makes it safe to add. A column
      // that carried an axis the others lack would widen the join.
      const cellCount = pCols.filter((c) => c.spec.name === PUNCH_CELL_COUNT_COLUMN);
      // Alphabetical by the name a READER sees. The workflow emits these columns sorted by identity. Under
      // the per-tag grouping that identity is the barcode. A panel's names never sort as its sequences do.
      // Numeric collation, so `antigen_9` precedes `antigen_10`. The clonotype column is not among these.
      // `columns: null` adds it separately, so it keeps its place at the front.
      const labelOf = (c: (typeof cols)[number]) =>
        c.spec.annotations?.["pl7.app/label"] ?? (identityOf(c) as string);
      const ordered = [...cols].sort((a, b) =>
        labelOf(a).localeCompare(labelOf(b), undefined, { sensitivity: "base", numeric: true }),
      );
      // Headers carry the identity's full name, and never a truncation. Correct a too-long label where the
      // workflow produces it.
      //
      // Column ORDER comes from the `pl7.app/table/orderPriority` annotation on each spec, and from nothing
      // else. The cell count carries 96000, between the clonotype label's 100000 and the punches' 92000. It
      // lands at position 3: row number, clonotype, cell count, then the identities. A
      // `displayOptions.ordering` rule and this array's own order are BOTH inert here. To fix a column that
      // "renders last", measure first, and measure with `aria-colindex`.
      // `querySelectorAll('[role="columnheader"]')` returns AG Grid's recycled header nodes in an order
      // unrelated to column position.
      return createPlDataTableV3(ctx, {
        primaryColumns: [...cellCount, ...ordered].map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.punchcardTableState,
      });
    },
    { retentive: true, withStatus: true },
  )
  // The clonotype axis id, DERIVED from an emitted column and never written by hand. The page hangs the
  // expansion's row button on this axis, and `isJsonEqual` matches `showCellButtonForAxisId`: exact JSON
  // equality, domain and all. A hand-written `{type, name}` misses the domain this axis carries, matches
  // nothing, and renders no button and no error. Deriving it from the same spec the filter reads also makes
  // the two agree provably.
  .output("clonotypeAxisId", (ctx): AxisId | undefined => {
    const pCols = ctx.outputs
      ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
      ?.getPColumns();
    const axis = pCols?.[0]?.spec.axesSpec[0];
    return axis === undefined ? undefined : getAxisId(axis);
  })
  // The expansion: ONE clonotype's identities, read down the page. It carries cellsBound and the support
  // counts.
  //
  // Reads `antigenVerdictsTable`: the LONG verdicts family at (set, identity) grain, which main.tpl holds
  // open for exactly this. This page therefore costs no workflow change and no second import. Its rows are
  // identities, which is the shape the expansion wants, and the shape a pivot cannot give it.
  //
  // NOT gated on the identity count that gates the card's pivots. That gate exists because a pivot costs a
  // COLUMN per identity. Here an identity costs a ROW, and the block fetches one clonotype's rows only.
  //
  // The filter is pushed down, and not applied afterwards. `createPlDataTableV3` puts it in the PTable def.
  // `createPTableDefV3` wraps the join in a `{type:"filter", predicate}` query node. The engine lowers that
  // node into the data query (pframes-rs `visit_filter`). One clonotype's rows cross the boundary, whatever
  // the run's size.
  .output(
    "expansionTable",
    (ctx) => {
      // Undefined until the reader chooses a clonotype. A table built with no filter is EVERY clonotype's
      // identities at once. No selection, no table.
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenVerdictsTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // The columns the design asks for: identity, state, bound, and could-answer only where the run carried
      // panels that differ. NAMED explicitly, which is the whole correctness of this call.
      // `antigenVerdictsTable` surfaces the entire export frame. Its families are keyed on five different
      // axes: tag, panel, sample, set, and (set, identity). One table over all of them is a malformed join,
      // and the SDK answers `discoverColumns failed` out of `discoverLabelColumns`. That reads as an SDK
      // fault and is not one. Only the (set, identity) family belongs here.
      //
      // The identity's readable name comes FIRST, and it has to be named here rather than left to `columns:
      // null`. That option resolves label columns from the result pool, and this label lives in `exportFb`. A
      // block's own exports are not in its own result pool. Without the name, every row prints the same
      // clonotype with nothing to tell the rows apart.
      //
      // Could-answer is CONDITIONAL. Under one panel it is the clonotype's own cell count at every identity.
      // The grid already carries that count beside its name. A column of it repeats one number down the page.
      //
      // Read from the run RECORD, and not from current args. What panels a run carried is a fact about that
      // run. The comparator follows the same discipline: served, never requested.
      const runMeta = ctx.outputs
        ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
        ?.getDataAsJsonOrUndefined<VerdictRunMeta>();
      // Absent reads as one panel. A run record written before the field existed has no opinion, and one
      // panel is the ordinary case.
      const panelsDiffer = (runMeta?.samplePanelCount ?? 1) > 1;
      // Not-bound is absent by design. A cell's vote is exactly one of bound or not-bound, so a third column
      // is `answered - bound` printed out. It is not in the export either.
      const WANTED = [
        "pl7.app/label",
        "pl7.app/antigen/verdict",
        ...(panelsDiffer ? ["pl7.app/antigen/cellsCouldAnswer"] : []),
        "pl7.app/antigen/cellsAnswered",
        "pl7.app/antigen/cellsBound",
      ];
      // One axis, the identity axis, is what makes `pl7.app/label` a label column rather than a name
      // collision. The frame carries other one-axis labels, for the panel and for the tag. A label on the
      // wrong axis joins nothing. Filtered on the axis, and never trusted by name.
      const identityAxisName = "pl7.app/antigen/identityId";
      const pCols = WANTED.flatMap((name) =>
        frame.filter(
          (c) =>
            c.spec.name === name &&
            (name !== "pl7.app/label" ||
              (c.spec.axesSpec.length === 1 && c.spec.axesSpec[0].name === identityAxisName)),
        ),
      );
      // The identity's name has to be one of the matched columns, and a count cannot prove that. An axis name
      // that drifts at export time makes the label fail to match. The verdict and bound columns still match.
      // The count is still non-zero. The panel renders anonymous rows, with nothing to report the regression.
      if (!pCols.some((c) => c.spec.name === "pl7.app/label")) return undefined;
      // The set axis is the first of the (set, identity) pair, taken from the verdict column and not from
      // `pCols[0]`. The identity label column sorts first and carries the identity axis alone.
      // `pCols[0].spec.axesSpec[0]` hands the filter that axis, and resolves nothing. An axis assembled here
      // would be a lookalike with a different identity, and would filter nothing.
      const verdictCol = pCols.find((c) => c.spec.name === "pl7.app/antigen/verdict");
      // Checked directly, and before `setAxis` is derived from it. The filter below reads `verdictCol.id`,
      // and a check on `setAxis` alone leaves `verdictCol` typed as possibly undefined at that use.
      if (verdictCol === undefined) return undefined;
      const setAxis = verdictCol.spec.axesSpec[0];
      if (setAxis === undefined) return undefined;
      return createPlDataTableV3(ctx, {
        primaryColumns: pCols.map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.expansionTableState,
        // The label column is SUPPLIED here as a primary column, and filtered out of the frame above. Nothing
        // discovers it from a pool. `PlAgDataTableV2` drops any axis that has a label column. It renders the
        // label column in that axis's place, subject to that column's own visibility.
        //
        // Two rules, and the order matters: the first match wins. Both columns the table would show as a name
        // are called `pl7.app/label`. The axis each one labels tells them apart.
        displayOptions: {
          visibility: [
            // The identity's name. This is the row's subject. Required although the spec already annotates
            // "default": the catch-all rule below matches this column too, and the first match wins.
            {
              match: {
                name: "^pl7\\.app/label$",
                axes: [{ name: "^pl7\\.app/antigen/identityId$" }],
                partialAxesMatch: false,
              },
              visibility: "default",
            },
            // Any other label column here labels the CLONOTYPE axis. The panel is about one clonotype, which
            // the reader chose by a click. Optional rather than hidden, so the Columns picker can restore it.
            { match: { name: "^pl7\\.app/label$" }, visibility: "optional" },
          ],
          // Cells-that-answered sits LAST, behind the count it contains. Its annotation puts it at 98000,
          // ahead of cells-that-read-bound at 97500. In this panel the two swap. Overridden here and not in
          // the workflow spec. Those columns are EXPORTS with downstream readers, and a priority is global.
          //
          // This rule only reaches a clonotype the grid has not drawn before.
          // `expansionTableState.stateCache` keeps one grid state PER `sourceId`, and `sourceId` here is the
          // expanded clonotype. Every clonotype opened once has its own frozen `columnOrder.orderedColIds`. A
          // stored order is an explicit list of column ids, and it beats anything the model asks for. A
          // reorder that must reach already-opened clonotypes therefore needs the cache invalidated, the
          // device the v3 -> v4 migration used. Not done here.
          ordering: [{ match: { name: "^pl7\\.app/antigen/cellsAnswered$" }, priority: 90000 }],
        },
        filters: {
          type: "and",
          filters: [
            {
              type: "patternEquals",
              // The FULL axis id, domain included. A dropped domain leaves an id that `remapFilterColumnIds`
              // cannot resolve against the table's columns. The SDK's unresolved-leaf path then calls
              // `console`, which does not exist in the model's QuickJS sandbox. The symptom is
              // `ReferenceError: 'console' is not defined` from deep inside the SDK, naming nothing about the
              // filter. That error means an unresolvable filter column.
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
              // A never-asked position is not a reading. The panel keeps the numbers to the identities the
              // experiment put to these cells. Filtered by the verdict's own value, and not by a count. A
              // bound count of 0 is a real reading and must stay.
              column: { type: "column", id: verdictCol.id },
              value: "never asked",
            },
          ],
        },
      });
    },
    { retentive: true, withStatus: true },
  )
  // The expansion's BY-CELL face: one row per cell of the chosen clonotype, one column per identity. Each
  // position carries the cell's own reading rather than its set's verdict. An `unreliable` on the card is
  // cells that disagree, and nothing but this face shows the disagreement.
  //
  // Filtered on `setId`, which is a COLUMN here and not an axis. The frame is keyed (sampleId, cellId),
  // because that is what a cell is. The clonotype is a property of the row. The filter leaf is therefore a
  // `{type: "column"}` leaf, and `PColumn.id` is the `ColumnUniversalId` it wants. Never a hand-built id.
  //
  // The same push-down as the by-identity face. It matters more here, where the frame's grain is every cell
  // of the run.
  .output(
    "cellExpansionTable",
    (ctx) => {
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenCellReference", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // The set column has to be found before anything else. Without it there is no filter, and an unfiltered
      // table here is every cell in the run against every identity. Absent means the software gated the pivot
      // away, which is a legitimate state and not an error. So no table, and the page states the reason from
      // the run record.
      const setCol = frame.find((c) => c.spec.name === "pl7.app/antigen/cellSetId");
      if (setCol === undefined) return undefined;
      const punchCols = frame.filter((c) => c.spec.name === "pl7.app/antigen/cellPunch");
      if (punchCols.length === 0) return undefined;
      const boundCount = frame.filter((c) => c.spec.name === "pl7.app/antigen/boundIdentities");
      // No ordering rule, by design. The bound count sits immediately right of the axes. Its own annotation
      // priority (95000) outranks every identity column at 94000 and below. The by-identity face carries a
      // rule because there the annotation puts cells-answered in the wrong place.
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
  // The run's own quality report: every declared measurement with its status, and the coverage triple behind
  // it. Where nothing computed a measurement, the report carries the reason. This block must produce the
  // run-level measurements, and it must compute and SHOW every measurement that CAN be computed. Read from
  // `outputs` and not from the exports, because a block's own exports are not in its own result pool.
  //
  // `allowPermanentAbsence` for the same reason punchcardTable needs it. A chosen V(D)J dataset gates the
  // whole verdict stage. On a run without one this field never appears. A resolve that treats a permanent
  // absence as a pending one waits forever. It never returns undefined.
  //
  // A frame with no rows is NOT folded into undefined. Absent means the verdict stage did not run. Empty
  // means it ran and had nothing to report, which for the mismatch check is the good outcome.
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
  // The three distributions the quality readout puts last. They travel as ONE p-frame for GraphMaker, and not
  // as rows in the measurement table. `allowPermanentAbsence` for the same reason the tables above need it.
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
  // One row per (panel, tag, identity), carrying the figures the measurement table holds in long format. A
  // tag that carries two identities takes a row under each. `allowPermanentAbsence` for the same reason the
  // tables above need it.
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
  // Barcodes the reads carried that no panel declares, keyed by sequence. It carries the one status this
  // run's quality surface publishes outside the measurement list. That status is the share of a sample's
  // reads that land in undeclared barcodes. That status is the barcode's, and never rolled into any sample's
  // own. Usually empty, which is the wanted outcome. `allowPermanentAbsence` for the same reason the tables
  // above need it: a chosen V(D)J dataset gates the verdict stage.
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
  // What the reading was answered under. The page states the comparator that SERVED, and not the one that was
  // requested. The software degrades a request it cannot honour. Absent until a run with a V(D)J dataset has
  // produced it.
  .output("verdictRunMeta", (ctx): VerdictRunMeta | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<VerdictRunMeta>(),
  )
  // Every sample-level quality measurement, keyed by sample, with that sample's rolled-up status. The single
  // source for both the Main grid's Quality tag and the sample detail view's Quality Checks tab. The tag and
  // the list beside it cannot disagree about one sample. Absent until a run with a V(D)J dataset has produced
  // it. The verdict step writes the sample report.
  .output("sampleQcReport", (ctx): Record<string, SampleQcReport> | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenSampleQc", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<Record<string, SampleQcReport>>(),
  )
  // The binned count distributions every count plot is drawn from: the fitted-background grid on Run
  // quality, and one sample's own antigen counts per tag on its Visual Report. Read as content, because the
  // chart takes its bins as values. `allowPermanentAbsence` for the same reason the tables need it: a
  // chosen V(D)J dataset gates the whole verdict stage, so on a run without one this field never appears.
  .output("tagCountBins", (ctx): TagCountBins | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenTagBins", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<TagCountBins>(),
  )
  // The rung the run WILL be answered under, for the settings field to show. The same call `args()` projects,
  // so the field cannot show one rule while the workflow receives another. Keep it that way. Nothing writes
  // back, so there is no hairpin.
  .output("effectiveReferenceSource", (ctx): ReferenceSource | undefined =>
    resolveReferenceSource(ctx.data),
  )
  // Every baseline rung, each with what it still needs. Two rungs remain: a declared reference tag, and a
  // tag's own distribution across the sample's cells. The panel's own readings are retired, and empty
  // droplets need gene expression this block does not read.
  //
  // Whether the declared rung can serve is knowable before a run, from the panel metadata staging already
  // emits. It needs a role column, and values of it that the column carries. The distribution rung's
  // conditions are properties of the DATA, so its description states them and the run reports them.
  .retentiveOutput("referenceSources", (ctx): ReferenceSourceChoices => {
    const meta = readCsvMeta(ctx);
    const roleColumn = ctx.data.roleColumn;
    const roleValues = new Set(roleColumn ? (meta?.valuesByColumn?.[roleColumn] ?? []) : []);
    const declaredTags = (ctx.data.referenceValues ?? []).filter((v) => roleValues.has(v));

    // EVERY rung is offered and every rung is selectable, whether or not it can serve yet. The scientist
    // chooses the rung and then supplies what it needs. `needs` carries what is still missing, and the form
    // shows it against the chosen rung.
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
      // No `needs`. Whether this rung can serve turns on the sample's cell count, and on whether each tag's
      // counts separate. This block has read neither, and the second is answered per tag rather than per run.
      // The conditions live in the description, and the RUN reports which tags fitted and which did not.
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

    // `none` is NOT offered, and there is no fourth option. A baseline is required, and a run without one
    // does not happen. "no baseline" is not a position a scientist can select here. It is a configuration
    // `args()` refuses.

    // What an unselected run is answered under. Nothing falls anywhere, so this states the consequence of an
    // untouched field rather than naming a fallback.
    const fallback = "no baseline -- every verdict that needs one reads unreliable";
    return { options, fallback };
  })
  .title(() => "Feature Barcode Profiling")
  // Standard block-label subtitle. The subtitle render context is args-only. A touch of the result pool or of
  // outputs renders "Invalid subtitle". The dynamic "<dataset> / <barcode> - <feature>" string is derived in
  // the `suggestedBlockLabel` OUTPUT, which HAS the pool. A UI watchEffect copies it into
  // `defaultBlockLabel`. Guard `ctx.data`: it can be undefined before the app parses block storage.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // Main, the per-sample progress grid, is always shown. The result tabs, Per-sample QC and the per-cell
  // results table, appear once the block has produced outputs. An unrun block shows Main alone. ctx.outputs
  // settles when the workflow starts emitting, the same signal as the `started` output.
  .sections((ctx) => {
    const hasRun = ctx.outputs !== undefined;
    return [
      { type: "link" as const, href: "/" as const, label: "Main" },
      ...(hasRun
        ? [
            { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
            { type: "link" as const, href: "/results" as const, label: "Per-cell results" },
            // Shown for every run, including a run with no V(D)J dataset. That run produces no antigen
            // columns, and this page is the only place a user learns why.
            { type: "link" as const, href: "/punchcard" as const, label: "Explore readout" },
            // "Run quality" rather than "QC". That page is per SAMPLE and this one is per run. Shown for
            // every run, including a run with no V(D)J dataset, for the same reason the readout is.
            { type: "link" as const, href: "/antigen-qc" as const, label: "Run quality" },
          ]
        : []),
    ];
  })
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
