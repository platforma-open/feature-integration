import type { PlRef } from "@platforma-sdk/model";

/** "full" processes every read; "dry" (Preview) caps each sample at `limitInput` reads. */
export type RunMode = "dry" | "full";

/**
 * Where each cell's baseline reading comes from. "declared": the panel's declared baseline tags;
 * "distribution": each tag's own distribution. The retired "panel" source is not accepted.
 */
export type ReferenceSource = "declared" | "distribution";

/**
 * This block's init-params contract — the shape a block of this kind receives at creation, and exactly
 * what a project template serializes for it.
 *
 * Every field is optional: a block with nothing picked is an ordinary state the UI reaches, so export has
 * to be able to write it and apply to take it back. Whether a configuration is runnable is settled by the
 * model's `args` lambda, not here.
 *
 * Left out on purpose:
 * - the panel CSV handle, which is local to the machine that uploaded it;
 * - every panel column choice (barcode, feature name, sample, role, grouping, combine) and what depends on
 *   one (reference values, contending groups, the combine-mode UMI floor). `args` checks them against
 *   panel metadata the UI snapshots when the user picks them, which a template cannot do;
 * - the snapshots themselves, and view state (table and graph states, the block label).
 */
export type BlockParams = {
  // Inputs.
  fbFastqRef?: PlRef;
  datasetRef?: PlRef;

  // Read geometry and cell barcodes.
  presetId?: string;
  pattern?: string;
  cellWhitelist?: string;

  // Run scope and resources.
  runMode?: RunMode;
  limitInput?: number;
  perProcessCPUs?: number;
  perProcessMemGB?: number;

  // Aggregate-barcode detection.
  aggregateBarcodeIqrMultiplier?: number;
  aggregateBarcodeMinUmiThreshold?: number;
  aggregateBarcodeTopN?: number;

  // The binding reading.
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers?: number;
  distributionMinCells?: number;
  countFloor?: number;
  boundCutoff?: number;
  boundProbability?: number;
  expectedBinderFraction?: number;
  minVotingCells?: number;
  minAgreement?: number;
  gateThreshold?: number;

  // QC warn / error lines.
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
