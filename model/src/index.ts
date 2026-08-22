import type {
  AxisId,
  BlockRenderCtx,
  InferOutputsType,
  PlDataTableStateV2,
} from "@platforma-sdk/model";
import {
  BlockModelV3,
  createPlDataTableStateV2,
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

// Re-exported so the UI can seed a grid state without a direct @platforma-sdk/model dependency. The ui
// package depends only on ui-vue, which does not carry this factory.
export { createPlDataTableStateV2 } from "@platforma-sdk/model";
export type { PTableKey } from "@platforma-sdk/model";

// The reading's shipped defaults. They restate the Python's own (verdict.py DEFAULT_FLOOR, BOUND_CUTOFF,
// DEFAULT_PANEL_MIN_MEMBERS, DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE, combine.py DEFAULT_MIN_VOTERS) so
// the user can see and change the value that produced a run. Each is a declared default, not a
// calibrated line: nothing published sets any of them.
const DEFAULT_COUNT_FLOOR = 4;
const DEFAULT_BOUND_CUTOFF = 75;
const DEFAULT_MIN_VOTING_CELLS = 1;
// From one preprint, whose own panels held fifty and a hundred members. Nothing validates it lower. It
// GATES rather than tunes: below it, a count compared against a handful of other antigens is not a
// background estimate, so lowering it is a departure rather than a preference. Keep it above the
// fifteen-tag cap of an antibody kit, so such a panel falls to the tag-distribution rung instead.
const DEFAULT_PANEL_REFERENCE_MIN_MEMBERS = 25;
// Both from the study the tag-distribution rung comes from. The first is that study's own bootstrapping
// figure. The second has no published value: the paper shows the trough in a figure and never says how
// deep one must be, so it ships as a declared default a run can move and report moving. Mirrors
// DEFAULT_DISTRIBUTION_MIN_CELLS and DEFAULT_SEPARATION_DEPTH in tag_distribution.py.
const DEFAULT_DISTRIBUTION_MIN_CELLS = 300;
const DEFAULT_DISTRIBUTION_SEPARATION = 0.5;
const DEFAULT_HIGH_REFERENCE_LINE = 100;

// The punchcard's frame is keyed on the clonotype set alone, and each identity is a COLUMN rather than
// an axis value: that is what a punchcard needs, and a (set, identity) frame cannot give it to a table.
// The identity travels in the column's DOMAIN, so the model reads a column's identity without parsing a
// label.
//
// One column per identity. Its value carries the state and both support counts together (see
// identityPunchImportSpec), because a grid pairs one column's cell with another's by position alone,
// which no import guarantees.
//
// The UI identifies a punch column by these two — the column name and the domain key its identity
// travels under — read off the spec the grid hands back on `colDef.context`. Never by the column id: an
// id is mangled by `substituteSpecialCharacters`, and a substring test on it lets `SpikeWT` match
// `SpikeWT_alt` and name the wrong antigen.
export const PUNCH_COLUMN_NAME = "pl7.app/antigen/identityPunch";
export const PUNCH_IDENTITY_DOMAIN = "pl7.app/antigen/identityId";
// The by-cell face's punch column. Same identity domain key, so one column-matching helper serves both
// cards. Only the column NAME separates a set's verdict from a cell's own reading.
export const CELL_PUNCH_COLUMN_NAME = "pl7.app/antigen/cellPunch";
// The clonotype's cell count, carried in the punchcard's own frame so the grid can read it. A block's
// own exports are not in its own result pool, so the copy in the exported setCounts family is
// unreachable from here.
export const PUNCH_CELL_COUNT_COLUMN = "pl7.app/antigen/cellCount";

// How each comparator choice is written for a reader, and the single place that wording lives. The
// Python enum, the run-meta JSON and the p-column domain all carry the machine token, so rewording a
// sentence here cannot break a branch. These strings match the labels `referenceSources` offers before a
// run, so a choice does not change its name once it has served. User-facing names only: the DATA layer
// keeps `declared`/`panel`/`none`, which are p-column domain values. Domain is part of column identity,
// so renaming those would change what every emitted column IS. A label may say "baseline" where the
// data says "reference".
export const REFERENCE_SOURCE_LABELS: Record<ReferenceSource, string> = {
  declared: "Declared baseline tag",
  panel: "The panel's own readings",
  distribution: "Each tag's own distribution",
};

// The run record emit_verdicts.py writes (result_run_meta.json), read as content. Only the fields the UI
// states back to the user are typed here. The file carries every parameter the reading used.
export type VerdictRunMeta = {
  /**
   * The comparator that actually SERVED — a request the panel cannot honour degrades to none. A
   * `ReferenceSource` rather than a bare string: the value crosses from the Python enum through the
   * run-meta JSON into a UI branch, and typing it as `string` is what let a display sentence be used as
   * a control-flow token.
   */
  referenceChoice: ReferenceSource;
  /**
   * Whether the run established a baseline, and where it did not, why.
   *
   * Only the tag-distribution rung can reach false. Its conditions — enough cells in the sample, and
   * counts that actually separate — are properties of the DATA, so a run resting on it proceeds and
   * reports afterwards. The other rungs are refused from the settings before anything is read, so a
   * run that reaches this record was never one of those.
   *
   * False means the run finished and read no verdicts. The punchcard is not drawn, and the reason is
   * shown in its place: a full grid of *unreliable* costs what a real run costs and looks like a
   * result at a glance.
   */
  baselineEstablished: boolean;
  noBaselineReason: string | null;
  /** The comparator that was ASKED for, so a degraded run can say what it lost. */
  referenceSourceRequested: ReferenceSource;
  referenceTags: string[];
  identityCount: number;
  setCount: number;
  cellsAnalysed: number;
  /** Tags the grouping column said nothing about. Each stands as its own identity, under a bare barcode. */
  tagsWithoutGroupingValue: string[];
  /**
   * How many DISTINCT panels the run carried. One means every sample was stained with the same tags,
   * and then how many of a clonotype's cells could answer does not vary by identity — it is the
   * clonotype's own cell count, which the grid carries beside its name. Optional because a run record
   * written before this field existed does not have it, and a reader treats absent as one.
   */
  samplePanelCount?: number;
  /**
   * The read limit the run applied, or absent/null where none was declared. Present here because it is
   * the one signal for "a gate was declared" — `the-explore-readout` shows set-aside cells only then,
   * and a gate that set nothing aside must still say so rather than look like no gate at all.
   */
  gateThreshold?: number | null;
  /**
   * How many of each clonotype's cells the gate set aside, keyed by set id. Absent when no gate was
   * declared, and SPARSE — a clonotype that lost nothing carries no entry, so an absent key reads as
   * zero. Sparse because this record is parsed on every render.
   *
   * Set grain, deliberately, and not a column of the expansion table: a set-aside cell answers nothing
   * at any identity, so repeating the subtraction at every position would imply a per-identity failure
   * that did not happen.
   */
  cellsSetAsideBySet?: Record<string, number>;
};

// What the software resolves an unset reference source to, restated so the dropdown can say it. Mirrors
// verdict.py resolve_default_source: a declared reagent, else the panel's own readings where the panel is
// big enough, else nothing.
export type ReferenceSourceChoices = {
  options: { value: ReferenceSource; label: string; description: string }[];
  /** One line per source this panel cannot serve, saying why. */
  unavailable: string[];
  /**
   * What a run with nothing chosen is answered under, as a sentence. Constant now that nothing derives,
   * and kept because the sentence is what a reader needs rather than the token.
   */
  fallback: string;
  /**
   * Set only for the one combination a user is likely to have got wrong: a control feature marked on
   * the Main page while no tag is declared the baseline. `unavailable` already says a baseline is
   * undeclared, but it cannot tell the benign case (no control in the panel at all) from this one,
   * where the user has DEMONSTRATED they have a background control and still wired none of it to the
   * arithmetic. Undefined -> nothing to warn about; the two fields are independent by design and
   * neither is required.
   */
  controlNotBaseline?: string;
};

// Ordinal step key -> the step a sample is CURRENTLY on once that report has settled. A stepReports entry
// appears when its step finishes, so the furthest-present report implies the next running step.
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

// CsvMeta (the panel's headers, each header's distinct values, and its row count) now lives in types.ts
// beside the data field that carries it. It feeds the barcode/feature column dropdowns, the
// negative-control dropdown indexed by the chosen feature column, and the duplicate-mapping gate, which
// compares distinct barcode values against rowCount to spot a barcode declared on more than one row —
// the sample-specific-mapping case per_cell_metrics.py guards at the end of the run.

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
// finished). Shared by completedSamples / sampleQc / analysisLog. Returns [] when outputs have not
// settled. A caller that must tell "not started" apart maps that to undefined itself.
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

// Tag→feature CSV metadata, or undefined until the UI has read the file. Shared by the two column
// dropdowns, the control dropdown, and the csvColumnsLoading signal.
//
// The snapshot is read only while its handle matches the CSV currently picked. That comparison is the
// whole guard against a stale read: every path that swaps the CSV clears the snapshot, and if one ever
// fails to, the mismatch makes the metadata absent rather than wrong.
function readCsvMeta(ctx: BlockRenderCtx<BlockArgs, BlockData>): CsvMeta | undefined {
  const snap = ctx.data.csvMetaSnapshot;
  if (snap === undefined) return undefined;
  return snap.handle === ctx.data.tagFeatureCsvHandle ? snap.meta : undefined;
}

// The grouping columns a rule names, whichever shape it is stored in. A project saved before the rule
// took a list carries `column` rather than `columns`. Reading both here costs one function, where a data
// migration would have to run against every stored project. Every reader goes through this, so nothing
// else needs to know two shapes exist.
export function groupingColumns(rule: GroupingRule | undefined): string[] {
  if (rule === undefined || rule.by !== "property") return [];
  if (rule.columns !== undefined) return rule.columns.filter((c: string) => c !== "");
  return rule.column ? [rule.column] : [];
}

// `referenceRungsAvailable` stood here and is gone with the derivation it fed. Which rungs this data
// could serve is still worth SAYING -- the dropdown's option list says it -- but it must not decide
// anything. A helper shared between a display and a projection is how the deciding crept back in.

/**
 * The baseline rung this run is answered under: the scientist's choice, and nothing else.
 *
 * `what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects
 * for them. A baseline nobody chose is a methodology nobody knows they used, and two runs of one
 * experiment would otherwise be answered by different rules with nobody choosing either. There is
 * exactly one place a rung comes from: `data.referenceSource`. Never derive it from what the panel can
 * serve.
 *
 * An unselected run IS refused, and undefined is what carries that. A baseline is required and a run
 * without one does not happen: verdicts are what this block is for, so a configuration that could
 * produce none is refused rather than run. There is no bottom rung answering every position
 * *unreliable* — that output is honest and useless, costing what a real run costs while looking like a
 * result at a glance. `args()` throws on undefined; this function only reports it.
 *
 * A stored choice is passed through even where this data cannot serve it, such as a declared tag whose
 * values were cleared. The refusal then comes from `args()` where it can see the reason, and from the
 * software otherwise — `served_source` names the condition that failed and never substitutes a rung
 * that would serve, because falling to one would be the block choosing a scientist's methodology.
 * Nothing here writes to `data`, so a choice that becomes serviceable again revives on its own.
 */
export function resolveReferenceSource(data: BlockData): ReferenceSource | undefined {
  return data.referenceSource;
}

// A/C/G/T plus N (ambiguous base), case-insensitive.
const isDnaValue = (v: string) => /^[ACGTN]+$/i.test(v);

// Evidence that the chosen barcode-sequence column does NOT hold nucleotide sequences. Undefined when it
// does, or when the CSV meta has not resolved and the question cannot be answered yet. Blank cells are
// ignored rather than counted against the column: a trailing empty row is a CSV artefact, not evidence
// about the contents. A module helper because a block output cannot read another output, and two need
// this — barcodeAlphabetIssue reports it, and barcodeMappingIssue stays silent while it holds, so the
// two never hand the reader contradictory fixes.
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
  // Name a column that would work, if the CSV has one. The mistake is nearly always "picked the ID
  // column when the sequences are one over", so naming the alternative saves the user a guess.
  const alternative = meta.columns.find((c) => {
    if (c === barcodeCol || c === ctx.data.featureNameColumn) return false;
    const candidate = clean(meta.valuesByColumn?.[c] ?? []);
    return candidate.length > 0 && candidate.every(isDnaValue);
  });
  return { offenders, checked: checked.length, alternative };
}

// The tag CSV column that looks like it names the dataset's samples, or undefined. A CSV is sample-aware
// when the same barcode maps to different features per sample. The tell is a column whose distinct values
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

// v3 data shape: the reading's parameters, with the three grid states the two removed result views owned.
// v4 replaced them with the punchcard's own state. `punchcardIdentities` is dead on the right-hand side
// of the Omit and harmless on the left.
type BlockDataV3 = Omit<BlockData, "punchcardTableState" | "punchcardIdentities"> & {
  verdictTableState: PlDataTableStateV2;
  antigenQcTableState: PlDataTableStateV2;
  panelMismatchTableState: PlDataTableStateV2;
};

// v2 data shape: the preset selector + pattern string, with the dominance-era parameters still on it.
// The dominant-feature readout, the off-target designation and the specificity score they fed are gone
// from per_cell_metrics.py, so nothing consumes these three any more.
type BlockDataV2 = Omit<
  BlockDataV3,
  | "datasetRef"
  | "roleColumn"
  | "referenceValues"
  | "referenceSource"
  | "panelReferenceMinMembers"
  | "distributionMinCells"
  | "distributionSeparation"
  | "countFloor"
  | "boundCutoff"
  | "minVotingCells"
  | "minAgreement"
  | "gateThreshold"
  | "highReferenceLine"
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
    // The shipped default (16/10/15) maps to the fixed BEAM preset. Any other geometry maps to the
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
  // dropped rather than carried, because a field kept "just in case" still travels in the args hash and
  // stales the block on an edit that changes no computation. The new numeric parameters are seeded with
  // the shipped defaults, so a migrated project renders the same run a fresh one would. A parameter left
  // undefined here would reach the CLI as its argparse default, the same number arrived at without
  // anyone choosing it.
  .migrate<BlockDataV3>(
    "v3",
    ({ dominanceThreshold: _d, offtargetProperty: _p, offtargetValues: _v, ...rest }) => ({
      ...rest,
      countFloor: DEFAULT_COUNT_FLOOR,
      boundCutoff: DEFAULT_BOUND_CUTOFF,
      minVotingCells: DEFAULT_MIN_VOTING_CELLS,
      panelReferenceMinMembers: DEFAULT_PANEL_REFERENCE_MIN_MEMBERS,
      distributionMinCells: DEFAULT_DISTRIBUTION_MIN_CELLS,
      distributionSeparation: DEFAULT_DISTRIBUTION_SEPARATION,
      highReferenceLine: DEFAULT_HIGH_REFERENCE_LINE,
      verdictTableState: createPlDataTableStateV2(),
      antigenQcTableState: createPlDataTableStateV2(),
      panelMismatchTableState: createPlDataTableStateV2(),
    }),
  )
  // v3 -> v4: the flat verdict table and the quality-report tables are gone as VIEWS, and the punchcard
  // takes their place. The three grid states go with them rather than being carried: a saved column set
  // or filter is meaningful only against the frame it was saved on, and none of these three frames is on
  // screen. The punchcard's own state starts fresh, on the whole panel — every identity column the pivot
  // produced is drawn, and a reader hides or filters columns in the grid's own panels.
  //
  // What the removed pages showed is still EMITTED: the verdicts and the run's measurements are both
  // artifacts `verdict-block-interface` obliges this block to produce, and dropping a view does not
  // release it from producing them.
  //
  // The Run quality page's two grid states are `runQualityTableState` / `runQualityMismatchTableState`,
  // NOT the two keys stripped here. Never reuse a stripped key: a saved column set and filter from a
  // removed view would reappear under a grid it was never saved against.
  .migrate<BlockData>(
    "v4",
    ({ verdictTableState: _v, antigenQcTableState: _q, panelMismatchTableState: _m, ...rest }) => ({
      ...rest,
      punchcardTableState: createPlDataTableStateV2(),
    }),
  )
  .init(() => ({
    runMode: "full" as const, // full run by default. "dry" = read-limited Preview
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
    distributionMinCells: DEFAULT_DISTRIBUTION_MIN_CELLS,
    distributionSeparation: DEFAULT_DISTRIBUTION_SEPARATION,
    highReferenceLine: DEFAULT_HIGH_REFERENCE_LINE,
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
    punchcardTableState: createPlDataTableStateV2(),
    // No migration adds these two, and none is needed: a field absent from an older project's stored
    // data is filled from these defaults on load. Their names avoid the two keys the v3 -> v4 migration
    // strips — see the comment on those fields in types.ts.
    runQualityTableState: createPlDataTableStateV2(),
    runQualityMismatchTableState: createPlDataTableStateV2(),
  }));

export const platforma = BlockModelV3.create(dataModel)
  .args((data): BlockArgs => {
    if (!data.fbFastqRef) throw new Error("Select the feature-barcode FASTQ");
    if (!data.tagFeatureCsvHandle) throw new Error("Upload the tag→feature CSV");
    if (!data.barcodeSeqColumn) throw new Error("Select the barcode-sequence column in the CSV");
    if (!data.featureNameColumn) throw new Error("Select the feature-name column in the CSV");
    // The barcode-sequence and feature-name roles must map to different CSV columns. The Python guards
    // this too (per_cell_metrics.py), but only after the full mitool chain runs. Rejecting it here
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
    // tag-stat column. Python guards this too, but only after the mitool chain runs. Reject up front so
    // a mis-picked column, such as the barcode column of DNA sequences, disables Run with a clear
    // message instead of failing the pipeline at the end.
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
    // Read geometry: resolve the selected preset to its effective pattern (a fixed preset owns it, the
    // generic preset carries it in data.pattern), validate it loosely, then hand the string to the
    // workflow. Loose means only the CELL/UMI/FEATURE tags and the R2 capture must be present, since
    // refine-tags/tag-stat reference them by name. Anything else goes to mitool verbatim.
    const preset = getPreset(data.presetId);
    if (!preset) throw new Error("Select a read-geometry preset");
    const pattern = preset.userConfigurable ? (data.pattern ?? "") : preset.pattern;
    const patternError = validatePattern(pattern);
    if (patternError) throw new Error(patternError);

    // Sample-aware mapping (optional): when a sample column is chosen, the per-sample workflow body
    // filters the CSV to its own sample's rows. Pass the column name and the sampleId→name snapshot it
    // needs to translate its iteration key. That snapshot is taken on the same gesture that sets the
    // column (MainPage.setSampleColumn). Require it here so a half-set state disables Run.
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

    // A barcode on more than one row with no sample column is not a warning. It is a run that will stop:
    // per_cell_metrics.py refuses to map one barcode to two antigens, and it refuses at the END, after
    // every sample has been parsed. Blocking Run here costs the user a second instead of the whole run.
    // Both numbers are snapshots taken when the barcode column was picked. Absent means the meta had not
    // resolved then, and the gate stays out of the way rather than guessing.
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

    // The reading's own parameters. The single-cell V(D)J dataset is deliberately NOT required: without
    // it the block still emits the tag counts, the per-cell scalars, the panel-versus-reads check and the
    // per-sample QC, none of which need a clonotype set. A missing input narrows what can be answered
    // and nothing more.
    if (data.countFloor < 0) throw new Error("The count floor cannot be negative");
    if (data.boundCutoff < 0 || data.boundCutoff > 100)
      throw new Error("The bound cutoff is a score between 0 and 100");
    if (data.minVotingCells < 1) throw new Error("At least one cell must vote");
    // A role column names WHERE each tag's role is written. The role values are what actually marks one.
    // Named alone the column is inert: emit_verdicts.py reads it only under `if args.role_column and
    // reference_values`, so it is validated, recorded in the run meta, and changes no number. The run
    // then reads against the panel's own readings while the form says a baseline tag is declared — a
    // wrong answer wearing the look of a configured one. Requiring the values costs no expressiveness: a
    // panel that declares no baseline leaves this column blank, the configuration
    // `292-no-declared-reference` protects.
    //
    // A baseline is required, and a run without one does not happen. Verdicts are what this block is
    // for, so a configuration that could produce none is refused rather than run — the alternative
    // being a full punchcard of *unreliable*, which costs what a real run costs and looks like a result
    // at a glance.
    //
    // Refused HERE, before anything is read, because which rung was chosen and whether a baseline tag
    // is declared are properties of the settings. The scientist changes the configuration instead of
    // waiting for a run to tell them, and the message names the condition that failed.
    if (!data.referenceSource)
      throw new Error(
        "Choose what the counts are read against, under “Baseline”. Every verdict is a reading " +
          "against a baseline, so a run without one produces no answers at all. Which baselines this " +
          "panel can serve is listed with each option.",
      );
    if (data.referenceSource === "declared" && !data.roleColumn)
      throw new Error(
        "The declared-baseline option reads every count against one tag marked as the baseline, and no " +
          "panel column is set to say which tag that is. Choose the column under “Column declaring " +
          "each tag’s role”, or pick a different baseline.",
      );
    //
    // The panel-size condition is NOT checked here and cannot be: it needs the count of distinct
    // barcodes, which lives in the CSV metadata, and this projection must not read that (see the
    // note on the staging projection below). The software refuses it instead, naming the same
    // condition, and the `referenceSources` output marks the option unserviceable so a scientist
    // meets it before running rather than after.
    //
    // The cell-count condition is not checked anywhere before the run, and cannot be: whether a
    // sample holds enough cells whose counts separate is a property of the DATA. A run on that rung
    // proceeds and reports afterwards that no baseline could be established.
    //
    // The gate below is about the ROLE COLUMN's VALUES rather than about which rung was chosen.
    if (data.roleColumn && !data.referenceValues?.length)
      throw new Error(
        `The panel column "${data.roleColumn}" declares each tag's role, but no value of it is marked ` +
          `as the baseline, so the column changes nothing. Under "Values that mark the baseline tag", ` +
          `choose at least one value, or clear the role column to read against the panel's own readings.`,
      );
    // Every panel column the verdict settings name, each with the label the user sees. Two different
    // things can be wrong with one of these, so both checks below walk this same list. Check each
    // grouping column on its own: a grouping may name several, and joining them would compare
    // "Identity, Channel" against the panel's headers, match nothing, throw here, and take the whole
    // block to Limbo, refs and all.
    const named: [string, string | undefined][] = [
      ["Baseline role", data.roleColumn],
      ...groupingColumns(data.grouping).map((c): [string, string] => ["Grouping", c]),
    ];

    // First: a column the panel reader consumes as a KEY is not a property column, so naming one here
    // ends the run at the exec. emit_verdicts.py raises on a grouping column the panel does not declare,
    // and on a role column wherever role values are set. Where they are NOT set it raises nothing and
    // the baseline falls back to the panel's own readings — a wrong answer rather than no answer.
    //
    // The way in is reassigning a key column WITHIN one panel file. The settings dropdowns stop offering
    // it, the pick already stored survives, and the field reads empty while the data is not. Checked
    // against data rather than the header snapshot, because a key column IS a real header and the
    // snapshot check below cannot see this case.
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

    // Second: a role column or a grouping column the panel does not carry at all ends the whole run at
    // the exec too, and the user meets that as a dead run with no hint of which setting caused it. The
    // check is against the headers snapshotted when the column was picked — args reads data only — so a
    // panel swap that leaves the pick behind disables Run with a message naming the column instead.
    const panelColumns = data.panelColumnSnapshot;
    if (panelColumns?.length) {
      for (const [role, column] of named) {
        if (column && !panelColumns.includes(column))
          throw new Error(
            `The ${role} column "${column}" is not in the uploaded panel file. Select a column from the new panel.`,
          );
      }
    }

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
      // feature's mode: sum = OR, the default, and all = AND, where a feature is called only when every
      // member barcode fires. Projected only when set, so the workflow default (every feature OR) stands
      // otherwise. minUmi is the AND per-barcode "fired" floor, an integer >= 1 defaulting to 1 in the
      // workflow and Python. Projected only alongside combineColumn, because the workflow passes
      // --min-umi only with --combine-col.
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
      // Always concrete, because the software has no default: --reference-source is required there, and
      // nothing below this line picks a rung. An unselected choice reaches the run as "none", which is a
      // rung rather than a refusal. `served_source` only ever drops a rung to none, never substitutes a
      // different one, so what this sends is what the run is answered under. The run record carries
      // both, so a drop is visible.
      referenceSource: resolveReferenceSource(data),
      panelReferenceMinMembers: Math.round(data.panelReferenceMinMembers),
      distributionMinCells: Math.round(data.distributionMinCells),
      distributionSeparation: data.distributionSeparation,
      countFloor: Math.round(data.countFloor),
      // A switch, so off means ABSENT rather than false — the same rule the two thresholds below
      // follow. It keeps the args vector of every project that never touched it byte-identical.
      minimumAppliesToBaseline: data.minimumAppliesToBaseline === true ? true : undefined,
      boundCutoff: data.boundCutoff,
      minVotingCells: Math.round(data.minVotingCells),
      // Off by default, and off means ABSENT: a minimum agreement of 0 passes every majority instead of
      // skipping the check, and a gate of 0 sets aside every cell instead of gating none. Both are
      // different claims from "off", so neither is projected as zero.
      minAgreement:
        typeof data.minAgreement === "number" && data.minAgreement > 0
          ? data.minAgreement
          : undefined,
      gateThreshold:
        typeof data.gateThreshold === "number" && data.gateThreshold > 0
          ? Math.round(data.gateThreshold)
          : undefined,
      highReferenceLine: Math.round(data.highReferenceLine),
      // A rule over declared panel properties, never a tag→identity map. Absent means one identity per
      // tag, which is the reading's own default, so no hand-built { by: "tag" } is sent in its place.
      // Normalised to a list here so the software receives one shape. It reads the older `column`
      // too, but a run record naming `columns` is what every future reader should see.
      grouping:
        data.grouping?.by === "property"
          ? { by: "property" as const, columns: groupingColumns(data.grouping) }
          : data.grouping,
      contendingGroups: contendingGroups.length > 0 ? contendingGroups : undefined,
      // Preview: cap reads only in dry mode. A full run omits it (all reads). Projected only when dry, so
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
  // Staging depends only on the CSV. It imports the file and exports the blob, and nothing else.
  // featureNameColumn is deliberately NOT a prerun arg, and neither is fbFastqRef: the panel is
  // independent of the FASTQ, so keying staging on it would re-import the CSV on every FASTQ change or
  // PlRef re-resolve.
  //
  // THIS PROJECTION MUST NOT GROW TO INCLUDE csvMetaSnapshot, csvImportError, OR ANYTHING DERIVED FROM
  // THEM. The UI reads the panel from the blob this staging exports and writes the result into
  // csvMetaSnapshot. A data write re-renders staging only when the canonical JSON of THIS projection
  // changes — that comparison in pl-middle-layer's setStates is what gates renderStagingFor — so today
  // that write cannot re-run staging. Add the snapshot here and it can: the write re-renders staging,
  // which re-exports the blob, which re-triggers the write. And because a staging re-render calls
  // resetStaging first, every turn of that loop would discard the uploaded CSV, not merely waste work.
  .prerunArgs((data) => ({
    tagFeatureCsvHandle: data.tagFeatureCsvHandle,
  }))
  // Enrichments (.enriches): intentionally NOT declared. `.enriches(args => PlRef[])` is for a block
  // that produces columns sharing the key space of a ref it holds (clonotype-browser enriches its
  // inputAnchor, cell-browser its countsRef). This block introduces a NEW cell/feature key space
  // [sampleId, cellId, featureId] off a FASTQ input and holds no ref to the downstream VDJ dataset, so
  // there is nothing to enrich. VDJ Multiomic Integration discovers these columns under its VDJ anchor
  // through the pl7.app/sc/cellLinker, not through enrichment.

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
    const grouped = groupingColumns(ctx.data.grouping);
    // Nothing to offer under a grouping on SEVERAL columns. An identity is then the combination of their
    // values, and the prerun CSV meta is column-wise: each column's distinct values, with no pairing
    // between them. Crossing them would invent combinations the panel never declared, and a fabricated
    // identity is worse than none. The editor says so instead of showing a list built from a guess.
    if (grouped.length > 1) return [];
    const column = grouped[0] ?? ctx.data.barcodeSeqColumn;
    if (!column) return [];
    return (readCsvMeta(ctx)?.valuesByColumn?.[column] ?? []).map((v) => ({ value: v, label: v }));
  })
  // Suggested block label for the sidebar subtitle: "<dataset> / <barcode> - <feature>", derived from
  // the current inputs. Computed here (not in .subtitle) because the subtitle context has no result
  // pool. A UI watchEffect copies this into data.defaultBlockLabel. Each part is dropped until set.
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
    // dataset or file label. The " / " and " - " separators are a slash and a hyphen, so stripping "."
    // leaves them intact. Replace periods with spaces and collapse the doubles that creates. A subtitle
    // the user types in the sidebar does not pass through this output, so an override is safe.
    return parts.join(" / ").replace(/\./g, " ").replace(/ {2,}/g, " ").trim();
  })
  // Negative-control dropdown options: the distinct values of the chosen feature-name column, indexed out
  // of the snapshot's valuesByColumn map. Picking the feature column only re-indexes here, because the
  // snapshot already carries every column's values. Retentive avoids a flicker to [] on rerun. Empty
  // until the panel has been read.
  .retentiveOutput("controlOptions", (ctx): { value: string; label: string }[] => {
    const col = ctx.data.featureNameColumn;
    const names = col ? (readCsvMeta(ctx)?.valuesByColumn?.[col] ?? []) : [];
    return names.map((name) => ({ value: name, label: name }));
  })
  // The panel's column headers → the barcode/feature column dropdowns. Retentive so the dropdowns do not
  // blank on rerun. Empty until the panel has been read.
  .retentiveOutput("csvColumnOptions", (ctx): { value: string; label: string }[] =>
    (readCsvMeta(ctx)?.columns ?? []).map((c) => ({ value: c, label: c })),
  )
  // Every panel column's distinct values. The UI reads this when the sample column is picked, to snapshot
  // that column's values into data (args gates Run on them).
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
    // One line per issue (the UI renders each on its own line). Missing samples block Run, because args
    // throws. Extra CSV values are informational, since those rows are never used. Counted into a real
    // plural rather than written "sample(s)": these two lines are read while something is already wrong,
    // and the reader should not have to resolve that form.
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
  // The tag CSV column that looks like it names the dataset's samples, or undefined. The UI offers it as
  // a one-click "use sample-aware mapping" suggestion. Purely advisory: the user must still pick it, the
  // gesture that snapshots the sample map into data, and this output never writes data. Excludes the
  // columns already bound to the barcode and feature roles. See suggestSampleColumn for the rule.
  .retentiveOutput("suggestedSampleColumn", (ctx): string | undefined => suggestSampleColumn(ctx))
  // Alphabet check on the chosen barcode-sequence column, a UI warning only. mitool guards the same
  // condition, but by failing refine-tags in the middle of the run.
  //
  // A panel CSV often carries BOTH an identifier column and the nucleotide column: "Barcode" holds T0100
  // and "Sequence" holds CGATGCCGGACGATC. The identifier column has the name a user is more likely to
  // select, and that choice writes a panel.txt of non-nucleotide strings. The run then fails several
  // stages later, inside barcode correction, with "Error while loading sequence set from ./panel.txt"
  // and a Java stack trace, after the reads are parsed.
  //
  // The args guard cannot catch this: it sees only `data`, and the values live in the prerun CSV meta.
  // The check therefore fires here, at config time, as barcodeMappingIssue does. Deliberately not gated
  // on sampleColumn — a per-sample filter narrows which rows reach the panel, and never turns an
  // identifier into a sequence.
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
  // Duplicate-barcode detection at config time, a UI warning only. The Python guards it authoritatively
  // at the end of the run. Fires when a CSV is uploaded, the barcode column is chosen, no sample column
  // is set, and that barcode column has fewer distinct values than the CSV has data rows. Some barcode
  // then maps on more than one row, which would fan the per-cell join and double molecule counts. Names
  // the fix: set the Sample column, suggesting the likely one, or remove the duplicate rows. Skipped
  // where rowCount is absent, which defers to the Python guard.
  .retentiveOutput("barcodeMappingIssue", (ctx): string | undefined => {
    if (!ctx.data.tagFeatureCsvHandle) return undefined;
    const barcodeCol = ctx.data.barcodeSeqColumn;
    if (!barcodeCol) return undefined;
    if (ctx.data.sampleColumn) return undefined; // already sample-aware — the per-sample filter fixes it
    // Silent while the column holds no sequences at all. "Some barcode sits on two rows" would direct
    // the reader to the sample column. The mistake is in the barcode column itself.
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
  // The case the two checks either side of this one cannot see: a panel CSV that IS sample-keyed, with no
  // sample column set, and no barcode repeated to give it away. `barcodeMappingIssue` needs a duplicate
  // barcode, which a fully disjoint panel — sample A stained with one set, sample B with another — never
  // supplies. `sampleMappingWarning` validates a column that has been chosen and returns nothing when
  // none has. So the one arrangement that fails silently is where the panels share no barcode at all.
  //
  // The tell is a column whose values cover every dataset sample, which is what `suggestSampleColumn`
  // already looks for. Guarded against `barcodeMappingIssue`'s condition so the two never fire together:
  // that one is the louder problem, an ambiguous mapping fans the per-cell join, and it already names
  // this fix.
  //
  // Worth a warning rather than a tooltip because, read as one panel, every sample is offered every
  // antigen. An antigen a sample was never stained with then comes back NOT BOUND instead of NEVER
  // ASKED. That is the collapse of a non-answer into a negative that the four-state verdict exists to
  // prevent, and nothing else on the page would say it happened.
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
  // Total data rows in the panel, so the UI can snapshot it alongside the barcode column's distinct
  // count. Those two numbers are what args() needs to refuse a duplicate mapping.
  .retentiveOutput("csvRowCount", (ctx): number | undefined => readCsvMeta(ctx)?.rowCount)
  // True while the panel has been picked but not yet read: the handle is set and no snapshot matches it.
  // A local pick closes this window within a tick. A remote pick holds it until the upload lands and the
  // UI parses the exported blob. Lets the UI show a "reading columns…" state instead of silent empty
  // dropdowns. NOT retentive: it must report the live loading state, including on a CSV swap.
  .output(
    "csvColumnsLoading",
    (ctx): boolean => !!ctx.data.tagFeatureCsvHandle && readCsvMeta(ctx) === undefined,
  )
  // Drives the tag→feature CSV upload: getImportProgress() registers the import handle with the
  // middle-layer upload driver, so the CSV bytes are pushed. isActive keeps it computing while the block
  // is not being viewed. Without this the CSV never uploads and every per-sample body hangs on
  // __extra_tagsCsv (mirrors immune-assay-data index.ts / samples-and-data).
  .output(
    "tagFeatureCsvImportHandle",
    (ctx) => ctx.outputs?.resolve("tagFeatureCsvImportHandle")?.getImportProgress(),
    { isActive: true },
  )
  // Same upload driver, resolved from the PRERUN (staging) render, which is the one that fires before
  // Run. The prerun reads the uploaded CSV to populate the CSV-derived dropdowns (csvColumnOptions /
  // controlOptions), and args() REQUIRES their values. The main driver above fires only once args()
  // passes, so on its own it deadlocks: no upload → empty dropdowns → args() throws → no main render →
  // no upload. Driving the upload from staging breaks the cycle (mirrors samples-and-data's "Drives
  // prerun file uploads" getImportProgress).
  .output(
    "tagFeatureCsvImportHandlePrerun",
    (ctx) =>
      ctx.prerun
        ?.resolve({ field: "tagFeatureCsvImportHandle", allowPermanentAbsence: true })
        ?.getImportProgress(),
    { isActive: true },
  )
  // The uploaded tag->feature CSV as a downloadable blob handle, resolved from the PRERUN's csvFile
  // export. This is what lets the UI read the CSV's bytes for a REMOTE (index://) pick, where it
  // cannot read the user's disk: the same client-side parser then runs on these bytes instead. The
  // prerun already exported csvFile to make staging demand the blob, so this output adds no work to
  // the workflow. `traverse` is used rather than `resolve` because it does not assert a field type.
  .output("csvFileHandle", (ctx) => ctx.prerun?.traverse({ field: "csvFile" })?.getFileHandle())
  // True while the main run is executing, with no output or context field settled yet. Drives the block
  // spinner through the app.ts progress callback.
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
  // sample carries only its 1-parse entry, so the map key set is variable — see fb-refine-tagstat.
  .output("stepLogs", (ctx) =>
    ctx.outputs !== undefined
      ? parseResourceMap(ctx.outputs.resolve("stepLogs"), (acc) => acc.getLogHandle(), false)
      : undefined,
  )
  // Per-[sampleId] log handle for the Python per-cell-metrics step (the "4-metrics" step). Surfaced
  // separately from stepLogs because it is produced after the mitool stepLogs map is built. The UI's
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
  // Live per-sample parse progress (0–100%), read from the flat parseLogStream Log, registered the
  // moment the per-sample body runs and before parse finishes. Mainly an EARLY roster signal: it appears
  // before the stepLogs map fills. The per-step bar detail comes from stepProgress below.
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
  // JSON content, so getDataAsJsonOrUndefined reads it synchronously. The done-set drives the grid's
  // "Done" state: a sample not in this set is still Processing.
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
  // resolveSampleLabels with analysisLog. Kept as its own output because outputs cannot read one another.
  .output("sampleLabels", (ctx): Record<string, string> | undefined => resolveSampleLabels(ctx))
  // The block's single "Analysis logs" (lines shown in the UI's wide slide-over), built from the
  // per-sample QC JSON (qcJson), which settles incrementally as each sample's qc step finishes:
  //   - while the run is in progress → a live count of samples finished so far ("Processing… N …");
  //   - when every sample is done   → a run-level summary (aggregate reads/panel-assigned/cells +
  //     any samples flagged for a panel-assigned fraction below PANEL_ASSIGNED_FLOOR, by name).
  // One area regardless of sample count. Detailed per-sample stats live on the QC page (qcSummaryTable).
  .output("analysisLog", (ctx): string[] | undefined => {
    if (ctx.outputs === undefined) return undefined;

    // Sample labels (sampleId -> name) from the upstream pl7.app/label column — display names for
    // flagged samples. Shared resolver with the sampleLabels output.
    const labels = resolveSampleLabels(ctx);
    // Per-sample QC metrics. Each entry appears as that sample's qc step finishes (shared with
    // completedSamples / sampleQc). qcJson is inline JSON content read synchronously.
    const entries = parseQcRows(ctx);
    const done = entries.length;
    const running = ctx.outputs.getIsReadyOrError() === false;

    // While the run is in progress → a live count of samples finished so far. No fixed denominator: the
    // block processes only the samples present in its feature-barcode dataset, which is not reliably
    // known until the run completes. A project-wide total would over-count and make a finished run look
    // stuck. On a crash the count freezes where it got to, next to the block's error state.
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
  // The Main table is ONE ROW PER CELL [sampleId, cellId]. The collapsed workflow frame carries the
  // per-cell summary columns: Max feature UMI count, Max feature fraction, and a "Feature breakdown"
  // string listing every feature as "feature (fraction%, umi)" sorted by descending fraction. The
  // per-feature matrix is still exported to the result pool (perCellFeatures, the per-cell export
  // contract) for VDJ Multiomic Integration.
  // This output resolves the workflow's collapsed perCellTable PFrame, undefined until the workflow
  // emits it (guarded by the UI).
  //
  // Uses createPlDataTableV2 (columns passed directly via getPColumns), NOT V3. This frame is our OWN
  // self-contained, non-batch processColumn output, and createPlDataTableV3's discovery cannot render
  // it: the object (scoped-sources) form returns undefined whatever the anchor/maxHops config, and the
  // array-columns form runs discoverLabelColumnVariants over the ENTIRE result pool and hangs forever on
  // the upstream Samples&Data FASTQ File-dataset (no_data:<sndBlock>:pf.dataset.*). V2 takes the columns
  // as-is and auto-joins the sampleId label, the pattern blocks/peptide-extraction uses for the same
  // non-batch processColumn plus samples-and-data setup. retentive avoids blanking the grid on
  // recompute. withStatus feeds PlAgDataTableV2 the OutputWithStatus envelope it renders loading and
  // error from.
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
  // perCellTable, because it runs getAllLabelColumns over the result pool and auto-joins the matching
  // sampleId label. A V3 selector of { mode: "enrichment", maxHops: 0 } never traverses to the upstream
  // pl7.app/label column, and the sampleId axis then renders the raw sample hash.
  .output(
    "qcSummaryTable",
    (ctx) => {
      const pCols = ctx.outputs?.resolve("qcSummaryTable")?.getPColumns();
      if (pCols === undefined || pCols.length === 0) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.qcSummaryTableState);
    },
    { retentive: true, withStatus: true },
  )
  // Every combined identity the punchcard could show, in the order the workflow gave them, each with the
  // label the workflow put on its column. Two things on the card read it, and neither narrows anything.
  // The punch hover reads it, because a reader hovering a dot far down a long grid cannot see the header
  // row. The card's empty state reads it to tell "the pivot emitted no identity columns" apart from
  // "this run has no rows at all".
  //
  // Read from the pivot's own columns, not from the run record's identity list. The pivot is size-gated
  // upstream, so a run over a large panel names its identities in the record and emits no columns at
  // all. Reading the columns lists what the punchcard can actually draw, which is what makes the empty
  // state trustworthy.
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
      // The label the workflow put on the column, which for a merged identity is the joined name rather
      // than the barcode. The identity itself is a raw sequence under the per-tag grouping, so carrying
      // the label is what keeps the punch hover naming an antigen the reader recognises.
      const label = c.spec.annotations?.["pl7.app/label"];
      options.push({ value: identity, label: label ?? identity });
    }
    return options;
  })
  // The punchcard: one row per clonotype set, one column per identity, each cell carrying the four-state
  // verdict and the count of cells that answered it. The pivoted shape comes from the workflow, because a
  // table cannot pivot a (set, identity) frame into columns.
  //
  // The whole panel, every time. A reader who wants fewer columns hides them in the grid's columns panel
  // or filters in its filters panel, and this output second-guesses neither.
  //
  // V3 here, and V2 everywhere else in this block, for a reason particular to this table. V2 cannot build
  // it at all: `Cannot produce a Vec1 with a length of zero`. These columns are keyed on ONE axis,
  // `pl7.app/vdj/scClonotypeKey`, and the result pool holds a label column for exactly that axis (the
  // clonotyping block publishes it). V2 discovers the label, the frame's only axis is consumed, and the
  // engine is handed an empty key vector. One axis is inherent to a punchcard, so there is nothing to
  // tune. V3's `primaryColumns` form takes the columns as given and runs NO data-column discovery, so it
  // never walks the result pool and cannot hang on the upstream Samples & Data FASTQ dataset — the hazard
  // the other tables here chose V2 to avoid, and the reason this is not a blanket migration. V3 still
  // resolves label columns for the axes it was handed, which is wanted: a clonotype row reads better
  // under its clonotype label than under a raw key.
  //
  // V2 is deprecated SDK-side in favour of this call, so the rest of this model's tables will follow.
  // Each needs its own check against the discovery hazard first.
  .output(
    "punchcardTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      const identityOf = (c: (typeof pCols)[number]) => c.spec.domain?.[PUNCH_IDENTITY_DOMAIN];
      // Every identity the pivot produced, always. Narrowing is the grid's job: PlAgDataTableV2 ships a
      // columns panel and a filters panel, so a stored server-side filter cannot disagree with what the
      // column chooser shows.
      const cols = pCols.filter((c) => identityOf(c) !== undefined);
      if (cols.length === 0) return undefined;
      // The clonotype's cell count, beside its name. It carries no identity domain, so the filter above
      // drops it — and `primaryColumns` runs no discovery, so a column not listed here never reaches the
      // grid. It is keyed on the same single axis as the punch columns, which is what makes adding it to
      // this list safe: a column carrying an axis the others lack would widen the join.
      const cellCount = pCols.filter((c) => c.spec.name === PUNCH_CELL_COUNT_COLUMN);
      // Alphabetical by the name a READER sees. The workflow emits these sorted by identity, which under
      // the per-tag grouping is the barcode, and a panel's names never sort the same as its sequences.
      // Numeric collation, so `antigen_9` precedes `antigen_10`. The clonotype column is not among these
      // (`columns: null` brings it in separately), so it keeps its place at the front.
      const labelOf = (c: (typeof cols)[number]) =>
        c.spec.annotations?.["pl7.app/label"] ?? (identityOf(c) as string);
      const ordered = [...cols].sort((a, b) =>
        labelOf(a).localeCompare(labelOf(b), undefined, { sensitivity: "base", numeric: true }),
      );
      // Headers carry the identity's full name, never a truncation: which identity a column is, is the
      // one thing a reader needs from a header. Correct a too-long label where it is produced.
      //
      // Column ORDER comes from the `pl7.app/table/orderPriority` annotation on each spec, and from
      // nothing else. The cell count carries 96000, between the clonotype label's 100000 and the
      // punches' 92000, and lands at position 3: row number, clonotype, cell count, then the identities.
      // A `displayOptions.ordering` rule and this array's own order are BOTH inert here. If you are
      // fixing a column that "renders last", measure first, and measure with `aria-colindex`.
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
  // The clonotype axis id, DERIVED from an emitted column and never written out by hand. The page hangs
  // the expansion's row button on this axis, and `showCellButtonForAxisId` is matched with `isJsonEqual`
  // — exact JSON equality, domain and all. A hand-written `{type, name}` misses the domain this axis
  // carries, matches nothing, and renders no button and no error. Deriving it from the same spec the
  // filter reads also makes the two provably agree: a button on a row whose key the filter cannot
  // resolve is worse than no button.
  .output("clonotypeAxisId", (ctx): AxisId | undefined => {
    const pCols = ctx.outputs
      ?.resolve({ field: "antigenPunchcardTable", allowPermanentAbsence: true })
      ?.getPColumns();
    const axis = pCols?.[0]?.spec.axesSpec[0];
    return axis === undefined ? undefined : getAxisId(axis);
  })
  // The expansion: ONE clonotype's identities, read down. `the-explore-readout` puts this opposite the
  // card — the grid is read across a row to see what a clone bound, and the expansion down to see what
  // those verdicts rest on, which is where a number belongs. A number in every position of the card would
  // compete with reading it across, so this is where cellsBound and the support counts surface.
  //
  // Reads `antigenVerdictsTable`: the LONG verdicts family at (set, identity) grain, held open by
  // main.tpl for exactly this, so this page costs no workflow change and no second import. Its rows are
  // identities, the shape the expansion wants and the shape a pivot cannot give it.
  //
  // NOT gated on the identity count that gates the card's pivots. That gate exists because a pivot costs
  // a COLUMN per identity and sits well under the thousand-plus a pMHC panel carries. Here an identity
  // costs a ROW, and only one clonotype's rows are ever fetched, so a panel too wide for the card is
  // precisely where this view still reads.
  //
  // The filter is pushed down, not applied after the fact: `createPlDataTableV3` puts it in the PTable
  // def, `createPTableDefV3` wraps the join in a `{type:"filter", predicate}` query node, and the engine
  // lowers that into the data query (pframes-rs `visit_filter`). One clonotype's rows are what crosses
  // the boundary, whatever the run's size.
  .output(
    "expansionTable",
    (ctx) => {
      // Undefined until a clonotype is chosen, and that is the point rather than a convenience: a table
      // built with no filter is EVERY clonotype's identities at once, which on a real run is the exact
      // cost this design exists to avoid. No selection, no table.
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenVerdictsTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // The columns the design asks for — identity, state, bound, and could-answer only where the run
      // carried panels that differ — NAMED explicitly, which is the whole correctness of this call.
      // `antigenVerdictsTable` surfaces the entire export frame, whose families are keyed on five
      // different axes: tag, panel, sample, set, and (set, identity). Handing all of them to one table is
      // a malformed join, and the SDK answers `discoverColumns failed` out of `discoverLabelColumns`,
      // which reads as an SDK fault and is not one. Only the (set, identity) family belongs here.
      //
      // The identity's readable name comes FIRST, and it has to be named here rather than left to
      // `columns: null`. That option resolves label columns from the result pool, and this label lives
      // in `exportFb` — a block's own exports are not in its own result pool. Without it every row
      // prints the same clonotype with nothing telling them apart.
      //
      // Could-answer is CONDITIONAL. Under one panel it is the clonotype's own cell count at every
      // identity, which the grid already carries beside its name, so a column of it repeats one number
      // down the page and teaches a reader to ignore it. The whole argument for carrying the number is
      // that a verdict from three cells and one from forty print the same word, and a number that never
      // varies defeats that argument.
      //
      // Read from the run RECORD, not from current args: what panels a run carried is a fact about that
      // run, the same discipline the comparator uses (served, never requested).
      const runMeta = ctx.outputs
        ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
        ?.getDataAsJsonOrUndefined<VerdictRunMeta>();
      // Absent reads as one panel: a run record written before the field existed has no opinion, and one
      // panel is the ordinary case 206 calls the default.
      const panelsDiffer = (runMeta?.samplePanelCount ?? 1) > 1;
      // Not-bound is deliberately absent. A cell's vote is exactly one of bound or not-bound, so a third
      // column is `answered - bound` printed out, and a reader who wants it subtracts two numbers already
      // on the row. It stays in the EXPORT, which has its own readers. Only this panel drops it.
      const WANTED = [
        "pl7.app/label",
        "pl7.app/antigen/verdict",
        ...(panelsDiffer ? ["pl7.app/antigen/cellsCouldAnswer"] : []),
        "pl7.app/antigen/cellsAnswered",
        "pl7.app/antigen/cellsBound",
      ];
      // One axis, the identity axis, is what makes `pl7.app/label` a label column rather than a name
      // collision — the frame carries other one-axis labels (the panel's, the tag's), and a label on the
      // wrong axis would join nothing. Filtered on the axis rather than trusted by name.
      const identityAxisName = "pl7.app/antigen/identityId";
      const pCols = WANTED.flatMap((name) =>
        frame.filter(
          (c) =>
            c.spec.name === name &&
            (name !== "pl7.app/label" ||
              (c.spec.axesSpec.length === 1 && c.spec.axesSpec[0].name === identityAxisName)),
        ),
      );
      // The identity's name has to be one of them, and a count is not enough to know that. Where only the
      // label fails to match — an axis name drifting at export time is all it takes — the verdict and
      // bound columns still match, the count is still non-zero, and the panel renders anonymous rows with
      // nothing to say it regressed. No panel is a visible failure. A nameless one reads as working. The
      // verdict column is required too, and the setAxis guard below catches its absence.
      if (!pCols.some((c) => c.spec.name === "pl7.app/label")) return undefined;
      // The set axis is the first of the (set, identity) pair, taken from the verdict column and not from
      // `pCols[0]`: the identity label column sorts first and carries only the identity axis, so
      // `pCols[0].spec.axesSpec[0]` would hand the filter that axis and resolve nothing. An axis
      // assembled here would be a lookalike with a different identity and filter nothing.
      const verdictCol = pCols.find((c) => c.spec.name === "pl7.app/antigen/verdict");
      // Checked directly, and before `setAxis` is derived from it: the filter below reads `verdictCol.id`,
      // and narrowing only `setAxis` would leave `verdictCol` typed as possibly undefined at that use.
      if (verdictCol === undefined) return undefined;
      const setAxis = verdictCol.spec.axesSpec[0];
      if (setAxis === undefined) return undefined;
      return createPlDataTableV3(ctx, {
        primaryColumns: pCols.map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.expansionTableState,
        // The identity's own name, made visible. `identityLabelsImportSpec` annotates it hidden, the
        // right convention for a `pl7.app/label` column: a table CONSUMES a label column to name its axis
        // rather than rendering it as data. That convention fails here for one reason — the identity axis
        // is invented by this block, so its label column can never sit in this block's own result pool,
        // and the pool is the only place the table looks.
        //
        // Overridden here rather than in the workflow spec, because that column is an EXPORT with
        // downstream readers, and making it default-visible would change their tables to fix ours. This
        // is how clonotype-browser adjusts a column it did not annotate. Two rules, and the order
        // matters — the first match wins. Both columns the table would show as a name are called
        // `pl7.app/label`, so they are told apart by the axis each one labels.
        displayOptions: {
          visibility: [
            // The identity's name, promoted out of hidden. This is the row's subject.
            {
              match: {
                name: "^pl7\\.app/label$",
                axes: [{ name: "^pl7\\.app/antigen/identityId$" }],
                partialAxesMatch: false,
              },
              visibility: "default",
            },
            // Any other label column here labels the CLONOTYPE axis, and the panel is about one
            // clonotype, which the reader chose by clicking it. Printing its name down every row is
            // repetition. Optional rather than hidden, so the Columns picker can bring it back.
            { match: { name: "^pl7\\.app/label$" }, visibility: "optional" },
          ],
          // Cells-that-answered sits LAST, behind the count it contains. Its annotation puts it at 98000,
          // ahead of cells-that-read-bound at 97500, which is the right default everywhere else: a
          // denominator reads before the number it divides. In this panel the bound count is what the
          // reader came for and the answered count is the context, so the two swap. Overridden here
          // rather than in the workflow spec because those columns are EXPORTS with downstream readers,
          // and a priority is global.
          //
          // This rule only reaches a clonotype the grid has not drawn before.
          // `expansionTableState.stateCache` keeps one grid state PER `sourceId`, and `sourceId` here is
          // the expanded clonotype, so every clonotype opened once has its own frozen
          // `columnOrder.orderedColIds`. A stored order is an explicit list of column ids and it beats
          // anything the model asks for. A reorder that has to reach already-opened clonotypes therefore
          // needs the cache invalidated, the device the v3 -> v4 migration used when the frames under
          // those grids changed. Not done here: the order is a preference, and resetting every reader's
          // saved columns and filters to move one column right is the more expensive mistake.
          ordering: [{ match: { name: "^pl7\\.app/antigen/cellsAnswered$" }, priority: 90000 }],
        },
        filters: {
          type: "and",
          filters: [
            {
              type: "patternEquals",
              // The FULL axis id, domain included. Dropping the domain leaves an id that
              // `remapFilterColumnIds` cannot resolve against the table's columns, and the SDK's
              // unresolved-leaf path calls `console`, which does not exist in the model's QuickJS
              // sandbox. The symptom is then `ReferenceError: 'console' is not defined` from deep inside
              // the SDK, naming nothing about the filter. That error means an unresolvable filter
              // column, not a logging problem.
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
              // A never-asked position is not a reading, and 206 keeps the numbers to the identities the
              // experiment actually put to these cells. Filtered by the verdict's own value rather than
              // by a count, because a bound count of 0 is a real reading and must stay.
              column: { type: "column", id: verdictCol.id },
              value: "never asked",
            },
          ],
        },
      });
    },
    { retentive: true, withStatus: true },
  )
  // The expansion's BY-CELL face: one row per cell of the chosen clonotype, one column per identity,
  // carrying that cell's own reading rather than its set's verdict. This is where a reader sees WHY a
  // verdict came out as it did: an `unreliable` on the card is cells disagreeing, and nothing but this
  // shows the disagreement.
  //
  // Filtered on `setId`, a COLUMN here rather than an axis. The frame is keyed (sampleId, cellId) because
  // that is what a cell is, and the clonotype is a property of the row. So the filter leaf is a
  // `{type: "column"}` one, and `PColumn.id` is the `ColumnUniversalId` it wants. Never a hand-built id,
  // the same discipline the axis filter above follows.
  //
  // Same push-down as the by-identity face: `createPlDataTableV3` puts the filter in the PTable def and
  // the engine lowers it into the data query, so one clonotype's cells are what crosses the boundary.
  // That matters more here, where the frame's grain is every cell of the run.
  .output(
    "cellExpansionTable",
    (ctx) => {
      const chosen = ctx.data.expandedSet;
      if (chosen === undefined || chosen.length === 0) return undefined;
      const frame = ctx.outputs
        ?.resolve({ field: "antigenCellReference", allowPermanentAbsence: true })
        ?.getPColumns();
      if (frame === undefined || frame.length === 0) return undefined;
      // The set column has to be found before anything else: without it there is no filter, and an
      // unfiltered table here is every cell in the run against every identity. Absent means the software
      // gated the pivot away, a legitimate state and not an error. So no table, and the page says why
      // from the run record.
      const setCol = frame.find((c) => c.spec.name === "pl7.app/antigen/cellSetId");
      if (setCol === undefined) return undefined;
      const punchCols = frame.filter((c) => c.spec.name === "pl7.app/antigen/cellPunch");
      if (punchCols.length === 0) return undefined;
      const boundCount = frame.filter((c) => c.spec.name === "pl7.app/antigen/boundIdentities");
      // No ordering rule, deliberately. The bound count sits immediately right of the axes because its
      // own annotation priority (95000) outranks every identity column (94000 and down), and that is
      // where it belongs: it is the one number that summarises the row, and a matrix a hundred columns
      // wide puts its far edge off screen. The by-identity face carries a rule only because there the
      // annotation put cells-answered in the wrong place. Here the annotation is right.
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
  // The run's own quality report: every declared measurement with its status, the coverage triple behind
  // it, and — where nothing computed it — the reason it was deferred. This block is obliged to produce the
  // run-level measurements, and the obligation is that every measurement that CAN be computed is computed
  // and SHOWN. Read from `outputs` and not from the exports, because a block's own exports are not in its
  // own result pool.
  //
  // `allowPermanentAbsence` for the same reason punchcardTable needs it: the whole verdict stage is gated
  // on a V(D)J dataset being picked, so on a run without one this field never appears, and a resolve that
  // treats a permanent absence as a pending one waits forever instead of returning undefined for the page
  // to explain.
  //
  // A frame with no rows is deliberately NOT folded into undefined. Absent means the verdict stage did
  // not run. Empty means it ran and had nothing to report, which for the mismatch check is the good
  // outcome. Collapsing the two would leave the page unable to tell them apart.
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
  // The panel-versus-reads check: every barcode the panel declared that no read carried, and every barcode
  // the reads carried that the panel never declared. Both directions are in the one frame, told apart by
  // the direction column, which carries a discrete filter so either half is reachable on its own. A
  // mismatch report the user cannot see defeats its purpose, which is why the workflow emits it into
  // `outputs` and not only into the exports.
  .output(
    "runQualityMismatchTable",
    (ctx) => {
      const pCols = ctx.outputs
        ?.resolve({ field: "antigenPanelMismatchTable", allowPermanentAbsence: true })
        ?.getPColumns();
      if (pCols === undefined) return undefined;
      return createPlDataTableV2(ctx, pCols, ctx.data.runQualityMismatchTableState);
    },
    { retentive: true, withStatus: true },
  )
  // What the reading was actually answered under. The page states the comparator that SERVED rather than
  // the one that was requested, because the software degrades a request it cannot honour and a reader
  // meeting an all-unreliable table otherwise has no way to learn that happened. Absent until a run with a
  // V(D)J dataset has produced it.
  .output("verdictRunMeta", (ctx): VerdictRunMeta | undefined =>
    ctx.outputs
      ?.resolve({ field: "antigenRunMeta", allowPermanentAbsence: true })
      ?.getDataAsJsonOrUndefined<VerdictRunMeta>(),
  )
  // The rung the run WILL be answered under, for the settings field to show. The same call `args()`
  // projects, so the field cannot show one rule while the workflow receives another. Keep it that way:
  // the last divergence between a shown rung and a sent one came from two copies of one rule. The UI
  // reads it the way it reads any other output. Nothing writes back, so there is no hairpin.
  .output("effectiveReferenceSource", (ctx): ReferenceSource | undefined =>
    resolveReferenceSource(ctx.data),
  )
  // The comparator sources this panel can serve, with a line for each it cannot. Both facts are knowable
  // before a run, from the panel metadata staging already emits: the panel's size is the count of
  // distinct barcodes, and a declared comparator needs a role column and values of it that the column
  // actually carries. Offering a source the run would silently degrade would record a choice the user
  // never gets.
  .retentiveOutput("referenceSources", (ctx): ReferenceSourceChoices => {
    const meta = readCsvMeta(ctx);
    const barcodeColumn = ctx.data.barcodeSeqColumn;
    const panelSize = barcodeColumn
      ? (meta?.valuesByColumn?.[barcodeColumn] ?? []).filter((v) => v.trim() !== "").length
      : 0;
    const minMembers = Math.round(ctx.data.panelReferenceMinMembers);
    const roleColumn = ctx.data.roleColumn;
    const roleValues = new Set(roleColumn ? (meta?.valuesByColumn?.[roleColumn] ?? []) : []);
    const declaredTags = (ctx.data.referenceValues ?? []).filter((v) => roleValues.has(v));

    // Where a control feature is marked and no tag is the baseline, `controlNotBaseline` below and the
    // "Declared baseline tag" line here would both fire, since both test declaredTags being empty. The
    // warning wins that overlap: it gives the same fix plus what is serving instead, and says the marker
    // does not set the baseline. This line stands down rather than repeating it.
    const markerWithoutBaseline = !!ctx.data.controlFeature && declaredTags.length === 0;

    const options: ReferenceSourceChoices["options"] = [];
    const unavailable: string[] = [];
    if (declaredTags.length > 0)
      options.push({
        value: "declared",
        label: "Declared baseline tag",
        description:
          "The block judges each count against the tag your panel marks as the baseline, in the " +
          "same cell. Verdicts read this way compare across runs.",
      });
    else if (!markerWithoutBaseline)
      unavailable.push(
        "Declared baseline tag — you have not marked a tag as the baseline yet. Choose the panel " +
          "column that declares each tag's role. Then choose the values of that column that mark " +
          "the baseline tag.",
      );
    if (panelSize >= minMembers)
      options.push({
        value: "panel",
        label: "The panel's own readings",
        description:
          `The block judges each count against the rest of the panel (${panelSize} tags). This ` +
          `holds even where your panel declares a baseline tag. Pick this source to ignore that ` +
          `tag deliberately.`,
      });
    else
      unavailable.push(
        `The panel's own readings — your panel declares ${panelSize} ` +
          `${panelSize === 1 ? "tag" : "tags"}, and this source needs at least ${minMembers}. ` +
          `Lower "Minimum panel size to serve as baseline" under "Baseline thresholds". You can ` +
          `also declare a baseline tag.`,
      );
    // Always offered, and the only option with no condition attached. Whether it can serve turns on the
    // sample's cell count and on whether each tag's counts separate. This block has read neither, and
    // the second is answered per tag rather than per run. So the conditions live in the description, and
    // the RUN reports which tags fitted, which did not, and why.
    options.push({
      value: "distribution",
      label: "Each tag's own distribution",
      description:
        `The block splits each tag's counts across a sample's cells into two components and judges ` +
        `counts against the lower one. It needs at least ${Math.round(ctx.data.distributionMinCells)} ` +
        `cells in the sample, and it needs that tag's counts to actually separate. A tag whose counts ` +
        `do not separate gets no baseline, and only the antigens that tag carries read unreliable. ` +
        `Pick this where your panel declares no baseline tag and is too small to stand in for one.`,
    });

    // `none` is NOT offered, and there is no fourth option. A baseline is required and a run without
    // one does not happen, so "no baseline" is not a position a scientist can select here — it is a
    // configuration `args()` refuses. The published view that a tag declared to be bound by nothing is
    // not truly negative is served by the two rungs that need no such tag, the panel's own readings and
    // each tag's own distribution, rather than by an option that produces no answers.

    // What an unselected run is answered under. Nothing falls anywhere, so this states the consequence
    // of leaving the field alone rather than naming a fallback.
    const fallback = "no baseline — every verdict that needs one reads unreliable";

    // A warning and never a block. A panel that declares no baseline is a legitimate configuration, so
    // this flags a likely mistake rather than an invalid state. No other rung steps in.
    const controlNotBaseline = markerWithoutBaseline
      ? "You marked a control feature, but no tag is the baseline. The control feature marker only " +
        "labels that feature in the output. It does not set the level a count must exceed. Unless you " +
        "choose a baseline below, this run judges counts against nothing and every verdict that needs " +
        "a baseline reads unreliable. To use your control as the baseline, select the panel column " +
        "that declares it. Then select the value that marks it."
      : undefined;
    return { options, unavailable, fallback, controlNotBaseline };
  })
  .title(() => "Feature Barcode Profiling")
  // Standard block-label subtitle. The subtitle render context is args-only (no result pool / outputs
  // — touching them renders "Invalid subtitle"), so the dynamic "<dataset> / <barcode> - <feature>"
  // string is derived in the `suggestedBlockLabel` OUTPUT (which HAS the pool) and copied into
  // `defaultBlockLabel` by a UI watchEffect (the sanctioned block-label pattern). The subtitle only
  // reads `ctx.data`. Guard `ctx.data` — it can be undefined before block storage is parsed.
  .subtitle((ctx) => ctx.data?.defaultBlockLabel || "Feature-barcode - per-cell antigen counts")
  // Main (the per-sample progress grid) is always shown. The result tabs — Per-sample QC and the
  // per-cell results table — appear only once the block has produced outputs, so an unrun block shows
  // only Main. ctx.outputs settles when the workflow starts emitting, the same signal as the `started`
  // output.
  .sections((ctx) => {
    const hasRun = ctx.outputs !== undefined;
    return [
      { type: "link" as const, href: "/" as const, label: "Main" },
      ...(hasRun
        ? [
            { type: "link" as const, href: "/qc" as const, label: "Per-sample QC" },
            { type: "link" as const, href: "/results" as const, label: "Per-cell results" },
            // Shown for every run, including one with no V(D)J dataset. That run produces no antigen
            // columns, and the page saying so is the only place a user learns why. Hiding the tab would
            // leave the absence unexplained.
            { type: "link" as const, href: "/punchcard" as const, label: "Explore readout" },
            // Shown for every run too, and for the same reason as the explore readout: a run with no
            // V(D)J dataset computes no quality report, and this page saying so is the only place a user
            // learns why. Labelled "Run quality" and not "QC" so it cannot be read as another view of
            // the per-sample mitool stats above. That page is per sample, this one is per run.
            { type: "link" as const, href: "/antigen-qc" as const, label: "Run quality" },
          ]
        : []),
    ];
  })
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
