import type { GraphMakerState } from "@milaboratories/graph-maker";
import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/**
 * Which baseline a count is read against. Selected, never inferred: two runs answered by different rules
 * produce numbers that do not compare, and a scientist who did not choose the rule cannot know that
 * happened.
 *
 * There is no "none". A baseline is required and a run without one does not happen, so an unselected
 * choice is undefined here and `args()` refuses it.
 *
 * `"panel"` is RETIRED and no longer offered. The one tool that implements it decides at the CLONOTYPE,
 * pooling a clone's cells into one vector before anything is tested, so no cell ever holds a state and
 * there is nothing to vote on. The member stays in the union so a project stored under it still parses,
 * and `args()` refuses such a run and names the replacement.
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
 * it keeps running, and `groupingColumns()` is the one place that reads either. Never write it.
 */
export type GroupingRule =
  | { by: "tag" }
  | { by: "property"; columns: string[]; column?: never }
  | { by: "property"; column: string; columns?: never };

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column (from samples-and-data, result pool)
  tagFeatureCsvHandle: ImportFileHandle; // tag->feature CSV, user-uploaded
  barcodeSeqColumn: string; // CSV column holding the feature barcode (whitelist/panel)
  featureNameColumn: string; // CSV column holding the feature/antigen name
  pattern: string; // Mitool tag pattern
  // mitool tag names baked into `pattern`
  tags: { cell: string; umi: string; feature: string };
  // Sample-aware tag->feature mapping (optional). The same feature barcode may map to different features
  // per sample.
  sampleColumn?: string; // the CSV column holding the (user-friendly) sample name
  sampleLabels?: Record<string, string>; // a snapshot of sampleId->name
  // Cell-barcode whitelist for refine-tags CELL correction. "" = de-novo, the default for non-10x and
  // synthetic input. A 10x built-in name such as 737K-august-2016 makes cellIds match the VDJ producer by
  // construction.
  cellWhitelist: string;
  // Optional mitool resource overrides (Advanced Settings). Undefined means workflow defaults: 8 CPUs, and
  // RAM sized by the input-blob formula.
  perProcessCPUs?: number;
  perProcessMemGB?: number;
  // Preview (dry-run): mitool parse processes only the first `limitInput` reads per sample. Omitted means a
  // full run. Mirrors mixcr-clonotyping / demultiplex-fastq "Preview" mode.
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each feature's
  // mode: "sum" = OR, the default, and "all" = AND, where a feature is called only when every member barcode
  // fires. minUmi is the AND per-barcode "fired" floor, defaulting to 1 in the workflow.
  combineColumn?: string;
  minUmi?: number;
  // The aggregate-barcode detection knobs (qc_measures.py AGGREGATE_BARCODE_*). Undefined means the shipped
  // default. They run inside qc_report.py, part of the per-sample mitool fan-out, so moving one re-runs that
  // sample's parse/refine-tags/tag-stat chain.
  aggregateBarcodeIqrMultiplier?: number;
  aggregateBarcodeMinUmiThreshold?: number;
  aggregateBarcodeTopN?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // Everything below reaches emit_verdicts.py through verdict-args.lib.tengo, and nothing below reaches the
  // per-sample mitool fan-out. A change to how the counts are READ therefore recovers every per-sample body
  // from cache and re-runs the verdict stage alone.

  // The single-cell V(D)J dataset ANCHOR (axes [pl7.app/sampleId, pl7.app/vdj/scClonotypeKey],
  // pl7.app/isAnchor). Not a linker ref: the cell linker carries pl7.app/isLinkerColumn and is hidden in
  // tables, so the workflow resolves it from this anchor by name. The anchor is receptor-scoped, which is
  // what lets a BCR + TCR run bring two linkers without a panic. REQUIRED: `args()` refuses a run without
  // one. Optional in this type only because the workflow keeps its now-unreachable no-dataset branch.
  datasetRef?: PlRef;
  // The panel column declaring each tag's role, and the values of it that mark a tag as the comparator.
  roleColumn?: string;
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number; // members the panel needs before its own readings can serve
  // The one condition on reading a count against that tag's own distribution across the sample's cells. It
  // GATES rather than tunes: below it the baseline the rung would produce is wrong rather than conservative,
  // which is why it has no "off". There is no separation condition, because no published test tells a real
  // split from a dent.
  distributionMinCells: number; // cells a sample needs before the rung may serve
  countFloor: number; // counts below this are not evidence of binding
  boundCutoff: number; // specificity score (0-100) at or above which a cell binds
  boundProbability?: number; // the probability a count belongs to the signal component at or above which a cell binds
  expectedBinderFraction?: number; // the share of cells expected to bind, seeding the fitted rung's split
  minVotingCells: number; // a verdict may rest on one cell and say so
  // Share (0-1) of answering cells the majority must reach. Off by default, and off means ABSENT rather
  // than zero: a floor of 0 passes every majority instead of skipping the check.
  minAgreement?: number;
  // The admissibility gate, in comparator UMIs. Undefined means off. Zero would set aside every cell, so
  // the args lambda projects it only when positive.
  gateThreshold?: number;
  grouping?: GroupingRule;
  // Identities declared to contend for one binding site. Canonicalised by the args lambda: each group
  // sorted, groups sorted, groups of fewer than two members dropped.
  contendingGroups?: string[][];

  // The four inherited lines, each undefined meaning the shipped default. All four are round numbers carried
  // over from the field rather than calibrated for this assay. There is no error line for readsPerCellWarn,
  // because the field published one boundary.
  cellBarcodeValidWarn?: number;
  cellBarcodeValidError?: number;
  readsPerCellWarn?: number;
  aggregateBarcodeWarn?: number;
  aggregateBarcodeError?: number;
  undeclaredBarcodeWarn?: number;
  undeclaredBarcodeError?: number;
  usableReadWarn?: number;
  usableReadError?: number;
};

/**
 * What the block knows about the tag->feature CSV without running anything: the panel's headers, the
 * distinct values of each header, and how many non-blank data rows there were. Parsed in the UI, from the
 * file itself. `rowCount` stays optional so a project stored before the count existed keeps opening, and
 * the duplicate-mapping gate skips where it is absent.
 */
export type CsvMeta = {
  columns: string[];
  valuesByColumn: Record<string, string[]>;
  rowCount?: number;
};

/** Unified persisted UI state. */
export type BlockData = {
  fbFastqRef?: PlRef;
  tagFeatureCsvHandle?: ImportFileHandle;
  /**
   * The panel CSV's metadata, tagged with the handle it was read from. That tag is what makes it safe to
   * persist: a snapshot is read only while it matches the CSV currently picked, so a stale one left by a
   * failed clear can never be read against a different file.
   *
   * The ONLY source of the metadata -- no workflow step parses the panel. Filled from the user's disk on a
   * local pick, and from the prerun-imported blob on a remote pick. Absent means the bytes have not
   * arrived yet.
   */
  csvMetaSnapshot?: { handle: ImportFileHandle; meta: CsvMeta };
  /**
   * Why the panel CSV could not be read. Shown to the user rather than logged: with no workflow-side
   * parser to fall back on, a discarded parse error leaves empty dropdowns and nothing that says why.
   */
  csvImportError?: string;
  barcodeSeqColumn?: string;
  featureNameColumn?: string;
  sampleColumn?: string;
  sampleLabelSnapshot?: Record<string, string>;
  // Distinct values of the chosen sample column at pick time, snapshotted alongside the label map so args()
  // can gate Run purely from data.
  sampleColumnValues?: string[];
  // Preview (dry-run) mode. "full", the default, processes every read. "dry" caps mitool parse to
  // `limitInput` reads per sample.
  runMode?: "dry" | "full";
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. See BlockArgs for what each means to the reading.
  combineColumn?: string;
  minUmi?: number;
  // The aggregate-barcode detection knobs. See BlockArgs for what each means to the reading.
  aggregateBarcodeIqrMultiplier?: number;
  aggregateBarcodeMinUmiThreshold?: number;
  aggregateBarcodeTopN?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // See BlockArgs for what each one means to the reading. The notes here are about the DATA layer only.

  /**
   * The single-cell V(D)J dataset anchor, and the block's one optional input. A missing dataset means no
   * clonotype set and so no verdict, and stops nothing.
   */
  datasetRef?: PlRef;
  roleColumn?: string;
  /**
   * The panel's headers as they stood when the role column or the grouping column was picked.
   * emit_verdicts.py exits the whole run when the panel does not carry the column it was given, and args()
   * validates from data alone, so the headers have to BE in data. Snapshotted on the pick gesture, exactly
   * as sampleColumnValues is.
   */
  panelColumnSnapshot?: string[];
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number;
  distributionMinCells: number;
  countFloor: number;
  boundCutoff: number;
  boundProbability?: number;
  expectedBinderFraction?: number;
  minVotingCells: number;
  minAgreement?: number;
  gateThreshold?: number;
  grouping?: GroupingRule;
  // The four inherited lines. See BlockArgs for what each means to the reading.
  cellBarcodeValidWarn?: number;
  cellBarcodeValidError?: number;
  readsPerCellWarn?: number;
  aggregateBarcodeWarn?: number;
  aggregateBarcodeError?: number;
  undeclaredBarcodeWarn?: number;
  undeclaredBarcodeError?: number;
  usableReadWarn?: number;
  usableReadError?: number;
  /**
   * Written on a user gesture only. A watcher that copied the identityOptions model output into data would
   * make that output depend on the data it feeds, and two open clients would race.
   */
  contendingGroups?: string[][];
  punchcardTableState: PlDataTableStateV2; // punchcard grid state (UI-only, never projected to args)
  /**
   * The clonotype whose expansion is open, as the readout grid's own row key. UI-only, never projected to
   * args: opening an expansion must not re-run anything.
   *
   * A whole key rather than a bare string, because the grid hands back a `PTableKey` and `expansionTable`
   * turns it straight into an axis filter. Undefined is load-bearing: the output returns no table while it
   * holds, because a table built with no filter would be every clonotype's rows at once.
   */
  expandedSet?: (string | number)[];
  /**
   * Grid state for the expansion table. UI-only, never projected to args. Optional, unlike the card's own
   * state beside it: a required field would need every stored project migrated, and `createPlDataTableV3`
   * already takes `tableState` as optional.
   */
  expansionTableState?: PlDataTableStateV2;
  /**
   * Grid state for the expansion's BY-CELL face. Separate from the state beside it: the two tabs are
   * different tables over different axes, one row per identity against one row per cell, so a shared state
   * would carry one tab's column order and filters into the other, where none of the column ids resolve.
   */
  cellExpansionTableState?: PlDataTableStateV2;
  // No field narrows which identity columns the punchcard shows, and none should be added. PlAgDataTableV2
  // ships a columns panel and a filters panel, so such a field re-implements in block state what the grid
  // already does, and two narrowing mechanisms can disagree. Every identity column is rendered.
  //
  // No field truncates the punch headers either. A `punchcardIdentities` list or a `punchcardFullLabels`
  // flag stored by an older project is ignored.

  // Snapshotted on the gesture that picks the barcode column, so args() can refuse a mapping that is certain
  // to fail without reading an output. args is data-only, and the csvValuesByColumn / csvRowCount OUTPUTS lag
  // a gesture by one round trip. Same device as sampleColumnValues. Absent where the metadata had not arrived
  // at pick time, or predates rowCount: the gate then does not fire, and the Python guard catches it at the
  // end of the run.
  panelRowCount?: number;
  panelBarcodeDistinct?: number;

  presetId?: string;
  pattern?: string;
  cellWhitelist?: string; // optional (defaults to "" = de-novo); see BlockArgs.cellWhitelist
  // Optional mitool resource overrides (Advanced Settings). Undefined means workflow defaults.
  perProcessCPUs?: number;
  perProcessMemGB?: number;
  defaultBlockLabel?: string; // UI-only: sidebar subtitle, mirrored from the suggestedBlockLabel output
  tableState: PlDataTableStateV2; // per-cell results grid state (UI-only, never projected to args)
  qcSummaryTableState: PlDataTableStateV2; // per-sample QC summary grid state (UI-only)
  // The Run quality page's two grids (UI-only). Deliberately NOT named `antigenQcTableState` /
  // `panelMismatchTableState`: the v3 -> v4 migration strips those two keys. A stored grid state means
  // something only against the frame it was saved on.
  runQualityTableState: PlDataTableStateV2; // run-level quality measurements grid state
  // GraphMaker's own chart configuration, one per plot. Opaque to this block: the widget owns the shape and
  // reads it back. Separate rather than shared, so picking an axis on one chart does not move another.
  scoreDistributionGraphState: GraphMakerState;
  referenceReadingGraphState: GraphMakerState;
  fittedBackgroundGraphState: GraphMakerState;
  reagentTableState: PlDataTableStateV2; // per (tag, identity) reagent grid state
  // Barcodes the reads carried that no panel declares, keyed by sequence.
  undeclaredBarcodesTableState: PlDataTableStateV2;
};
