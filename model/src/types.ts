import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/**
 * Which comparator a count is read against. Selected, never inferred: two runs answered by different
 * rules produce numbers that do not compare, and a scientist who did not choose the rule cannot know
 * that happened. Undefined means the default for this panel — a declared reference tag where one
 * exists, and otherwise no comparator at all.
 */
export type ReferenceSource = "declared" | "panel" | "none";

/**
 * How tags become identities. A RULE over declared properties, never a tag->identity map: a map is
 * keyed by tags, which are known only after the block runs, so any editor for it writes an output
 * back into data. A property column name is knowable at prerun, from the panel header the block
 * already enumerates. Absent means one identity per tag.
 */
export type GroupingRule = { by: "tag" } | { by: "property"; column: string };

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column (from samples-and-data, result pool)
  tagFeatureCsvHandle: ImportFileHandle; // tag->feature CSV, user-uploaded
  barcodeSeqColumn: string; // CSV column holding the feature barcode (whitelist/panel)
  featureNameColumn: string; // CSV column holding the feature/antigen name
  // Negative-control feature name. It no longer gates any per-cell rule — the verdict asks the binding
  // question of every antigen independently — but main.tpl.tengo still passes it to
  // emit_feature_properties.py as --control-feature, which emits the pl7.app/feature/negativeControl
  // marker column consumers read. Omitted -> that marker is header-only.
  controlFeature?: string;
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
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
  // feature's mode ("sum" = OR, the default; "all" = AND, feature called only when every member barcode
  // fires). minUmi is the AND per-barcode "fired" floor (integer >= 1; workflow default 1).
  combineColumn?: string;
  minUmi?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // Everything below reaches emit_verdicts.py through verdict-args.lib.tengo, and nothing below reaches
  // the per-sample mitool fan-out: a change to how the counts are READ recovers every per-sample body
  // from cache and re-runs the verdict stage alone.

  // The single-cell V(D)J dataset ANCHOR (axes [pl7.app/sampleId, pl7.app/vdj/scClonotypeKey],
  // pl7.app/isAnchor). Not a linker ref: the cell linker carries pl7.app/isLinkerColumn and is hidden in
  // tables, so it is not a column a user can pick, and the workflow resolves it from this anchor by name.
  // Because the anchor is receptor-scoped, choosing the dataset is choosing the receptor — which is what
  // lets a BCR + TCR run bring two linkers without a panic. Optional: without it the block still emits
  // every column not keyed by a clonotype set, and only the verdict stage is skipped.
  datasetRef?: PlRef;
  // The panel column declaring each tag's role, and the values of it that mark a tag as the comparator.
  roleColumn?: string;
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number; // members the panel needs before its own readings can serve
  referenceThinLine: number; // below this the comparator rests on too little to compare against
  countFloor: number; // counts below this are not evidence of binding
  boundCutoff: number; // specificity score (0-100) at or above which a cell binds
  minVotingCells: number; // a verdict may rest on one cell and say so
  // Share (0-1) of answering cells the majority must reach. Off by default, and off means ABSENT rather
  // than zero: a floor of 0 makes every majority pass the check instead of skipping the check.
  minAgreement?: number;
  // The admissibility gate, in comparator UMIs. Undefined means off; zero would set aside every cell,
  // so the args lambda projects it only when positive.
  gateThreshold?: number;
  highReferenceLine: number; // where a reference reading counts as high, with the gate off
  grouping?: GroupingRule;
  // Identities declared to contend for one binding site. Canonicalised by the args lambda (each group
  // sorted, groups sorted, groups of fewer than two members dropped).
  contendingGroups?: string[][];
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
  // Preview (dry-run) mode. "full" (default) processes all reads; "dry" caps mitool parse to `limitInput`
  // reads per sample so the user can check settings first. Mirrors mixcr-clonotyping / demultiplex-fastq.
  runMode?: "dry" | "full";
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
  // feature's mode ("sum" = OR, the default; "all" = AND, feature called only when every member
  // barcode fires). minUmi is the AND per-barcode "fired" floor (integer >= 1; workflow default 1).
  combineColumn?: string;
  minUmi?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // See BlockArgs for what each one means to the reading; the notes here are about the DATA layer only.

  /**
   * The single-cell V(D)J dataset anchor, and the block's one optional input. A missing dataset narrows
   * what the block can answer — no clonotype set means no verdict — and stops nothing: the args lambda
   * never throws on its absence.
   */
  datasetRef?: PlRef;
  roleColumn?: string;
  /**
   * The panel's headers as they stood when the role column or the grouping column was picked. Both of
   * those name a panel column, and emit_verdicts.py exits the whole run when the panel does not carry the
   * one it was given — a failure the user meets as a dead run rather than as a message about the setting
   * that caused it. args() validates from data alone, so the headers have to BE in data; they are
   * snapshotted on the pick gesture, exactly as sampleColumnValues is.
   */
  panelColumnSnapshot?: string[];
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number;
  referenceThinLine: number;
  countFloor: number;
  boundCutoff: number;
  minVotingCells: number;
  minAgreement?: number;
  gateThreshold?: number;
  highReferenceLine: number;
  grouping?: GroupingRule;
  /**
   * Written on a user gesture only. The identities to choose from come from the identityOptions model
   * output, and a watcher that copied that output into data would make the model output depend on the
   * data it feeds — a write-on-read loop, and a write race between two open clients.
   */
  contendingGroups?: string[][];
  verdictTableState: PlDataTableStateV2; // verdict grid state (UI-only, never projected to args)
  /**
   * Grid state for the two halves of the run's own report — the quality measurements and the
   * panel-versus-reads check. Separate states because they are separate frames on separate keys: the
   * measurements are keyed (level, panel, measured thing, measurement) and the check is keyed
   * (panel, tag), so a column set or filter saved for one means nothing in the other. UI-only.
   */
  antigenQcTableState: PlDataTableStateV2;
  panelMismatchTableState: PlDataTableStateV2;

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
