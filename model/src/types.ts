import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column (from samples-and-data, result pool)
  tagFeatureCsvHandle: ImportFileHandle; // tag->feature CSV, user-uploaded (spec A-0004, A-0009)
  barcodeSeqColumn: string; // CSV column holding the feature barcode (whitelist/panel; spec A-0023)
  featureNameColumn: string; // CSV column holding the feature/antigen name (spec A-0009)
  controlFeature?: string; // negative-control feature name (spec A-0014); omitted -> no score
  dominanceThreshold: number; // spec A-0012, default 0.6, floor 0.5
  pattern: string; // Mitool tag pattern
  // mitool tag names baked into `pattern`
  tags: { cell: string; umi: string; feature: string };
  // Sample-aware tag→feature mapping (optional). When set, the same feature barcode may map to different
  // features per sample.
  sampleColumn?: string; // the CSV column holding the (user-friendly) sample name
  sampleLabels?: Record<string, string>; // a snapshot of sampleId→name
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
  sampleColumn?: string;
  sampleLabelSnapshot?: Record<string, string>;
  // Distinct values of the chosen sample column at pick time — snapshotted alongside the label map so
  // args() can gate Run purely from data (block when a dataset sample has no rows in the CSV).
  sampleColumnValues?: string[];
  dominanceThreshold: number;
  presetId?: string;
  pattern?: string;
  cellWhitelist?: string; // optional (defaults to "" = de-novo); see BlockArgs.cellWhitelist
  defaultBlockLabel?: string; // UI-only: sidebar subtitle, mirrored from the suggestedBlockLabel output
  tableState: PlDataTableStateV2; // per-cell results grid state (UI-only, never projected to args)
  qcSummaryTableState: PlDataTableStateV2; // per-sample QC summary grid state (UI-only)
};
