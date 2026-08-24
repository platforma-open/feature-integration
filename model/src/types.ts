import type { GraphMakerState } from "@milaboratories/graph-maker";
import type { ImportFileHandle, PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/**
 * Which baseline a count is read against. Selected, never inferred: two runs answered by different rules
 * produce numbers that do not compare, and a scientist who did not choose the rule cannot know that
 * happened.
 *
 * There is no "none". A baseline is required and a run without one does not happen, so an unselected
 * choice is undefined here and `args()` refuses it -- rather than a fourth value meaning "answer every
 * position unreliable", which costs what a real run costs and looks like a result at a glance.
 */
/**
 * `"panel"` is RETIRED and no longer offered. `292-what-plays-the-baseline@8.2.1` names it "a fourth
 * possibility ... named and not built", and the reason it gives is not panel size: the one tool that
 * implements it decides at the CLONOTYPE, pooling a clone's cells into one vector before anything is
 * tested, so no cell ever holds a state and there is nothing to vote on. Adopting it would carve a
 * clonotype-level exception through the middle of the verdict model. `060-parameter-set@2.0.0` drops the
 * 25-tag parameter with it.
 *
 * The member stays in the union so a project stored under it still parses. `args()` refuses such a run and
 * names the replacement, rather than moving the choice itself -- a baseline nobody chose is a methodology
 * nobody knows they used.
 */
export type ReferenceSource = "declared" | "panel" | "distribution";

/**
 * How tags become identities. A RULE over declared properties, never a tag->identity map: a map is keyed
 * by tags, which are known only after the block runs, so any editor for it writes an output back into
 * data. Property column names are knowable at prerun, from the panel header the block already enumerates.
 * Absent means one identity per tag.
 *
 * Several columns may be named, and the identity is the distinct combination of their values. Name
 * antigen and concentration together, and the same antigen at two concentrations is two identities.
 *
 * `column` is the shape this rule had before it took a list. It stays readable because a project stored
 * under it must keep running, and `groupingColumns()` is the one place that reads either. Never write it.
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
  // Sample-aware tag->feature mapping (optional). When set, the same feature barcode may map to different
  // features per sample.
  sampleColumn?: string; // the CSV column holding the (user-friendly) sample name
  sampleLabels?: Record<string, string>; // a snapshot of sampleId->name
  // Cell-barcode whitelist for refine-tags CELL correction. "" = de-novo, the default for non-10x and
  // synthetic input. A 10x built-in name such as 737K-august-2016 makes cellIds match the VDJ producer by
  // construction.
  cellWhitelist: string;
  // Optional mitool resource overrides (Advanced Settings). Undefined means workflow defaults: 8 CPUs, and
  // RAM sized by the input-blob formula. When set, perProcessMemGB is a fixed RAM request per sample.
  perProcessCPUs?: number;
  perProcessMemGB?: number;
  // Preview (dry-run): when set, mitool parse processes only the first `limitInput` reads per sample, so
  // the user can check settings before the full run. Omitted means a full run, every read. Mirrors
  // mixcr-clonotyping / demultiplex-fastq "Preview" mode.
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
  // feature's mode: "sum" = OR, the default, and "all" = AND, where a feature is called only when every
  // member barcode fires. minUmi is the AND per-barcode "fired" floor, an integer >= 1 defaulting to 1 in
  // the workflow.
  combineColumn?: string;
  minUmi?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // Everything below reaches emit_verdicts.py through verdict-args.lib.tengo, and nothing below reaches
  // the per-sample mitool fan-out. A change to how the counts are READ therefore recovers every per-sample
  // body from cache and re-runs the verdict stage alone.

  // The single-cell V(D)J dataset ANCHOR (axes [pl7.app/sampleId, pl7.app/vdj/scClonotypeKey],
  // pl7.app/isAnchor). Not a linker ref: the cell linker carries pl7.app/isLinkerColumn and is hidden in
  // tables, so no user can pick it, and the workflow resolves it from this anchor by name. The anchor is
  // receptor-scoped, so choosing the dataset is choosing the receptor, which is what lets a BCR + TCR run
  // bring two linkers without a panic. REQUIRED: `args()` refuses a run without one. It stays optional in
  // this type because the workflow still carries its no-dataset branch, which is now unreachable and left
  // as a guard rather than deleted.
  datasetRef?: PlRef;
  // The panel column declaring each tag's role, and the values of it that mark a tag as the comparator.
  roleColumn?: string;
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number; // members the panel needs before its own readings can serve
  // The one condition on reading a count against that tag's own distribution across the sample's cells.
  // It GATES rather than tunes: below it the baseline the rung would produce is wrong rather than
  // conservative, which is why it has no "off". There is no separation condition -- the rung's own atom
  // refuses one, because no published test tells a real split from a dent and inventing one here would be
  // this block deciding what the method leaves to the eye.
  distributionMinCells: number; // cells a sample needs before the rung may serve
  countFloor: number; // counts below this are not evidence of binding
  boundCutoff: number; // specificity score (0-100) at or above which a cell binds
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
};

/**
 * What the block knows about the tag->feature CSV without running anything: the panel's headers, the
 * distinct values of each header, and how many non-blank data rows there were.
 *
 * Parsed in the UI, from the file itself. `rowCount` stays optional because a project stored before the
 * count existed must keep opening, and the duplicate-mapping gate skips where it is absent.
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
   * The panel CSV's metadata, parsed in the UI, tagged with the handle it was read from.
   *
   * The handle tag is what makes it safe to persist: a snapshot is read only while it matches the CSV
   * currently picked, so a stale one left by a failed clear can never be read against a different file.
   *
   * This is the ONLY source of the metadata -- no workflow step parses the panel. The UI fills it from the
   * user's disk on a local pick, and from the prerun-imported blob for a remote pick. Absent means the
   * bytes have not arrived yet, which the "Reading columns..." alert reports.
   */
  csvMetaSnapshot?: { handle: ImportFileHandle; meta: CsvMeta };
  /**
   * Why the panel CSV could not be read, or undefined where it could. Shown to the user rather than
   * logged: with no workflow-side parser to fall back on, a discarded parse error leaves empty dropdowns
   * and nothing that says why.
   */
  csvImportError?: string;
  barcodeSeqColumn?: string;
  featureNameColumn?: string;
  sampleColumn?: string;
  sampleLabelSnapshot?: Record<string, string>;
  // Distinct values of the chosen sample column at pick time, snapshotted alongside the label map so
  // args() can gate Run purely from data. Run is blocked when a dataset sample has no rows in the CSV.
  sampleColumnValues?: string[];
  // Preview (dry-run) mode. "full", the default, processes every read. "dry" caps mitool parse to
  // `limitInput` reads per sample so the user can check settings first. Mirrors mixcr-clonotyping and
  // demultiplex-fastq.
  runMode?: "dry" | "full";
  limitInput?: number;
  // Optional multi-barcode antigen combine mode. combineColumn names a tag-CSV column giving each
  // feature's mode: "sum" = OR, the default, and "all" = AND, where a feature is called only when every
  // member barcode fires. minUmi is the AND per-barcode "fired" floor, an integer >= 1 defaulting to 1.
  combineColumn?: string;
  minUmi?: number;

  // --- the binding reading -------------------------------------------------------------------------
  // See BlockArgs for what each one means to the reading. The notes here are about the DATA layer only.

  /**
   * The single-cell V(D)J dataset anchor, and the block's one optional input. A missing dataset narrows
   * what the block can answer, since no clonotype set means no verdict, and stops nothing. The args lambda
   * never throws on its absence.
   */
  datasetRef?: PlRef;
  roleColumn?: string;
  /**
   * The panel's headers as they stood when the role column or the grouping column was picked. Both of
   * those name a panel column, and emit_verdicts.py exits the whole run when the panel does not carry the
   * one it was given. The user meets that as a dead run, not as a message about the setting that caused
   * it. args() validates from data alone, so the headers have to BE in data. They are snapshotted on the
   * pick gesture, exactly as sampleColumnValues is.
   */
  panelColumnSnapshot?: string[];
  referenceValues?: string[];
  referenceSource?: ReferenceSource;
  panelReferenceMinMembers: number;
  distributionMinCells: number;
  countFloor: number;
  boundCutoff: number;
  minVotingCells: number;
  minAgreement?: number;
  gateThreshold?: number;
  grouping?: GroupingRule;
  /**
   * Written on a user gesture only. The identities to choose from come from the identityOptions model
   * output, and a watcher that copied that output into data would make the model output depend on the data
   * it feeds -- a write-on-read loop, and a write race between two open clients.
   */
  contendingGroups?: string[][];
  punchcardTableState: PlDataTableStateV2; // punchcard grid state (UI-only, never projected to args)
  /**
   * The clonotype whose expansion is open, as the readout grid's own row key, or undefined when none is.
   * UI-only, never projected to args: opening an expansion must not re-run anything.
   *
   * A whole key rather than a bare string, because the grid hands back a `PTableKey` and `expansionTable`
   * turns it straight into an axis filter. Undefined is load-bearing rather than an empty state: the
   * output returns no table while it holds, because a table built with no filter would be every
   * clonotype's rows at once, the one outcome the expansion exists to avoid.
   */
  expandedSet?: (string | number)[];
  /**
   * Grid state for the expansion table. UI-only, never projected to args.
   *
   * Optional, unlike the card's own state beside it. A required field would need every stored project
   * migrated to carry it, and `createPlDataTableV3` already takes `tableState` as optional. A project that
   * predates the expansion opens with a default grid instead of failing to open.
   */
  expansionTableState?: PlDataTableStateV2;
  /**
   * Grid state for the expansion's BY-CELL face. It has to be separate from the state beside it: the two
   * tabs are different tables over different axes, one row per identity against one row per cell, so a
   * shared state would carry one tab's column order and filters into the other, where none of the column
   * ids resolve. Optional for the same reason as above.
   */
  cellExpansionTableState?: PlDataTableStateV2;
  // No field narrows which identity columns the punchcard shows, and none should be added.
  // PlAgDataTableV2 ships a columns panel and a filters panel, so such a field re-implements in block state
  // what the grid already does, and two narrowing mechanisms can disagree where the grid's own cannot
  // disagree with itself. Every identity column is rendered.
  //
  // No field truncates the punch headers either. A cut header hides which identity a column is, which is
  // what a reader needs from it most. Every column is resizable, and the punch hover carries the name. A
  // `punchcardIdentities` list or a `punchcardFullLabels` flag stored by an older project is ignored.

  // Snapshotted on the gesture that picks the barcode column, so args() can refuse a mapping that is
  // certain to fail without reading an output. args is data-only, and these are read from the
  // csvValuesByColumn / csvRowCount OUTPUTS, which lag a gesture by one round trip even though the metadata
  // they derive from is now in data. Same device as sampleColumnValues, for the same reason. Absent where
  // the metadata had not arrived at pick time, or predates rowCount. The gate then does not fire, and the
  // Python guard catches it at the end of the run.
  //
  // Both could be dropped now that csvMetaSnapshot puts the same numbers in data, where args() could read
  // them directly. That is a migration and a change to the gate, so it is deliberately left alone here.
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
  // `panelMismatchTableState`: the v3 -> v4 migration strips those two keys. Reusing them would either
  // fight that strip or resurrect a column set and filter saved against a frame nobody has looked at
  // since. A stored grid state means something only against the frame it was saved on.
  runQualityTableState: PlDataTableStateV2; // run-level quality measurements grid state
  // GraphMaker's own chart configuration, one per plot. Opaque to this block: the widget owns the
  // shape and reads it back. Separate rather than shared, because picking an axis on one chart must
  // not move another.
  scoreDistributionGraphState: GraphMakerState;
  referenceReadingGraphState: GraphMakerState;
  fittedBackgroundGraphState: GraphMakerState;
  runQualityMismatchTableState: PlDataTableStateV2; // panel-versus-reads mismatch grid state
  reagentTableState: PlDataTableStateV2; // per (tag, identity) reagent grid state
};
