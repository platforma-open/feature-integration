import { assertParamsObject, defineBlockKind } from "@platforma-sdk/block-kind";
import type { ImportFileHandle, PlRef } from "@platforma-sdk/model";
import { isPlRef } from "@platforma-sdk/model";
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
 * data. Property column names are knowable at prerun. Absent means one identity per tag.
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
  | { by: "tag" }
  | { by: "property"; columns: string[]; column?: never }
  | { by: "property"; column: string; columns?: never };

/**
 * This block's init-params contract — what a creator or a project template supplies to seed a new
 * instance. A subset of the model's `BlockData`: the two inputs, the panel mapping, the read geometry
 * and the binding-reading knobs.
 *
 * Every field is optional. A template may seed any subset of a configuration, and the model's `init`
 * supplies the shipped default wherever a field is absent. That is also what keeps export and apply
 * inverses: the projection hands back live state, including a half-picked panel, and the parser must
 * accept every state the UI can reach.
 *
 * Deliberately NOT here:
 * - grid, plot and expansion state — view state, meaningless to a fresh block;
 * - the QC warn/error lines — quality thresholds, tuned against a run rather than declared before one;
 * - `contendingGroups` — written only on a user gesture over identities that exist after a run;
 * - the CSV/panel snapshots (`csvMetaSnapshot`, `panelColumnSnapshot`, `sampleColumnValues`, …) —
 *   derived from the picked file by the UI, never authored.
 */
export type BlockParams = {
  // --- inputs ---
  fbFastqRef?: PlRef;
  datasetRef?: PlRef;
  // --- panel mapping ---
  tagFeatureCsvHandle?: ImportFileHandle;
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

  // The file handle is an opaque SDK string, so the envelope is all there is to check here: whether the
  // handle still resolves is settled when the workflow imports it.
  if (value.tagFeatureCsvHandle !== undefined) {
    if (typeof value.tagFeatureCsvHandle !== "string") {
      throw new Error("'tagFeatureCsvHandle' must be an import file handle.");
    }
    params.tagFeatureCsvHandle = value.tagFeatureCsvHandle as ImportFileHandle;
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
] as const;

function parseStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((e) => typeof e !== "string")) {
    throw new Error(`'${field}' must be an array of strings.`);
  }
  return value as string[];
}

/**
 * `{ by: "tag" }`, or a property rule naming one or more panel columns. The single-column `column` form
 * is the shape the rule had before it took a list; it stays accepted so a template written against an
 * older project keeps applying.
 */
function parseGroupingRule(value: unknown): GroupingRule {
  assertParamsObject(value);

  if (value.by === "tag") return { by: "tag" };

  if (value.by !== "property") {
    throw new Error("'grouping.by' must be 'tag' or 'property'.");
  }

  if (value.columns !== undefined) {
    return { by: "property", columns: parseStringArray(value.columns, "grouping.columns") };
  }

  if (typeof value.column !== "string") {
    throw new Error("A 'property' grouping needs 'columns' (or the legacy 'column').");
  }
  return { by: "property", column: value.column };
}
