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
import type { BlockArgs, BlockData, GroupingRule, ReferenceSource } from "./types";

export { assemblePattern, parsePattern, validatePattern } from "./pattern";
export type { PatternParts } from "./pattern";
export { allPresets, getPreset } from "./presets";
export type { Preset } from "./presets";
export type { BlockArgs, BlockData, GroupingRule, ReferenceSource } from "./types";

// Re-exported so the UI can seed a grid state without depending on @platforma-sdk/model directly — the ui
// package's only SDK dependency is ui-vue, which does not carry this factory.
export { createPlDataTableStateV2 } from "@platforma-sdk/model";
export type { PTableKey } from "@platforma-sdk/model";

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

// The punchcard's frame is keyed on the clonotype set alone, and each identity is a COLUMN rather than an
// axis value — which is what a punchcard needs and what a (set, identity) frame cannot give a table. The
// identity therefore travels in the column's DOMAIN, which is how the model reads which identity a column
// belongs to without parsing a label.
//
// One column per identity, its value carrying the state and both support counts together (see
// identityPunchImportSpec). The pairing is inside the value because a grid pairs a cell with another
// column's cell only by position, which no import guarantees.
//
// The UI identifies a punch column by these two — the column's own name and the domain key its identity
// travels under — read off the spec the grid hands back on `colDef.context`. It used to match the
// identity against the column ID instead, and that was wrong twice over: an id is
// `identityPunch_<substituteSpecialCharacters(identity)>`, so any identity carrying a hyphen or a space
// never matched itself, and a substring test let `SpikeWT` match `SpikeWT_alt`'s column and name the
// wrong antigen. Both id prefixes existed only to serve that match and are gone with it.
export const PUNCH_COLUMN_NAME = "pl7.app/antigen/identityPunch";
export const PUNCH_IDENTITY_DOMAIN = "pl7.app/antigen/identityId";
// The clonotype's cell count, carried in the punchcard's own frame so the grid can read it: a
// block's own exports are not in its own result pool, so the copy in the exported setCounts family
// is unreachable from here.
export const PUNCH_CELL_COUNT_COLUMN = "pl7.app/antigen/cellCount";

// How each comparator choice is written for a reader. The single place the wording lives: the Python
// enum, the run-meta JSON and the p-column domain all carry the machine token, so rewording a sentence
// here cannot break a branch anywhere. The three strings match the labels the `referenceSources` output
// offers before a run, so the same choice does not change its name once it has served.
// User-facing names only. The DATA layer keeps `declared`/`panel`/`none` — those tokens are the
// p-column domain values, and domain is part of column identity, so renaming them would change what
// every emitted column IS. Labels are free to say "baseline" where the data says "reference".
export const REFERENCE_SOURCE_LABELS: Record<ReferenceSource, string> = {
  declared: "Declared baseline tag",
  panel: "The panel's own readings",
  none: "No baseline",
};

// The run record emit_verdicts.py writes (result_run_meta.json), read as content. Only the fields the UI
// states back to the user are typed here; the file carries every parameter the reading used.
export type VerdictRunMeta = {
  /**
   * The comparator that actually SERVED — a request the panel cannot honour degrades to none. A
   * `ReferenceSource` rather than a bare string: the value crosses from the Python enum through the
   * run-meta JSON into a UI branch, and typing it as `string` is what let a display sentence be used as
   * a control-flow token.
   */
  referenceChoice: ReferenceSource;
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
  /** What an unset source resolves to for this panel, as a sentence. */
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

// The grouping columns a rule names, whichever shape it is stored in.
//
// A project saved before the rule took a list carries `column` rather than `columns`, and reading
// both here costs one function. A data migration would have to run against every stored project to
// avoid a failed run, which is a far worse trade for the same result. Every reader goes through this,
// so nothing else needs to know two shapes exist.
export function groupingColumns(rule: GroupingRule | undefined): string[] {
  if (rule === undefined || rule.by !== "property") return [];
  if (rule.columns !== undefined) return rule.columns.filter((c: string) => c !== "");
  return rule.column ? [rule.column] : [];
}

// A/C/G/T plus N (ambiguous base), case-insensitive.
const isDnaValue = (v: string) => /^[ACGTN]+$/i.test(v);

// Evidence that the chosen barcode-sequence column does NOT hold nucleotide sequences, or undefined when
// it does (or when the CSV meta hasn't resolved and the question can't be answered yet). Blank cells are
// ignored rather than counted against the column — a trailing empty row is a CSV artefact, not evidence
// about the contents. Kept a module helper because a block output cannot read another output, and two
// need this: barcodeAlphabetIssue reports it, and barcodeMappingIssue stays silent while it holds so the
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

// v3 data shape: the reading's parameters, with the three grid states the two removed result views owned.
// v4 replaced them with the punchcard's own state. `punchcardIdentities` is named in the Omit for history:
// v4 introduced it and a later change removed it from BlockData, so the key is dead on the right-hand side
// and harmless on the left.
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
  .migrate<BlockDataV3>(
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
      antigenQcTableState: createPlDataTableStateV2(),
      panelMismatchTableState: createPlDataTableStateV2(),
    }),
  )
  // v3 -> v4: the flat verdict table and the quality-report tables are gone as VIEWS, and the punchcard
  // takes their place. The three grid states go with them rather than being carried: a saved column set or
  // filter is meaningful only against the frame it was saved on, and none of these three frames is on
  // screen any more. The punchcard's own state starts fresh, on the whole panel — every identity column the
  // pivot produced is drawn, and a reader hides or filters columns in the grid's own panels.
  //
  // What the removed pages showed is still EMITTED: the verdicts and the run's measurements are both
  // artifacts `verdict-block-interface` obliges this block to produce, and dropping a view does not
  // release it from producing them.
  //
  // The run's measurements and the panel-versus-reads check now have a page again (Run quality), and its
  // two grid states are `runQualityTableState` / `runQualityMismatchTableState` — NOT the two keys stripped
  // here. That is deliberate: this strip is what the names below mean, so reusing them would make a saved
  // column set and filter from a view removed several versions ago reappear under a grid it was never saved
  // against, and would make this destructure read as if it were removing the live page's state.
  .migrate<BlockData>(
    "v4",
    ({ verdictTableState: _v, antigenQcTableState: _q, panelMismatchTableState: _m, ...rest }) => ({
      ...rest,
      punchcardTableState: createPlDataTableStateV2(),
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
    punchcardTableState: createPlDataTableStateV2(),
    // No migration adds these two, and none is needed: a field absent from an older project's stored data
    // is filled from these defaults on load, which is how qcSummaryTableState arrived as well. Their names
    // avoid the two keys the v3 -> v4 migration strips — see the comment on those fields in types.ts.
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

    // A barcode on more than one row with no sample column is not a warning, it is a run that will stop:
    // per_cell_metrics.py refuses to map one barcode to two antigens, and it refuses at the END, after
    // every sample has been parsed. Blocking Run here spends the user a second instead of the whole run.
    // Both numbers are snapshots taken when the barcode column was picked; absent means the meta had not
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
    // "declared" reads counts against a tag the panel marks as the comparator, and nothing marks one
    // without the role values. Asking for it anyway would degrade to no comparator inside the run, where
    // the choice is recorded but the user never sees they lost it.
    if (data.referenceSource === "declared" && !data.referenceValues?.length)
      throw new Error(
        'Under "Values that mark the baseline tag", choose at least one value, or choose a ' +
          'different option for "What sets the baseline".',
      );
    // Every panel column the verdict settings name, each with the label the user sees. Two different
    // things can be wrong with one of these, so both checks below walk this same list.
    //
    // Each grouping column is checked on its own. A grouping may name several, and joining them into one
    // string to check would compare "Identity, Channel" against the panel's headers and never match —
    // which throws here and takes the whole block to Limbo, refs and all.
    const named: [string, string | undefined][] = [
      ["Baseline role", data.roleColumn],
      ...groupingColumns(data.grouping).map((c): [string, string] => ["Grouping", c]),
    ];

    // First: a column the panel reader consumes as a KEY is not a property column, so naming one here
    // ends the run at the exec. emit_verdicts.py raises on a grouping column the panel does not declare,
    // and on a role column wherever role values are set. Where they are NOT set it raises nothing, and
    // the baseline falls back to the panel's own readings — a wrong answer rather than no answer, which
    // is the outcome this block exists to refuse.
    //
    // The way in is reassigning a key column WITHIN one panel file. The settings dropdowns stop offering
    // the column, and the pick already stored survives, so the field reads empty while the data is not.
    // Checked against data rather than the header snapshot because a key column IS a real header: the
    // snapshot check below cannot see this case and correctly does not try to.
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
      // Resolved here rather than sent absent, so the run records the rule it actually read under and the
      // settings field can always show a concrete one. This reproduces verdict.py's `resolve_default_source`
      // from data alone: a declared baseline tag where values mark one, otherwise the panel's own readings.
      // The third rung needs the panel SIZE, which is panel metadata rather than data — and it needs no
      // help here, because `served_source` already degrades a panel request to none when the panel is too
      // short. So the two agree without this reaching outside data.
      //
      // A value the user chose explicitly wins: `served_source` never substitutes a different rung for one
      // that was asked for, only drops it to none.
      referenceSource:
        data.referenceSource ?? (data.referenceValues?.length ? "declared" : "panel"),
      panelReferenceMinMembers: Math.round(data.panelReferenceMinMembers),
      referenceThinLine: Math.round(data.referenceThinLine),
      countFloor: Math.round(data.countFloor),
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
    const grouped = groupingColumns(ctx.data.grouping);
    // Nothing to offer under a grouping on SEVERAL columns. An identity is then the combination of
    // their values, and the prerun CSV meta is column-wise: it carries each column's distinct values
    // with no pairing between them. Crossing them would invent combinations the panel never declared,
    // and offering a fabricated identity to the contending-groups editor is worse than offering none.
    // The editor says so rather than showing a list built from a guess.
    if (grouped.length > 1) return [];
    const column = grouped[0] ?? ctx.data.barcodeSeqColumn;
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
    // extra CSV values are only informational (those rows are simply never used). Counted into a real
    // plural rather than written "sample(s)": the reader has to resolve that form themselves, and these
    // two lines are read while something is already wrong.
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
  // The tag CSV column that looks like it names the dataset's samples (or undefined). The UI offers it as
  // a one-click "use sample-aware mapping" suggestion. Purely advisory — the user must still pick it (a
  // gesture that snapshots the sample map into data); this output never writes data. Excludes the columns
  // already bound to the barcode / feature roles. See suggestSampleColumn for the superset/equality rule.
  .retentiveOutput("suggestedSampleColumn", (ctx): string | undefined => suggestSampleColumn(ctx))
  // Alphabet check on the chosen barcode-sequence column. This is a UI warning only. mitool guards the
  // same condition, but it guards it by failing refine-tags in the middle of the run.
  //
  // A panel CSV often carries BOTH an identifier column and the nucleotide column. "Barcode" holds
  // T0100 and "Sequence" holds CGATGCCGGACGATC. The identifier column has the name a user is more
  // likely to select. That choice writes a panel.txt of non-nucleotide strings. The run then fails
  // several stages later, inside barcode correction, with "Error while loading sequence set from
  // ./panel.txt" and a Java stack trace. The reads are already parsed by then.
  //
  // The args guard cannot catch this. It sees only `data`, and the values live in the prerun CSV meta.
  // The check therefore fires here, at config time, as barcodeMappingIssue does for duplicate
  // barcodes. It is deliberately not gated on sampleColumn. A per-sample filter narrows which rows
  // reach the panel. It never turns an identifier into a sequence.
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
  // barcode, which a partly-overlapping panel supplies but a fully disjoint one — sample A stained with
  // one set, sample B with another — never does. `sampleMappingWarning` validates a column that has been
  // chosen and returns nothing when none has. So the one arrangement that fails silently is exactly the
  // one where the panels share no barcode at all.
  //
  // The tell is a column whose values cover every dataset sample, which is what `suggestSampleColumn`
  // already looks for. Guarded against `barcodeMappingIssue`'s condition so the two never fire together:
  // that one is the louder problem (an ambiguous mapping fans the per-cell join) and it already names
  // this fix.
  //
  // Why it is worth a warning rather than a line of tooltip: read as one panel, every sample is offered
  // every antigen, so an antigen a sample was never stained with comes back NOT BOUND instead of NEVER
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
  // True while the uploaded CSV is still being parsed by staging (handle set, but emit-csv-meta hasn't
  // produced csvMeta yet) — lets the UI show a "reading columns…" state instead of silent empty
  // dropdowns. NOT retentive: it must report the live loading state, including on a CSV swap.
  // Total data rows in the uploaded CSV, so the UI can snapshot it alongside the barcode column's
  // distinct count. Those two numbers are what args() needs to refuse a duplicate mapping, and args()
  // cannot reach the prerun meta itself.
  .retentiveOutput("csvRowCount", (ctx): number | undefined => readCsvMeta(ctx)?.rowCount)
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
  // Every combined identity the punchcard could show, in the order the workflow gave them, each with the
  // label the workflow put on its column. Two things on the card read this, and neither narrows anything.
  // The punch hover reads it because a reader who hovers a dot far down a long grid cannot see the header
  // row at all. The card's empty state reads it to tell "the pivot emitted no identity columns" apart
  // from "this run has no rows at all".
  //
  // Read from the pivot's own columns rather than from the run record's identity list, because the two can
  // disagree in exactly one way that matters — the pivot is size-gated upstream, so a run over a large
  // panel names its identities in the record and emits no columns at all. Reading the columns means this
  // lists what the punchcard can actually draw, which is what makes the empty state trustworthy.
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
      // than the barcode. The identity itself is a raw sequence under the per-tag grouping, so carrying the
      // label is what keeps the punch hover naming an antigen the reader recognises.
      const label = c.spec.annotations?.["pl7.app/label"];
      options.push({ value: identity, label: label ?? identity });
    }
    return options;
  })
  // The punchcard: one row per clonotype set, one column per identity, each cell carrying the four-state
  // verdict and the count of cells that answered it. The pivoted shape comes from the workflow because a
  // table cannot pivot a (set, identity) frame into columns.
  //
  // The whole panel, every time. A reader who wants fewer columns hides them in the grid's columns panel or
  // filters in its filters panel; this output does not second-guess either.
  //
  // V3 here, and V2 everywhere else in this block, for a reason particular to this table.
  //
  // V2 could not build it at all: `Cannot produce a Vec1 with a length of zero`. These columns are keyed
  // on ONE axis, `pl7.app/vdj/scClonotypeKey`, and the result pool holds a label column for exactly that
  // axis (the clonotyping block publishes it). V2 discovers the label, the frame's only axis is consumed,
  // and the engine is handed an empty key vector. Verified by instrumented build: a SINGLE punch column
  // fails identically, so it is not the 13-way join or the shared column name. The flat verdict table this
  // view replaced survived only because it carried a second axis that nothing labelled — one axis is
  // inherent to a punchcard, so there was nothing to tune.
  //
  // V3's `primaryColumns` form is the explicit one: it takes the columns as given and runs NO data-column
  // discovery, so it does not walk the result pool and cannot hang on the upstream Samples & Data FASTQ
  // dataset — which is the hazard the other tables here chose V2 to avoid, and the reason this is not a
  // blanket migration. V3 does still resolve label columns for the axes it was handed, which is wanted:
  // a clonotype row reads better under its clonotype label than under a raw key.
  //
  // V2 is deprecated SDK-side in favour of this call, so the rest of this model's tables will follow
  // eventually; each needs its own check against the discovery hazard first.
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
      // the per-tag grouping is the barcode — and a panel's names never sort the same as its sequences, so
      // the card opened in an order that looked arbitrary to the only person reading it. Numeric collation
      // so `antigen_9` precedes `antigen_10` rather than following it. The clonotype column is not among
      // these (`columns: null` brings it in separately), so it keeps its place at the front.
      const labelOf = (c: (typeof cols)[number]) =>
        c.spec.annotations?.["pl7.app/label"] ?? (identityOf(c) as string);
      const ordered = [...cols].sort((a, b) =>
        labelOf(a).localeCompare(labelOf(b), undefined, { sensitivity: "base", numeric: true }),
      );
      // Headers carry the identity's full name. A cut to 20 characters was applied here before, so that
      // one long label could not auto-size its column and move the rest of the card off screen.
      //
      // That cut removed the one thing a reader needs from a header: which identity the column is. It
      // also applied only to the joined labels a tag receives when its rows disagree about the grouping
      // column. Correct those labels where they are produced. Do not hide them behind an ellipsis.
      // Column ORDER here comes from the `pl7.app/table/orderPriority` annotation on each spec, and
      // from nothing else. The cell count carries 96000, between the clonotype label's 100000 and the
      // punches' 92000, and lands at position 3: row number, clonotype, cell count, then the identities.
      //
      // Verified by A/B against the live grid: a `displayOptions.ordering` rule and this array's own
      // order are BOTH inert — inverting the rule's priority and moving the cell count to the end of
      // `primaryColumns` each left it at position 3. Neither lever was added; if you are here to fix a
      // column that "renders last", measure before you change anything, and measure with
      // `aria-colindex`. `querySelectorAll('[role="columnheader"]')` returns AG Grid's recycled header
      // nodes in an order that has nothing to do with column position, and reading it that way is what
      // produced a bug report against this line when the placement was already correct.
      return createPlDataTableV3(ctx, {
        primaryColumns: [...cellCount, ...ordered].map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.punchcardTableState,
      });
    },
    { retentive: true, withStatus: true },
  )
  // The clonotype axis id, DERIVED from an emitted column rather than written out by hand.
  //
  // The page hangs the expansion's row button on this axis, and `showCellButtonForAxisId` is matched with
  // `isJsonEqual` — exact JSON equality, domain and all. A hand-written `{type, name}` misses the domain
  // this axis carries, matches nothing, and renders no button with no error to say why. Deriving it from
  // the same spec the filter reads also makes the two provably agree, which is the property that matters:
  // a button on a row whose key the filter cannot resolve is worse than no button.
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
  // Reads `antigenVerdictsTable`: the LONG verdicts family at (set, identity) grain. That output already
  // existed, held open by main.tpl for exactly this ("the first page that wants the verdicts unpivoted —
  // a per-identity list, say — reads it without a workflow change"), so this page costs no workflow
  // change and no second import. Its rows are identities, which is the shape the expansion wants and the
  // shape a pivot cannot give it.
  //
  // NOT gated on the identity count that gates the card's pivots. That gate exists because a pivot costs
  // a COLUMN per identity and sits well under the thousand-plus a pMHC panel carries. Here an identity
  // costs a ROW, and only one clonotype's rows are ever fetched — so a panel too wide for the card is
  // precisely where this view still reads.
  //
  // The filter is pushed down, not applied after the fact: `createPlDataTableV3` puts it in the PTable
  // def, `createPTableDefV3` wraps the join in a `{type:"filter", predicate}` query node, and the engine
  // lowers that into the data query (pframes-rs `visit_filter`). So one clonotype's rows are what crosses
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
      // a malformed join, and the SDK answers with `discoverColumns failed` out of `discoverLabelColumns`,
      // which reads as an SDK fault and is not one. Only the (set, identity) family belongs here.
      //
      // The identity's readable name comes FIRST, and it has to be named here rather than left to
      // `columns: null`. That option resolves label columns from the result pool, and this label lives
      // in `exportFb` — a block's own exports are not in its own result pool. Measured before this
      // existed: 17 rows all printing the same clonotype with nothing telling them apart.
      //
      // Could-answer is CONDITIONAL. Under one panel it is the clonotype's own cell count at every
      // identity, which the grid already carries beside its name — so a column of it repeats one number
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
      const WANTED = [
        "pl7.app/label",
        "pl7.app/antigen/verdict",
        ...(panelsDiffer ? ["pl7.app/antigen/cellsCouldAnswer"] : []),
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
      // The identity's name has to be one of them, and a count is not enough to know that. If only the
      // label failed to match — an axis name drifting at export time is all it would take — the verdict
      // and bound columns still match, the count is still non-zero, and the panel renders 17 anonymous
      // rows again with nothing to say it regressed. No panel is a visible failure; a nameless one reads
      // as working. The verdict column is required too, and the setAxis guard below catches its absence.
      if (!pCols.some((c) => c.spec.name === "pl7.app/label")) return undefined;
      // The set axis is the first of the (set, identity) pair, taken from the verdict column rather than
      // from `pCols[0]`: the identity label column now sorts first and carries only the identity axis, so
      // reading `pCols[0].spec.axesSpec[0]` here would hand the filter that axis instead of the set axis
      // and resolve nothing. An axis assembled here would also be a lookalike with a different identity
      // and would filter nothing.
      const verdictCol = pCols.find((c) => c.spec.name === "pl7.app/antigen/verdict");
      const setAxis = verdictCol?.spec.axesSpec[0];
      if (setAxis === undefined) return undefined;
      return createPlDataTableV3(ctx, {
        primaryColumns: pCols.map((c) => DataColumn.fromColumn(c)),
        columns: null,
        tableState: ctx.data.expansionTableState,
        // The identity's own name, made visible. `identityLabelsImportSpec` annotates it hidden, which is
        // the right convention for a `pl7.app/label` column: a table CONSUMES a label column to name its
        // axis rather than rendering it as data. That convention fails here for one reason — the identity
        // axis is invented by this block, so its label column can never sit in this block's own result
        // pool, and the pool is the only place the table looks. Measured before this rule: 17 rows all
        // printing the same clonotype with nothing telling them apart.
        //
        // Overriding visibility here rather than in the workflow spec, because that column is an EXPORT
        // with downstream readers, and making it default-visible would change their tables to fix ours.
        // This is how clonotype-browser adjusts a column it did not annotate.
        // Two rules, and the order matters — the first match wins. Both columns the table would show as
        // a name are called `pl7.app/label`, so they are told apart by the axis each one labels.
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
            // clonotype: printing its name down all 17 rows is repetition, and the reader chose the
            // clonotype by clicking it. Optional rather than hidden, so the Columns picker can bring it
            // back for anyone who wants the name on screen.
            { match: { name: "^pl7\\.app/label$" }, visibility: "optional" },
          ],
        },
        filters: {
          type: "and",
          filters: [
            {
              type: "patternEquals",
              // The FULL axis id, domain included. Dropping the domain leaves an id that
              // `remapFilterColumnIds` cannot resolve against the table's columns, and the SDK's
              // unresolved-leaf path calls `console`, which does not exist in the model's QuickJS
              // sandbox — so the symptom is `ReferenceError: 'console' is not defined` from deep inside
              // the SDK rather than anything naming the filter. Worth knowing: that error means an
              // unresolvable filter column, not a logging problem.
              column: {
                type: "axis",
                id:
                  setAxis.domain === undefined
                    ? { name: setAxis.name, type: setAxis.type }
                    : { name: setAxis.name, type: setAxis.type, domain: setAxis.domain },
              },
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
  // and SHOWN; a measurement computed on every run and read by nobody satisfies half of that. Read from
  // `outputs` rather than from the exports, because a block's own exports are not in its own result pool.
  //
  // `allowPermanentAbsence` for the same reason punchcardTable needs it: the whole verdict stage is gated
  // on a V(D)J dataset being picked, so on a run without one this field never appears at all, and a resolve
  // that treats a permanent absence as a pending one leaves the output waiting forever instead of returning
  // undefined for the page to explain.
  //
  // A frame with no rows is deliberately NOT folded into undefined here. Absent means the verdict stage did
  // not run; empty means it ran and had nothing to report, which for the mismatch check is the good
  // outcome. Collapsing the two would make the page unable to tell them apart, so the distinction is kept
  // and the page says which it is meeting.
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
  // mismatch report the user cannot see defeats its purpose — that is the whole reason the workflow emits
  // it into `outputs` and not only into the exports.
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
  // The comparator sources this panel can serve, with a line for each it cannot. Both facts are knowable
  // before a run, from the panel metadata staging already emits: the panel's size is the count of distinct
  // barcodes, and a declared comparator needs a role column and values of it that the column actually
  // carries. Offering a source the run would silently degrade would record a choice the user never gets.
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

    // The two messages this output feeds used to both fire in one state, saying the same thing. Whenever a
    // control feature is marked and no tag is the baseline, `controlNotBaseline` below fires AND the
    // "Declared baseline tag" line here fires, because both test declaredTags being empty — so a reader saw
    // a warning and an info block one above the other, each telling them to set the role column and its
    // values. The warning wins that overlap: it says the same fix plus what is serving instead and that the
    // marker does not set the baseline. This line stands down rather than repeating it.
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
    // `none` is deliberately NOT offered. It is what the run REPORTS when neither rung can serve — the
    // third rung of `292-no-declared-reference`'s ordering — and never something a scientist asks for:
    // requesting it guarantees a run with no answers at all, every position unreliable. It was on this
    // list only because `ReferenceSource` (what is requested) and `ReferenceChoice` (what served) happen
    // to share their three tokens, and the giveaway was its own description, which explained the software
    // degrading rather than a choice anyone would make. The served case is still surfaced: the punchcard
    // says so in a banner, and `unavailable` below says why neither rung could serve.

    // The three-rung default, restated from verdict.py resolve_default_source.
    const fallback =
      declaredTags.length > 0
        ? "the declared baseline tags"
        : panelSize >= minMembers
          ? "the panel's own readings"
          : "no baseline — every reading would be unreliable";

    // Built from `fallback` rather than naming a rung, so the sentence stays true where the panel is
    // also too small to serve as its own baseline: that case reads "no baseline" instead of claiming a
    // median served. A warning and never a block — atom `292-no-declared-reference` serves an
    // undeclared baseline as a legitimate configuration, so this flags a likely mistake, not an
    // invalid state.
    const controlNotBaseline = markerWithoutBaseline
      ? "You marked a control feature, but no tag is the baseline. The control feature marker only " +
        "labels that feature in the output. It does not set the level a count must exceed. This run " +
        `judges counts against ${fallback} instead. To use your control as the baseline, select the ` +
        "panel column that declares it. Then select the value that marks it."
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
            // Shown for every run, including one with no V(D)J dataset. That run produces no antigen
            // columns at all, and the page saying so is the only place a user learns why — hiding the
            // tab would leave the absence unexplained.
            { type: "link" as const, href: "/punchcard" as const, label: "Explore readout" },
            // Shown for every run too, and for the same reason as the explore readout: a run with no V(D)J
            // dataset computes no quality report, and this page saying so is the only place a user
            // learns why. Labelled "Run quality" and not "QC" so it cannot be read as another view of
            // the per-sample mitool stats above — that page is per sample, this one is per run.
            { type: "link" as const, href: "/antigen-qc" as const, label: "Run quality" },
          ]
        : []),
    ];
  })
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
