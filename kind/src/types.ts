import type { ImportFileHandleIndex, PlRef } from "@platforma-sdk/model";

/** "full" processes every read; "dry" (Preview) caps each sample at `limitInput` reads. */
export type RunMode = "dry" | "full";

/**
 * Where each cell's baseline reading comes from. "declared": the panel's declared baseline tags;
 * "distribution": each tag's own distribution. The retired "panel" source is not accepted.
 */
export type ReferenceSource = "declared" | "distribution";

/** How tags become identities: the distinct combination of the named panel columns' values. */
export type GroupingRule = { by: "property"; columns: string[] };

/**
 * This block's init-params contract — the shape a block of this kind receives at creation, and exactly
 * what a project template serializes for it.
 *
 * Every field is optional: a block with nothing picked is an ordinary state the UI reaches, so export has
 * to be able to write it and apply to take it back. Whether a configuration is runnable is settled by the
 * model's `args` lambda, not here.
 *
 * The panel CSV is narrowed to an `index://` handle. An `upload://` handle names an import local to one
 * machine, so it cannot survive being written to a template and applied elsewhere; the projection drops
 * those rather than writing a reference that resolves nowhere. The panel column choices name columns of
 * that file, so the projection writes them only alongside it.
 *
 * Left out on purpose:
 * - CPU and memory per process: resource allocation belongs to the machine that runs the block, not to
 *   portable configuration;
 * - the sample column: `args` requires the dataset's sample-name map the UI snapshots when the user picks
 *   it, and those sample ids belong to one project;
 * - the combine column and its UMI floor, and the contending groups;
 * - the snapshots themselves, and view state (table and graph states, the block label).
 */
export type BlockParams = {
  // Inputs.
  fbFastqRef?: PlRef;
  datasetRef?: PlRef;
  tagFeatureCsvHandle?: ImportFileHandleIndex;

  // Panel columns, written only with the CSV they name.
  barcodeSeqColumn?: string;
  featureNameColumn?: string;
  roleColumn?: string;
  referenceValues?: string[];
  grouping?: GroupingRule;

  // Read geometry and cell barcodes.
  presetId?: string;
  pattern?: string;
  cellWhitelist?: string;

  // Run scope.
  runMode?: RunMode;
  limitInput?: number;

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
