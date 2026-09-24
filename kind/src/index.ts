import { assertParamsObject, defineBlockKind } from "@platforma-sdk/block-kind";
import type { ImportFileHandle, ImportFileHandleIndex, PlRef } from "@platforma-sdk/model";
import { isImportFileHandleIndex, isPlRef } from "@platforma-sdk/model";
import { name, version } from "../package.json" with { type: "json" };

/**
 * Which baseline a count is read against. Selected, never inferred: two runs answered by different rules
 * produce numbers that do not compare, and a scientist who did not choose the rule cannot know that
 * happened.
 *
 * There is no "none". A baseline is required and a run without one does not happen, so an unselected
 * choice is undefined here and the model's `args()` refuses it.
 *
 * `"panel"` is RETIRED and no longer offered. The one tool that implements it decides at the CLONOTYPE,
 * pooling a clone's cells into one vector before anything is tested, so no cell ever holds a state and
 * there is nothing to vote on. The member stays in the union so a project stored under it still parses,
 * and `args()` refuses such a run and names the replacement.
 *
 * Lives in the kind rather than the model because it is part of the init-params contract, and the model
 * depends on the kind rather than the other way round. `model/src/types.ts` re-exports it.
 */
export type ReferenceSource = "declared" | "panel" | "distribution";

/**
 * How tags become identities. A RULE over declared properties, never a tag->identity map: a map is keyed
 * by tags, which are known only after the block runs, so any editor for it writes an output back into
 * data. Property column names are knowable at prerun. Absent means one identity per tag; there is no
 * explicit per-tag rule.
 *
 * Several columns may be named, and the identity is the distinct combination of their values.
 *
 * `column` is the shape this rule had before it took a list. It stays readable so a project stored under
 * it keeps running, and the model's `groupingColumns()` is the one place that reads either. Never write
 * it.
 *
 * Lives in the kind for the same reason as `ReferenceSource` above.
 */
export type GroupingRule =
  | { by: "property"; columns: string[]; column?: never }
  | { by: "property"; column: string; columns?: never };

/** "full" processes every read; "dry" (Preview) caps each sample at `limitInput` reads. */
export type RunMode = "dry" | "full";

/**
 * This block's init-params contract — what a creator or a project template supplies to seed a new
 * instance. A subset of the model's `BlockData`: the two inputs, the panel mapping, the read geometry,
 * the run scope, the aggregate-barcode knobs, the binding-reading knobs and the QC warn/error lines.
 *
 * Every field is optional. A template may seed any subset of a configuration, and the model's `init`
 * supplies the shipped default wherever a field is absent. That is also what keeps export and apply
 * inverses: the projection hands back live state, including a half-picked panel, and the parser must
 * accept every state the UI can reach.
 *
 * The panel CSV is narrowed to an `index://` handle. An `upload://` handle names an import local to one
 * machine, so it cannot survive being written to a template and applied elsewhere; the projection drops
 * those, and the panel columns with them, since they name columns of that file.
 *
 * Deliberately NOT here:
 * - grid, plot and expansion state — view state, meaningless to a fresh block;
 * - CPU and memory per process — resource allocation belongs to the machine that runs the block, not to
 *   portable configuration;
 * - the combine-mode column and its UMI floor — not offered in the UI;
 * - `contendingGroups` — written only on a user gesture over identities that exist after a run;
 * - the CSV/panel snapshots (`csvMetaSnapshot`, `panelColumnSnapshot`, `sampleColumnValues`, …) —
 *   derived from the picked file by the UI, never authored.
 */
export type BlockParams = {
  // --- inputs ---
  fbFastqRef?: PlRef;
  datasetRef?: PlRef;
  // --- panel mapping ---
  tagFeatureCsvHandle?: ImportFileHandleIndex;
  barcodeSeqColumn?: string;
  featureNameColumn?: string;
  /**
   * The panel column holding the sample name, for a sample-aware tag->feature mapping. Seeded alone: the
   * `sampleLabelSnapshot` / `sampleColumnValues` that sit beside it in `BlockData` are project-scoped --
   * one is this project's sampleId->name map, the other this CSV's own values -- so they cannot travel in
   * a template. The UI takes them again against the applied project's dataset and CSV.
   */
  sampleColumn?: string;
  // --- read geometry ---
  presetId?: string;
  pattern?: string;
  cellWhitelist?: string;
  // --- run scope ---
  runMode?: RunMode;
  limitInput?: number;
  // --- aggregate-barcode detection ---
  aggregateBarcodeIqrMultiplier?: number;
  aggregateBarcodeMinUmiThreshold?: number;
  aggregateBarcodeTopN?: number;
  // --- the binding reading ---
  referenceSource?: ReferenceSource;
  roleColumn?: string;
  referenceValues?: string[];
  panelReferenceMinMembers?: number;
  distributionMinCells?: number;
  countFloor?: number;
  boundCutoff?: number;
  boundProbability?: number;
  expectedBinderFraction?: number;
  minVotingCells?: number;
  minAgreement?: number;
  gateThreshold?: number;
  grouping?: GroupingRule;
  // --- QC warn / error lines ---
  panelAssignedWarn?: number;
  panelAssignedError?: number;
  matchRateWarn?: number;
  matchRateError?: number;
  cellBarcodeQualityWarn?: number;
  cellBarcodeQualityError?: number;
  readsPerCellWarn?: number;
  aggregateBarcodeWarn?: number;
  aggregateBarcodeError?: number;
  undeclaredBarcodeWarn?: number;
  undeclaredBarcodeError?: number;
  usableReadWarn?: number;
  usableReadError?: number;
  rescuedShareWarn?: number;
  rescuedShareError?: number;
  vdjAntigenCountWarn?: number;
  vdjAntigenCountError?: number;
};

/**
 * The same contract at runtime, for params that arrive from a template file rather than from typed
 * code — the only point that can catch a hand-written entry being wrong.
 *
 * Every field is optional, so each is checked only where present. A key the contract does not name is
 * dropped by not being read; there is no key-set check, so a misspelled key surfaces later as a block
 * that initialized blank rather than as a rejection here.
 *
 * The checks are on the envelope — is this a ref, a string, a number, a list of strings — never on what
 * the value means. Whether a threshold is in range, or a named column exists in the panel, is settled by
 * the model's `args()` against real data.
 */
function parseInitializationParams(value: unknown): BlockParams {
  assertParamsObject(value);

  const params: BlockParams = {};

  for (const key of REF_KEYS) {
    const v = value[key];
    if (v === undefined) continue;
    if (!isPlRef(v)) throw new Error(`'${key}' must be a PlRef.`);
    params[key] = v;
  }

  for (const key of STRING_KEYS) {
    const v = value[key];
    if (v === undefined) continue;
    if (typeof v !== "string") throw new Error(`'${key}' must be a string.`);
    params[key] = v;
  }

  for (const key of NUMBER_KEYS) {
    const v = value[key];
    if (v === undefined) continue;
    if (typeof v !== "number" || !Number.isFinite(v)) {
      throw new Error(`'${key}' must be a finite number.`);
    }
    params[key] = v;
  }

  // Only an `index://` handle resolves on another machine. `isImportFileHandleIndex` is a prefix test, so
  // handing it a checked string is safe; the cast only gets the string past a signature that expects the
  // union. Whether the handle still resolves is settled when the workflow imports it.
  if (value.tagFeatureCsvHandle !== undefined) {
    const v = value.tagFeatureCsvHandle;
    if (typeof v !== "string" || !isImportFileHandleIndex(v as ImportFileHandle)) {
      throw new Error(
        "'tagFeatureCsvHandle' must be an 'index://' file handle — an 'upload://' handle names a local import and does not resolve on another machine.",
      );
    }
    params.tagFeatureCsvHandle = v as ImportFileHandleIndex;
  }

  if (value.runMode !== undefined) {
    const v = value.runMode;
    if (v !== "dry" && v !== "full") throw new Error("'runMode' must be one of 'dry', 'full'.");
    params.runMode = v;
  }

  if (value.referenceSource !== undefined) {
    const v = value.referenceSource;
    if (v !== "declared" && v !== "panel" && v !== "distribution") {
      throw new Error("'referenceSource' must be one of 'declared', 'panel', 'distribution'.");
    }
    params.referenceSource = v;
  }

  if (value.referenceValues !== undefined) {
    params.referenceValues = parseStringArray(value.referenceValues, "referenceValues");
  }

  if (value.grouping !== undefined) {
    params.grouping = parseGroupingRule(value.grouping);
  }

  return params;
}

// Identity (`name`/`version`) comes from this package's own `package.json`, so
// the on-wire `{name}@{version}` reference can never drift from what npm
// publishes; the bundler inlines the JSON import.
export const kind = defineBlockKind<BlockParams>({
  name,
  version,
  parseInitializationParams,
});

// Internals

const REF_KEYS = ["fbFastqRef", "datasetRef"] as const;

const STRING_KEYS = [
  "barcodeSeqColumn",
  "featureNameColumn",
  "sampleColumn",
  "presetId",
  "pattern",
  "cellWhitelist",
  "roleColumn",
] as const;

const NUMBER_KEYS = [
  "panelReferenceMinMembers",
  "distributionMinCells",
  "countFloor",
  "boundCutoff",
  "boundProbability",
  "expectedBinderFraction",
  "minVotingCells",
  "minAgreement",
  "gateThreshold",
  "limitInput",
  "aggregateBarcodeIqrMultiplier",
  "aggregateBarcodeMinUmiThreshold",
  "aggregateBarcodeTopN",
  "panelAssignedWarn",
  "panelAssignedError",
  "matchRateWarn",
  "matchRateError",
  "cellBarcodeQualityWarn",
  "cellBarcodeQualityError",
  "readsPerCellWarn",
  "aggregateBarcodeWarn",
  "aggregateBarcodeError",
  "undeclaredBarcodeWarn",
  "undeclaredBarcodeError",
  "usableReadWarn",
  "usableReadError",
  "rescuedShareWarn",
  "rescuedShareError",
  "vdjAntigenCountWarn",
  "vdjAntigenCountError",
] as const;

function parseStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((e) => typeof e !== "string")) {
    throw new Error(`'${field}' must be an array of strings.`);
  }
  return value as string[];
}

/**
 * A property rule naming one or more panel columns. The single-column `column` form
 * is the shape the rule had before it took a list; it stays accepted so a template written against an
 * older project keeps applying.
 */
function parseGroupingRule(value: unknown): GroupingRule {
  assertParamsObject(value);

  if (value.by !== "property") {
    throw new Error("'grouping.by' must be 'property'.");
  }

  if (value.columns !== undefined) {
    return { by: "property", columns: parseStringArray(value.columns, "grouping.columns") };
  }

  if (typeof value.column !== "string") {
    throw new Error("A 'property' grouping needs 'columns' (or the legacy 'column').");
  }
  return { by: "property", column: value.column };
}
