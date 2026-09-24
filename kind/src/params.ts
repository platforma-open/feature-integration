import { assertParamsObject } from "@platforma-sdk/block-kind";
import { isPlRef } from "@platforma-sdk/model";
import { isString } from "es-toolkit";
import { isNumber } from "es-toolkit/compat";
import type { BlockParams, ReferenceSource, RunMode } from "./types";

/**
 * The contract at runtime, for params that arrive from a template file rather than from typed code.
 *
 * Each field the contract names is read and checked; a key it does not name is dropped by never being
 * read. Params written against a different version of the contract are caught by the version in the
 * template entry's `{name}@{selector}` reference, not by a key-set check.
 */
export function parseInitializationParams(value: unknown): BlockParams {
  assertParamsObject(value);

  const params: Record<string, unknown> = {};
  for (const [field, { is, must }] of Object.entries(CONTRACT)) {
    const raw = value[field];
    if (raw === undefined) continue;
    if (!is(raw)) throw new Error(`'${field}' must be ${must}.`);
    params[field] = raw;
  }
  // Every value placed here passed its own field's guard, and `CONTRACT` is proven exhaustive over
  // `BlockParams` by the `satisfies` below.
  return params as BlockParams;
}

type Guard<T> = (value: unknown) => value is T;

/** A guard plus how to finish the sentence "'field' must be …". */
type Check<T> = { readonly is: Guard<T>; readonly must: string };

function check<T>(is: Guard<T>, must: string): Check<T> {
  return { is, must };
}

function oneOf<T extends string>(...allowed: readonly T[]): Check<T> {
  return check((v): v is T => allowed.includes(v as T), `one of: ${allowed.join(", ")}`);
}

const REF = "a reference to another block's output";
const NUMBER = check(isNumber, "a number");
const STRING = check(isString, "a string");

/**
 * The contract, field by field. The `satisfies` clause demands an entry for every key `BlockParams`
 * declares, typed against that key's own type, so a field added to the contract stops this compiling until
 * its check exists.
 *
 * Numbers are checked as numbers, not against their ranges: the ranges live in the model's `args` and in
 * the settings that set them, and restating them here would make the kind refuse a file the UI can
 * produce.
 */
const CONTRACT = {
  fbFastqRef: check(isPlRef, REF),
  datasetRef: check(isPlRef, REF),

  presetId: STRING,
  pattern: STRING,
  cellWhitelist: STRING,

  runMode: oneOf<RunMode>("dry", "full"),
  limitInput: NUMBER,
  perProcessCPUs: NUMBER,
  perProcessMemGB: NUMBER,

  aggregateBarcodeIqrMultiplier: NUMBER,
  aggregateBarcodeMinUmiThreshold: NUMBER,
  aggregateBarcodeTopN: NUMBER,

  referenceSource: oneOf<ReferenceSource>("declared", "distribution"),
  panelReferenceMinMembers: NUMBER,
  distributionMinCells: NUMBER,
  countFloor: NUMBER,
  boundCutoff: NUMBER,
  boundProbability: NUMBER,
  expectedBinderFraction: NUMBER,
  minVotingCells: NUMBER,
  minAgreement: NUMBER,
  gateThreshold: NUMBER,

  panelAssignedWarn: NUMBER,
  panelAssignedError: NUMBER,
  matchRateWarn: NUMBER,
  matchRateError: NUMBER,
  cellBarcodeQualityWarn: NUMBER,
  cellBarcodeQualityError: NUMBER,
  readsPerCellWarn: NUMBER,
  aggregateBarcodeWarn: NUMBER,
  aggregateBarcodeError: NUMBER,
  undeclaredBarcodeWarn: NUMBER,
  undeclaredBarcodeError: NUMBER,
  usableReadWarn: NUMBER,
  usableReadError: NUMBER,
  rescuedShareWarn: NUMBER,
  rescuedShareError: NUMBER,
  vdjAntigenCountWarn: NUMBER,
  vdjAntigenCountError: NUMBER,
} satisfies { [K in keyof BlockParams]-?: Check<NonNullable<BlockParams[K]>> };
