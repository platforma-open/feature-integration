import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column (from samples-and-data, result pool)
  tagFeatureCsvHandle: ImportFileHandle; // tag->feature CSV, user-uploaded
  barcodeSeqColumn: string; // CSV column holding the feature barcode (whitelist/panel)
  featureNameColumn: string; // CSV column holding the feature/antigen name
  controlFeature?: string; // negative-control feature name; omitted -> no score
  dominanceThreshold: number; // default 0.6, floor 0.5
  pattern: string; // Mitool tag pattern
  // mitool tag names baked into `pattern`
  tags: { cell: string; umi: string; feature: string };
  // Sample-aware tag→feature mapping (optional). When set, the same feature barcode may map to different
  // features per sample.
  sampleColumn?: string; // the CSV column holding the (user-friendly) sample name
  sampleLabels?: Record<string, string>; // a snapshot of sampleId→name
  // Cell-barcode whitelist for refine-tags CELL correction. "" = de-novo (default; non-10x/synthetic).
  // A 10x built-in name (e.g. 737K-august-2016) makes cellIds match the VDJ producer by construction.
  cellWhitelist: string;
  // Optional mitool resource overrides (Advanced Settings). Undefined -> workflow defaults (4 CPUs; RAM
  // sized by the input-blob formula). When set, perProcessMemGB is a hard fixed RAM request per sample.
  perProcessCPUs?: number;
  perProcessMemGB?: number;
  // Preview (dry-run): when set, mitool parse processes only the first `limitInput` reads per sample so
  // the user can sanity-check settings before the full run. Omitted -> full run (all reads). Mirrors
  // mixcr-clonotyping / demultiplex-fastq "Preview" mode.
  limitInput?: number;
  // Optional off-target designation (F2). offtargetProperty names an imported per-feature property column
  // (e.g. antigen_class); offtargetValues are that column's values marking a feature as off-target. Such
  // features are excluded from the dominant call (like the control) and enable the "cross-reactive" label.
  // Both present -> off-target-aware; omitted -> unchanged dominant call.
  offtargetProperty?: string;
  offtargetValues?: string[];
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
  // Preview (dry-run) mode. "full" (default) processes all reads; "dry" caps mitool parse to `limitInput`
  // reads per sample so the user can check settings first. Mirrors mixcr-clonotyping / demultiplex-fastq.
  runMode?: "dry" | "full";
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
  // feature's mode ("sum" = OR, the default; "all" = AND, feature called only when every member
  // barcode fires). minUmi is the AND per-barcode "fired" floor (integer >= 1; workflow default 1).
  combineColumn?: string;
  minUmi?: number;
  // Optional off-target designation (F2). offtargetProperty names an imported per-feature property column
  // (e.g. antigen_class); offtargetValues are that column's values marking a feature as off-target. Both
  // present -> the dominant call excludes those features and enables the "cross-reactive" label.
  offtargetProperty?: string;
  offtargetValues?: string[];
  presetId?: string;
  pattern?: string;
  cellWhitelist?: string; // optional (defaults to "" = de-novo); see BlockArgs.cellWhitelist
  // Optional mitool resource overrides (Advanced Settings); undefined = workflow defaults.
  perProcessCPUs?: number;
  perProcessMemGB?: number;
  defaultBlockLabel?: string; // UI-only: sidebar subtitle, mirrored from the suggestedBlockLabel output
  tableState: PlDataTableStateV2; // per-cell results grid state (UI-only, never projected to args)
  qcSummaryTableState: PlDataTableStateV2; // per-sample QC summary grid state (UI-only)
};
