import type { GraphMakerState } from "@milaboratories/graph-maker";
import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column (from samples-and-data, result pool)
  tagFeatureCsvHandle: ImportFileHandle; // tag->feature CSV, user-uploaded (spec A-0004, A-0009)
  barcodeSeqColumn: string; // CSV column holding the feature barcode (whitelist/panel; spec A-0023)
  featureNameColumn: string; // CSV column holding the feature/antigen name (spec A-0009)
  controlFeature?: string; // negative-control feature name (spec A-0014); omitted -> no score
  dominanceThreshold: number; // spec A-0012, default 0.6, floor 0.5
  // Read geometry for the mitool tag pattern (DP-1 "parameterize + proceed"; 10x 5' v2 defaults).
  // The exact assay geometry must be confirmed against real FASTQs (Task 0); it is configurable here
  // rather than hardcoded, with a clean seam for a future whitelist-translation step.
  cellLen: number; // cell barcode length on R1 (10x 5' v2: 16)
  umiLen: number; // UMI length on R1 (10x 5' v2: 10)
  featureLen: number; // feature barcode length on R2 (assay-specific; default 15)
  // Cell-barcode whitelist for refine-tags CELL correction. "" = de-novo (default; non-10x/synthetic).
  // A 10x built-in name (e.g. 737K-august-2016) makes cellIds match the VDJ producer by construction.
  // See docs/dormant-features/cell-whitelist-correction-plan.md.
  cellWhitelist: string;
};

/** Unified persisted UI state. */
export type BlockData = {
  fbFastqRef?: PlRef;
  tagFeatureCsvHandle?: ImportFileHandle;
  barcodeSeqColumn?: string;
  featureNameColumn?: string;
  controlFeature?: string;
  dominanceThreshold: number;
  cellLen: number;
  umiLen: number;
  featureLen: number;
  cellWhitelist?: string; // optional (defaults to "" = de-novo); see BlockArgs.cellWhitelist
  defaultBlockLabel?: string; // UI-only: sidebar subtitle, mirrored from the suggestedBlockLabel output
  tableState: PlDataTableStateV2; // per-cell results grid state (UI-only, never projected to args)
  tagstatTableState: PlDataTableStateV2; // raw tag-stat QC grid state (UI-only)
  qcSummaryTableState: PlDataTableStateV2; // per-sample QC summary grid state (UI-only)
  graphState: GraphMakerState; // violin-plot graph tab state (UI-only, never projected to args)
};
