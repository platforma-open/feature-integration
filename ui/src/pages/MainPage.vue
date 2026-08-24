<script setup lang="ts">
import type { PlAgHeaderComponentParams } from "@platforma-sdk/ui-vue";
import {
  AgGridTheme,
  PlAccordionSection,
  PlAgCellStatusTag,
  PlAgChartStackedBarCell,
  PlAgOverlayLoading,
  PlAgOverlayNoRows,
  PlAlert,
  PlCheckbox,
  PlBlockPage,
  PlBtnGhost,
  PlBtnGroup,
  PlDropdown,
  PlDropdownMulti,
  PlDropdownRef,
  PlFileInput,
  PlLogView,
  PlMaskIcon24,
  PlNumberField,
  PlRow,
  PlSectionSeparator,
  PlSlideModal,
  autoSizeRowNumberColumn,
  createAgGridColDef,
  makeRowNumberColDef,
} from "@platforma-sdk/ui-vue";
import type {
  GroupingRule,
  ReferenceSource,
} from "@platforma-open/milaboratories.feature-integration.model";
import { groupingColumns } from "@platforma-open/milaboratories.feature-integration.model";
import type { ImportFileHandle } from "@platforma-sdk/model";
import { parseTagCsvMeta } from "../csvMeta";
import { readLocalCsvMeta, useRemoteCsvBytes } from "../csvSource";
import type { ColDef, GridReadyEvent } from "ag-grid-enterprise";
import { ClientSideRowModelModule, ModuleRegistry } from "ag-grid-enterprise";
import { AgGridVue } from "ag-grid-vue3";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import PatternEditor from "../components/PatternEditor.vue";
import SampleReportPanel from "./SampleReportPanel.vue";
import {
  sampleResults,
  type ProgressCell,
  type QcStatus,
  type RecoveryBar,
  type SampleResult,
} from "../results";

const app = useApp();
// Auto-open Settings for a fresh block, with no FASTQ chosen yet. Stays closed once configured.
const settingsOpen = ref(app.model.data.fbFastqRef === undefined);
// Close the Settings drawer once a run starts. Watching an output and writing a local ref is not a hairpin,
// since nothing writes to server-stored data.
watch(
  () => app.model.outputs.isRunning,
  (running) => {
    if (running) settingsOpen.value = false;
  },
);

// The block's "Analysis logs": a live completed-sample heartbeat while the run is in progress, then a
// run-level summary once it finishes. The model builds the lines from the per-sample QC. Shown in a wide
// slide-over as one text area. Detailed per-sample statistics live on the QC page.
const analysisLog = computed(() => app.model.outputs.analysisLog ?? []);
// First line of the Analysis-logs drawer, pointing the user at the richer per-sample logs behind a
// double-click on each sample row. The run-level analysisLog below is only a summary heartbeat.
const LOGS_HINT =
  "Tip: double-click any sample in the progress table to open its own detailed per-step logs (parse, refine tags, count UMIs).";
const logText = computed(() => [LOGS_HINT, "", ...analysisLog.value].join("\n"));
const logsOpen = ref(false);

// Per-sample report slide-over of live per-step mitool logs. Opened by double-clicking a grid row, and
// shown whenever a sample is selected.
const selectedSample = ref<string | undefined>(undefined);
const sampleReportOpen = computed({
  get: () => selectedSample.value !== undefined,
  set: (open: boolean) => {
    if (!open) selectedSample.value = undefined;
  },
});
const selectedSampleLabel = computed(() =>
  selectedSample.value !== undefined
    ? (app.model.outputs.sampleLabels?.[selectedSample.value] ?? selectedSample.value)
    : "",
);

// True while the panel CSV has been picked but not yet read: the handle is set and csvMetaSnapshot is not.
// For a local pick that window is a single tick, so the note below never appears. For a remote pick it lasts
// until the upload lands and the blob watcher parses it. Drives a "reading columns..." note and disables the
// CSV-derived dropdowns, so their empty state reads as "loading" rather than "no columns".
const csvProcessing = computed(() => app.model.outputs.csvColumnsLoading === true);

// The CSV-derived tag-mapping dropdowns (barcode / feature / control / sample columns) have nothing to offer
// until a tag-feature CSV is picked AND its columns are read. Disable and dim them while no CSV handle
// exists, or while the panel has not been read yet, so their empty state reads as "waiting for a CSV" rather
// than "no columns found". Reuses the SDK disabled and dimmed affordance the parse window (csvProcessing)
// already uses.
const tagMappingDisabled = computed(
  () => !app.model.data.tagFeatureCsvHandle || csvProcessing.value,
);

// CSV columns not already bound to the barcode-sequence or feature-name roles. A column holding DNA barcodes
// or antigen names is not a sample column, and offering it invites a mis-pick. The data layer refuses two
// roles on one column, but only at the end of the run. The model's args() rejects the collision too, and
// filtering here prevents the mistake up front.
const roleFreeColumnOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) =>
      o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.featureNameColumn,
  ),
);

// The panel's headers as they stand now. Snapshotted into data on the gesture that names a panel column, so
// args() can refuse a column the panel does not carry without reaching outside data. Left to a watcher this
// would be an output written back into data, which two open clients would race to write.
function snapshotPanelColumns() {
  app.model.data.panelColumnSnapshot = (app.model.outputs.csvColumnOptions ?? []).map(
    (o) => o.value,
  );
}

// ---- the binding reading ----------------------------------------------------------------------
// The settings for the binding reading. Rendered in the Main page's Settings drawer, and there ONLY. A block
// puts its settings in one place, and the Explore readout offered this same drawer until that second copy was
// removed. Do not mount it from a results page again: two drawers editing one set of controls is not the
// idiom, whatever it costs to recompute.
//
// Everything this component EDITS is below the "binding reading" line in BlockArgs, so a change here recovers
// every per-sample mitool body from cache and re-runs the verdict stage alone. It also READS three fields it
// must never edit -- tagFeatureCsvHandle, barcodeSeqColumn, sampleColumn -- to tell whether the panel has
// loaded, and to keep a role or grouping setting from naming a column the panel reader consumes as a key.
// Those three force the whole per-sample fan-out to re-run, and the Main page owns their controls.
//
// VOCABULARY, and the split is deliberate. Everything a USER reads says "baseline": the level a count must
// exceed, measured in the same cell from a tag declared to bind nothing. The DATA layer keeps `reference` --
// `ReferenceSource`, the run-meta keys, the p-column domain values -- and those cannot follow, because domain
// is part of column identity and renaming one would change what every emitted column IS. Code comments here
// describe the data layer, so they still say reference and comparator.
//
// One user-facing word, never four. "control" is not it. The glossary keeps control and reference apart --
// being a control is a property of the tag, and a panel may carry several, where being the reference that
// supplies the baseline is a job given to exactly one of them. This form nominates the reference, so it says
// baseline throughout and never "control".

// The distinct values of the chosen role column -- what the comparator is designated by.
const roleValueOptions = computed(() => {
  const column = app.model.data.roleColumn;
  if (!column) return [];
  return (app.model.outputs.csvValuesByColumn?.[column] ?? []).map((v) => ({
    value: v,
    label: v,
  }));
});

// Changing the role column drops the values chosen under the old one. They designate values of THIS column,
// and left behind they would mark no tag while still reading as a configured comparator.
//
// Declaring a baseline is also what the baseline CHOICE was made against, so changing the declaration drops
// the choice too. An override means "I want this rung given what I have declared", not a standing instruction
// that outlives the declaration it answered. Left behind, marking a baseline tag could not move the field
// onto it, which reads as the block ignoring what you just declared.
//
// The same rule the two settings either side of it follow: a setting does not outlive the thing it was chosen
// against. Only on a GESTURE, never from a watcher -- a watcher on a model output writing back into data is
// the hairpin, and two clients with the project open would race on it.
function clearBaselineChoice() {
  app.model.data.referenceSource = undefined;
}

function setRoleColumn(column: string | undefined) {
  app.model.data.roleColumn = column || undefined;
  app.model.data.referenceValues = undefined;
  clearBaselineChoice();
  snapshotPanelColumns();
}

// ONE value, stored as a one-element list. `040-glossary` splits the two cardinalities: being a control is
// a property of the tag and a panel may carry several, but the reference is one job given to one of them.
// So the value that marks the baseline is singular. Several values here only ever described a panel whose
// role column spells one role more than one way, and a panel that does that is asking to be corrected
// rather than accommodated.
//
// `data.referenceValues` stays a LIST. The field name, the `--reference-values` flag and every stored
// project keep their shape, so this tightens the control without a migration. The `> 1 tag` refusal in
// `verdict.py` stays too, and is now the only thing that can fire: one value can still mark several tags,
// which is a panel fact this control cannot see.
function setReferenceValue(value: string | undefined) {
  app.model.data.referenceValues = value ? [value] : undefined;
  clearBaselineChoice();
}

// The comparator sources this panel can serve. Both the option list and the reasons come from a model output
// rather than from a watcher: the facts behind them are the panel's, and copying them into data would make
// the output depend on the data it feeds.
const referenceSources = computed(() => app.model.outputs.referenceSources);

// The rungs this panel can serve, and the rung the run will be answered under. Both come from model outputs:
// the option list from `referenceSources`, the shown value from `effectiveReferenceSource`.
//
// NEVER derive the shown value here. Writing the rule twice -- once to decide what to display, once in
// `args()` to decide what to send -- makes the field lie the moment a stored choice stops being serviceable.
// Clearing the role values then leaves a dead "declared" behind: this component re-renders as "the panel's
// own readings" while the data still holds "declared", so the form shows a scientist the exact value they are
// being asked to supply while Run stays greyed out, and only re-picking the already-shown value fixes it.
//
// No derivation exists anywhere. The block does not choose a baseline, because a baseline nobody chose is a
// methodology nobody knows they used. `effectiveReferenceSource` is the stored choice, or the bottom rung
// where none was made. Reading an output to display it is not a hairpin: nothing here writes back.
// EVERY rung, always, and every one selectable. The scientist picks the rung first and the form then asks
// for what that rung needs. Offering only the rungs already satisfied made the declared rung unreachable:
// its requirements are the two fields that appear once it is chosen, so it could never become serviceable
// while it was hidden.
const allSources = computed(() => referenceSources.value?.options ?? []);

// The chosen rung, from DATA. Each baseline brings its own rule, so a setting belonging to one rule is
// shown only where that rule runs. Read from data and never from `effectiveReferenceSource`: the form
// reveals fields against what the scientist picked, and that must not move on its own.
const chosenSource = computed(() => app.model.data.referenceSource);

// What the chosen rung still needs, if anything. The model computes it, because whether a rung can serve
// turns on panel facts this component must not re-derive.
const chosenNeeds = computed(
  () => allSources.value.find((o) => o.value === chosenSource.value)?.needs,
);
const shownSource = computed(() => app.model.outputs.effectiveReferenceSource);
// Whether the scientist has actually chosen. Read from data rather than from the output above, which answers
// "none" both for an explicit choice of no baseline and for no choice at all. The two look the same to a run
// and are opposite things to say to a reader.
const baselineUnchosen = computed(() => app.model.data.referenceSource === undefined);

// A checkbox binds a boolean, and the field is optional in data so a project that never touched it carries no
// key. `undefined` reads as false here, and the setter writes `undefined` back rather than `false`, which
// keeps such a project's args vector unchanged.
const minimumAppliesToBaseline = computed({
  get: () => app.model.data.minimumAppliesToBaseline === true,
  set: (on: boolean) => {
    app.model.data.minimumAppliesToBaseline = on ? true : undefined;
  },
});

function setBaselineSource(value: string | undefined) {
  app.model.data.referenceSource = value === undefined ? undefined : (value as ReferenceSource);
}

// The identities the contending-groups editor picks from, live from the uploaded panel.
const identityOptions = computed(() => app.model.outputs.identityOptions ?? []);
// The panel-derived dropdowns have nothing to offer until the panel file is uploaded and staging has read
// its columns. Disabled and dimmed, so their empty state reads as "waiting" rather than "nothing found".
const panelUnread = computed(
  () => !app.model.data.tagFeatureCsvHandle || app.model.outputs.csvColumnsLoading === true,
);

// The panel's PROPERTY columns: every header except the ones the panel reader consumes as keys, which are
// the barcode column and the sample column where one is set. Mirrors panel.py's own rule. A column the
// reader strips is not one the grouping setting may name, and emit_verdicts.py ends the run rather than
// degrading when handed one.
const panelPropertyOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) => o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.sampleColumn,
  ),
);

// One control for the whole rule, taking SEVERAL columns: an identity is the distinct combination of the
// named columns' values, so naming antigen and concentration together makes the same antigen at two
// concentrations two identities.
//
// The barcode column sits in the same list as the property columns, because naming it IS a grouping -- the
// finest one available, one identity per barcode -- rather than a mode beside grouping. It cannot be offered
// as a property column, since the panel reader consumes it as the `tag` key, so it maps to the `tag` rule,
// which produces exactly that reading. A sentinel value stands for it, prefixed with a space so no real
// column name can collide.
const TAG_GROUPING_VALUE = " tag";

const groupingSelection = computed<string[]>(() => {
  const rule = app.model.data.grouping;
  if (rule === undefined) return [];
  if (rule.by === "tag") return [TAG_GROUPING_VALUE];
  return groupingColumns(rule);
});

const groupingOptions = computed(() => [
  {
    value: TAG_GROUPING_VALUE,
    label: `${app.model.data.barcodeSeqColumn || "Barcode"} — one identity per barcode`,
  },
  ...panelPropertyOptions.value,
]);

function setGrouping(selected: string[] | undefined) {
  const picked = (selected ?? []).filter((c) => c !== "");
  // The barcode column is the finest grouping there is, so it does not combine with a coarser one: a
  // combination including it is already one identity per barcode. Picking it therefore wins alone, and
  // picking nothing leaves the rule absent, which reads the same way.
  const rule: GroupingRule | undefined = picked.includes(TAG_GROUPING_VALUE)
    ? { by: "tag" }
    : picked.length > 0
      ? { by: "property", columns: picked }
      : undefined;
  app.model.data.grouping = rule;
  // The identities ARE the values of the grouping columns, so groups declared under the previous rule name
  // things that no longer exist. Cleared on the gesture that invalidates them rather than left to fail.
  app.model.data.contendingGroups = undefined;
  snapshotPanelColumns();
}

// Visible reason when the Combine-mode column is invalid, so a disabled Run button is explained rather than
// mysterious. The model's args() is the authoritative gate, throwing and greying out Run, and this mirrors
// the same condition into an inline alert. The selector is not offered today, but a project saved while it
// was, or migrated, can still carry a value that collides with the barcode or feature roles, and without
// this the Run button would simply be grey.
const combineColumnError = computed(() => {
  const c = app.model.data.combineColumn;
  if (!c) return undefined;
  if (c === app.model.data.barcodeSeqColumn || c === app.model.data.featureNameColumn)
    return (
      `The Combine-mode column must be a column of its own — it holds each feature's mode ` +
      `("sum" or "all"), not barcodes or feature names. It's currently set to "${c}", the same ` +
      `column used for the ${c === app.model.data.barcodeSeqColumn ? "barcode sequence" : "feature name"}. ` +
      `Pick a different column, or clear it to sum all co-barcodes.`
    );
  return undefined;
});

// Run mode: read-limited Preview (dry run) against a full run. The same PlBtnGroup pattern
// mixcr-clonotyping and demultiplex-fastq use, Preview first. Feature-barcode is single-cell and shallow per
// cell, so the dry-run default matches mixcr's single-cell recommendation of 500k reads per sample.
const runModeOptions = [
  { label: "Preview", value: "dry" as const },
  { label: "Full run", value: "full" as const },
];
const DRY_RUN_READS_DEFAULT = 500_000;
// Auto-fill the read limit where the user switches to Preview and has set none. Mirrors mixcr.
watch(
  () => app.model.data.runMode,
  (mode) => {
    if (mode === "dry" && app.model.data.limitInput == null)
      app.model.data.limitInput = DRY_RUN_READS_DEFAULT;
  },
);

// Empty stores as undefined so the no-control note below reads one condition rather than two.

// A GESTURE IS NOT A CHANGE. Every clear below must compare the new value against the old one: a control
// re-emitting the value it already held -- a user re-picking the dataset they had picked, or a re-render
// after the block pack was updated -- otherwise discards configuration nobody touched.
//
// Concretely: re-emitting an UNCHANGED FASTQ ref wipes `sampleColumn`, the run reaches per_cell_metrics.py
// with no `--sample-col`, and its duplicate-barcode guard refuses a sample-keyed panel. The user meets that
// as a QuickJS stack trace minutes after a gesture that changed nothing. `clearOnCsvChange` is the same shape
// over nine more fields, the whole binding reading included.
//
// The previous value has to be remembered HERE: `v-model` writes the new one into data before the handler
// runs, so data holds the "after" on both sides of any comparison made inside it. Keyed by JSON, so a ref
// object and a file handle compare the same way.
const keyOf = (v: unknown) => (v === undefined || v === null ? "" : JSON.stringify(v));
const seenFastqRef = ref(keyOf(app.model.data.fbFastqRef));
const seenCsvHandle = ref(keyOf(app.model.data.tagFeatureCsvHandle));
const seenFeatureColumn = ref(keyOf(app.model.data.featureNameColumn));

// Returns true only when the gesture carried a genuinely new value, and records it.
function changed(seen: { value: string }, next: unknown): boolean {
  const key = keyOf(next);
  if (key === seen.value) return false;
  seen.value = key;
  return true;
}

// Sample-aware mapping (optional). Picking the sample column snapshots the CURRENT dataset's sampleId->name
// map into data, so the args projection stays pure (model.md) and the per-sample workflow body can translate
// its iteration key.
function setSampleColumn(col: string | undefined) {
  app.model.data.sampleColumn = col || undefined;
  // Snapshot both the dataset's sampleId->name map AND the chosen column's CSV values, so args() can both
  // filter per sample and gate Run -- blocking when a dataset sample has no CSV rows -- purely from data.
  app.model.data.sampleLabelSnapshot = col ? app.model.outputs.sampleLabels : undefined;
  app.model.data.sampleColumnValues = col
    ? (app.model.outputs.csvValuesByColumn?.[col] ?? [])
    : undefined;
  // Clearing the sample column is what makes a duplicate barcode illegal again, so the numbers args() gates
  // on are refreshed here rather than assumed present from an earlier gesture.
  snapshotPanelCounts();
  clearVerdictSettingsNaming(col || undefined);
}

// The snapshot goes stale where the dataset changes, giving a different sampleId->name, or the CSV changes,
// giving different columns and values. Clear the sample-aware selection on those gestures and let the user
// re-pick. Split from its gesture handler because `clearOnCsvChange` calls it too, and THAT path must clear
// unconditionally: a new panel file invalidates the sample selection whatever the FASTQ ref does.
function clearSampleAwareState() {
  app.model.data.sampleColumn = undefined;
  app.model.data.sampleLabelSnapshot = undefined;
  app.model.data.sampleColumnValues = undefined;
}

function onFastqRefChanged(next: unknown) {
  if (!changed(seenFastqRef, next)) return;
  clearSampleAwareState();
}

// Picking the barcode column is what makes a duplicate mapping knowable, so it is where the two numbers
// args() needs are snapshotted. args() is data-only and the CSV meta lives on ctx.prerun, so without this the
// model can see the problem and still not refuse the run.
const seenBarcodeColumn = ref(keyOf(app.model.data.barcodeSeqColumn));

// Called from every gesture that can make a duplicate mapping RELEVANT, not only from the one that makes it
// knowable. Taken on the barcode-column pick alone, the gate is inert in the case that actually happens: the
// barcode column was picked long ago, and what changes now is the SAMPLE column being cleared, which turns a
// legal sample-keyed panel into an illegal duplicate one. Idempotent, so calling it from three places costs
// nothing.
function snapshotPanelCounts() {
  const col = app.model.data.barcodeSeqColumn;
  app.model.data.panelRowCount = col ? app.model.outputs.csvRowCount : undefined;
  app.model.data.panelBarcodeDistinct = col
    ? (app.model.outputs.csvValuesByColumn?.[col]?.length ?? undefined)
    : undefined;
}

// Claiming a column as a key invalidates any verdict setting that names it. The panel reader strips the
// barcode and sample columns before the properties are read, so the setting would name a column that is no
// longer a property. args() refuses the run in that state, which is a blocked Run button rather than a dead
// run, but the user still has to find the stale pick in a dropdown that stopped offering it. Clearing it on
// the gesture that invalidates it is the treatment clearOnCsvChange gives the panel swap. This is the
// reassignment case, reaching the same stale pick by a different gesture.
function clearVerdictSettingsNaming(column: string | undefined) {
  if (!column) return;
  if (app.model.data.roleColumn === column) {
    // The values designate values of THIS column, so they go with it -- the same pairing setRoleColumn keeps.
    app.model.data.roleColumn = undefined;
    app.model.data.referenceValues = undefined;
  }
  const remaining = groupingColumns(app.model.data.grouping).filter((c) => c !== column);
  if (remaining.length !== groupingColumns(app.model.data.grouping).length) {
    // A grouping may name several columns, so losing one leaves the others standing. Losing the last leaves no
    // rule, which reads as one identity per tag: the same state as never having set it.
    app.model.data.grouping =
      remaining.length > 0 ? { by: "property", columns: remaining } : undefined;
    // The identities ARE the values of the grouping columns, so declared groups now name things that do not
    // exist. Cleared here for the same reason setGrouping clears them.
    app.model.data.contendingGroups = undefined;
  }
}

function onBarcodeColumnChanged(next: unknown) {
  if (!changed(seenBarcodeColumn, next)) return;
  snapshotPanelCounts();
  clearVerdictSettingsNaming(app.model.data.barcodeSeqColumn);
}

// CSV swap invalidates every CSV-derived selection: the barcode / feature-name columns, since the new file's
// headers differ, the negative control, the sample-aware selection, and every setting of the binding reading
// that names a panel column or a panel value. The last group matters most: emit_verdicts.py ends the whole
// run when the role column or the grouping column is not one the panel carries, so a stale pick left behind
// here costs a run and reports it where the user never looks.
function onCsvChanged(next: unknown) {
  if (!changed(seenCsvHandle, next)) return;
  // The feature-name column is about to be cleared, so its own guard must not later read a stale key and
  // decide the user's re-pick was a no-op.
  seenFeatureColumn.value = "";
  clearOnCsvChange();
  // Read the panel NOW, from the file the user just chose, rather than waiting for the upload to land and a
  // workflow step to describe it. `next` is the parse target, not data: v-model has already written it, but
  // reading the argument makes the handler independent of listener order.
  void readPanelFrom(next as ImportFileHandle | undefined);
}

// Fills csvMetaSnapshot from the picked file. Local picks are read from disk, which is what makes the column
// dropdowns fill on the gesture. A remote pick reads nothing here and is served by the blob watcher below
// once the upload lands.
//
// The handle re-check before the write is the rapid-re-pick guard: the read is async, so a user who swaps
// files twice in quick succession can have the FIRST read resolve last. Publishing it would leave the
// dropdowns describing a file that is no longer chosen.
async function readPanelFrom(handle: ImportFileHandle | undefined) {
  if (!handle) return;
  try {
    const meta = await readLocalCsvMeta(handle);
    if (meta === undefined) return; // remote pick — the blob path serves it
    if (app.model.data.tagFeatureCsvHandle !== handle) return;
    app.model.data.csvMetaSnapshot = { handle, meta };
  } catch (e) {
    if (app.model.data.tagFeatureCsvHandle !== handle) return;
    app.model.data.csvImportError = e instanceof Error ? e.message : String(e);
  }
}

// The remote-pick path. Nothing on this machine can open an `index://` file, so the panel is read from the
// blob the prerun imported, through the SAME parser the local path uses. One parser, two byte sources.
//
// This is a watcher that writes to data, which hairpin.md tells reviewers to look at twice, and it is the
// same construction blocks/immune-assay-data uses for the same job. It cannot feed itself. The output it
// watches comes from the prerun, and the prerun re-renders only when the prerunArgs PROJECTION changes
// (canonical-JSON compared in pl-middle-layer's setStates, which gates renderStagingFor). That projection is
// tagFeatureCsvHandle alone, so writing csvMetaSnapshot cannot re-run the prerun and cannot change
// csvFileHandle. Adding the snapshot to prerunArgs WOULD close that loop, and because a staging re-render
// resets staging, each turn would throw away the uploaded blob. Leave the projection alone.
//
// Two clients open on one project both run this and both write, which is safe because they cannot disagree:
// the parse is pure and both read the same blob, so the writes are identical. The guard below stops the
// second one anyway.
const remoteCsvBytes = useRemoteCsvBytes(() => app.model.outputs.csvFileHandle);
watch(
  remoteCsvBytes,
  (bytes) => {
    const handle = app.model.data.tagFeatureCsvHandle;
    if (!bytes || !handle) return;
    if (app.model.data.csvMetaSnapshot?.handle === handle) return; // already read
    try {
      app.model.data.csvMetaSnapshot = { handle, meta: parseTagCsvMeta(bytes) };
      app.model.data.csvImportError = undefined;
    } catch (e) {
      app.model.data.csvImportError = e instanceof Error ? e.message : String(e);
    }
  },
  { immediate: true },
);

function clearOnCsvChange() {
  // The panel metadata describes the OLD file, so it goes first: every field cleared below is derived from
  // it, and readCsvMeta stops returning it the moment the handle it is tagged with stops matching.
  app.model.data.csvMetaSnapshot = undefined;
  app.model.data.csvImportError = undefined;
  app.model.data.barcodeSeqColumn = undefined;
  app.model.data.panelRowCount = undefined;
  app.model.data.panelBarcodeDistinct = undefined;
  seenBarcodeColumn.value = "";
  app.model.data.featureNameColumn = undefined;
  app.model.data.combineColumn = undefined;
  app.model.data.roleColumn = undefined;
  app.model.data.referenceValues = undefined;
  app.model.data.grouping = undefined;
  app.model.data.contendingGroups = undefined;
  app.model.data.panelColumnSnapshot = undefined;
  clearSampleAwareState();
}

// Sample-aware mapping sanity warning from the model: dataset samples missing from the CSV, and CSV sample
// values matching no dataset sample. Only present once a sample column is chosen.
const sampleMappingWarning = computed(() => app.model.outputs.sampleMappingWarning);

// Sample-aware mapping is auto-selected. Where the model spots a CSV column whose distinct values match the
// dataset's sample names (suggestedSampleColumn), pre-populate the Sample column dropdown with it through
// setSampleColumn, which snapshots the sample map into data. Guarded to run only while NO column is set, so a
// manual clear or a manual pick is never overridden. Safe from the reactive-write hairpin the block otherwise
// avoids: suggestedSampleColumn derives from the CSV meta and sample labels alone, and depends on neither
// sampleColumn nor the snapshot fields setSampleColumn writes, so applying it cannot re-trigger the
// suggestion. Clearing with X sticks. A CSV or dataset change re-clears through clearOnCsvChange, then
// re-suggests.
const suggestedSampleColumn = computed(() => app.model.outputs.suggestedSampleColumn);
watch(
  suggestedSampleColumn,
  (col) => {
    if (col && !app.model.data.sampleColumn) setSampleColumn(col);
  },
  { immediate: true },
);

// --- Running-state progress grid (in-memory AgGridVue, same pattern as blocks/peptide-extraction) ---
ModuleRegistry.registerModules([ClientSideRowModelModule]);

const onGridReady = (params: GridReadyEvent) => {
  autoSizeRowNumberColumn(params.api);
};

const defaultColumnDef: ColDef = {
  suppressHeaderMenuButton: true,
  lockPinned: true,
  sortable: false,
};

// The progress grid is always shown. Before the run starts, show the "not-ready" overlay. Once it begins,
// show "running" until the sample roster loads.
const loadingOverlayParams = computed(() =>
  app.model.outputs.started
    ? { variant: "running" as const, runningText: "Preparing sample list" }
    : { variant: "not-ready" as const },
);

const columnDefs: ColDef<SampleResult>[] = [
  makeRowNumberColDef(),
  createAgGridColDef<SampleResult, string>({
    colId: "label",
    field: "label",
    headerName: "Sample",
    headerComponentParams: { type: "Text" } satisfies PlAgHeaderComponentParams,
    pinned: "left",
    lockPinned: true,
    sortable: true,
    flex: 1,
  }),
  createAgGridColDef<SampleResult, ProgressCell>({
    colId: "progress",
    field: "progress",
    headerName: "Progress",
    headerComponentParams: {
      type: "Progress",
      info:
        "Double-click a sample to open its report: read recovery, the individual quality checks, " +
        "and the per-step logs (parse, refine tags, count UMIs).",
    } satisfies PlAgHeaderComponentParams,
    flex: 2,
    // results.ts already produces the cell config (status / percent / text / suffix). Pass it through.
    progress: (value) => value,
  }),
  // Quality status tag (OK / WARN / ALERT), worst-case per sample from the QC metrics (results.ts). Blank
  // while the sample is still running, since quality is undefined until its QC settles.
  createAgGridColDef<SampleResult, QcStatus | undefined>({
    colId: "quality",
    field: "quality",
    headerName: "Quality",
    headerComponentParams: {
      type: "Text",
      info:
        "Per-sample QC status.\n" +
        "ALERT — no cells detected, or under 25% of reads assigned to the panel.\n" +
        "WARN — under 50% of reads panel-assigned, or under 80% matched the read pattern.\n" +
        "OK — otherwise.",
    } satisfies PlAgHeaderComponentParams,
    width: 120,
    cellRendererSelector: (params) =>
      params.data?.quality
        ? { component: PlAgCellStatusTag, params: { type: params.data.quality } }
        : undefined,
  }),
  // Read recovery: a compact stacked bar of usable, off-panel and no-pattern-match. Blank until QC settles.
  createAgGridColDef<SampleResult, RecoveryBar | undefined>({
    colId: "recovery",
    field: "recovery",
    headerName: "Read recovery",
    headerComponentParams: {
      type: "Text",
      info:
        "Where each sample's reads went, by count. Bar segments left → right:\n" +
        "Usable (green) — matched the read pattern and kept after feature-barcode panel correction.\n" +
        "Off-panel (orange-red) — matched the pattern but the feature barcode was dropped as off-panel.\n" +
        "No pattern match (purple) — did not match the read pattern.",
    } satisfies PlAgHeaderComponentParams,
    flex: 2,
    cellStyle: { "--ag-cell-horizontal-padding": "12px" },
    cellRendererSelector: (params) =>
      params.data?.recovery
        ? { component: PlAgChartStackedBarCell, params: { value: params.data.recovery } }
        : undefined,
  }),
];

const gridOptions = {
  getRowId: (row: { data: SampleResult }) => row.data.sampleId,
  // Double-click a sample row -> open its per-step logs panel.
  onRowDoubleClicked: (e: { data?: SampleResult }) => {
    if (e.data?.sampleId !== undefined) selectedSample.value = e.data.sampleId;
  },
};
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Barcode Profiling</template>
    <template #append>
      <PlBtnGhost v-if="analysisLog.length > 0" @click.stop="logsOpen = true">
        Logs
        <template #append>
          <PlMaskIcon24 name="file-logs" />
        </template>
      </PlBtnGhost>
      <PlBtnGhost @click.stop="settingsOpen = true">
        Settings
        <template #append>
          <PlMaskIcon24 name="settings" />
        </template>
      </PlBtnGhost>
    </template>

    <!-- Main shows ONLY per-sample progress, like MiXCR Clonotyping. The per-cell results table lives on its
         own "Per-cell results" tab (pages/ResultsPage.vue). The in-memory progress grid, the same pattern as
         blocks/peptide-extraction, is always shown: the grid handles layout, its Progress cell, and the
         loading overlay for the pre-roster window. When the run finishes every row settles into its "Done"
         state, which results.ts sets from completedSamples. -->
    <div :style="{ flex: 1 }">
      <AgGridVue
        :theme="AgGridTheme"
        :style="{ height: '100%' }"
        :row-data="sampleResults"
        :default-col-def="defaultColumnDef"
        :column-defs="columnDefs"
        :grid-options="gridOptions"
        :loading-overlay-component-params="loadingOverlayParams"
        :loading-overlay-component="PlAgOverlayLoading"
        :no-rows-overlay-component="PlAgOverlayNoRows"
        @grid-ready="onGridReady"
      />
    </div>
    <PlSlideModal v-model="settingsOpen" width="40%">
      <template #title>Settings</template>
      <PlDropdownRef
        v-model="app.model.data.fbFastqRef"
        :options="app.model.outputs.fastqOptions"
        label="Tag-barcode FASTQ dataset"
        @update:model-value="onFastqRefChanged"
      >
        <template #tooltip>
          The tag-barcode FASTQ dataset to analyse. Its reads give the unique count for each tag
          barcode in each cell.<br /><br />
          Counts are not verdicts. Whether a cell bound an antigen is decided later, against the
          baseline, and only when you also give a V(D)J dataset.
        </template>
      </PlDropdownRef>
      <!-- Read layout: preset dropdown plus pattern builder/string (mitool tag pattern). Replaces the former
           cell/UMI/feature length fields, whose values are now decided inside the editor. -->
      <PatternEditor />
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Panel file"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
        @update:model-value="onCsvChanged"
      >
        <template #tooltip>
          The panel file declaring which tag barcode is which antigen. One row per barcode — or one
          row per barcode and sample, when you set a sample column below.
          <br /><br />
          This file is also the authority on what each sample was offered. An antigen it does not
          declare for a sample is never asked about there.
        </template>
      </PlFileInput>
      <PlAlert v-if="csvProcessing" type="info"> Reading columns from the uploaded CSV… </PlAlert>
      <PlAlert v-if="app.model.data.csvImportError" type="warn">
        Could not read the tag-feature CSV: {{ app.model.data.csvImportError }}
      </PlAlert>
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Panel column holding each tag's barcode sequence"
        @update:model-value="onBarcodeColumnChanged"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          The panel column holding each tag barcode's nucleotide sequence. The block matches these
          sequences against the barcode that the <code>FEATURE</code> tag captures on Read 2 — the
          second read of each pair.
        </template>
      </PlDropdown>
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Panel column naming each antigen"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          The panel column holding the antigen name each tag barcode maps to. These names label the
          antigens everywhere the block reports them.<br /><br />
          Where two samples give one barcode different names, the antigen carries both names,
          joined.
        </template>
      </PlDropdown>
      <!-- The dataset the verdicts are about. It sits with the inputs above rather than with the verdict
           settings below, because it IS an input: choosing it re-runs the verdict stage, where everything in
           `VerdictSettings` is a cheap re-read. It is the last input a user picks before choosing how much of
           the data to run, which is why it sits directly above Run mode. -->
      <PlDropdownRef
        v-model="app.model.data.datasetRef"
        :options="app.model.outputs.datasetOptions"
        label="Single-cell V(D)J dataset"
        required
      >
        <template #tooltip>
          The clonotypes each verdict is about. Every verdict is about one clonotype, so the block
          produces none without it.
        </template>
      </PlDropdownRef>
      <!-- Identity is what a verdict is about, so the rule that mints identities belongs with the dataset
           that supplies the clonotypes rather than among the reading thresholds below. -->
      <PlDropdownMulti
        :model-value="groupingSelection"
        :options="groupingOptions"
        label="Panel columns that define an identity"
        :disabled="panelUnread"
        @update:model-value="setGrouping"
      >
        <template #tooltip>
          A verdict is about an identity, not a barcode. Name one or more panel columns. Every tag
          that shares a value in all of them becomes one identity. That is how an antigen on two
          barcodes gives one column rather than two.<br /><br />
          Name several columns and the identity becomes the combination. Antigen and concentration
          together read the same antigen at two concentrations as two identities.<br /><br />
          The barcode column is the finest grouping: one identity per barcode. Select it and the
          block ignores the other columns, because any combination that includes the barcode gives
          the same identities.<br /><br />
          An identity's reading in a cell is the highest of its tags, never their sum. Tags differ
          in uptake, so a sum would need the baseline scaled to match.
        </template>
      </PlDropdownMulti>
      <PlBtnGroup v-model="app.model.data.runMode" :options="runModeOptions" label="Run mode">
        <template #tooltip>
          Preview reads only the first reads of each sample, up to the limit below. Use it to check
          your settings before a full run, which takes much longer.<br /><br />
          Preview still produces counts and verdicts. They rest on fewer cells than a full run
          gives, so more antigens read unreliable. Do not compare a Preview card with a full run.
        </template>
      </PlBtnGroup>
      <template v-if="app.model.data.runMode === 'dry'">
        <PlNumberField
          v-model="app.model.data.limitInput"
          label="Reads per sample limit"
          :clearable="true"
          :min-value="1"
          :error-message="
            app.model.data.limitInput == null
              ? 'Read limit is required for Preview mode'
              : undefined
          "
        >
          <template #tooltip>
            How many reads to use from each sample in Preview. The block takes the first reads of
            the file, not a random sample.<br /><br />
            Tag-barcode libraries are shallow per cell. 500,000 reads is enough to check that
            settings work.
          </template>
        </PlNumberField>
      </template>
      <!-- The baseline comes before the optional settings because it decides which of them apply: each
           baseline brings its own rule, and a setting belonging to one rule is shown only where that rule
           runs. A reader meeting Bound cutoff before choosing a baseline meets a field that appears and
           disappears on a choice they have not made yet. -->
      <PlSectionSeparator compact> Baseline (background) level </PlSectionSeparator>
      <PlDropdown
        :model-value="chosenSource"
        :options="allSources"
        label="What sets the baseline"
        :disabled="panelUnread"
        required
        @update:model-value="setBaselineSource"
      >
        <template #tooltip>
          You choose this. The block does not choose it for you: two runs answered against different
          baselines produce numbers that do not compare, and a baseline nobody chose is a method
          nobody knows they used.<br /><br />
          <b>Declared baseline tag</b> — the tag your panel marks as the one nothing should bind.<br />
          <b>The panel's own readings</b> — the median of each cell's own counts.<br />
          <b>Each tag's own distribution</b> — that tag's counts across the sample's cells, split in
          two.<br /><br />
          Pick one and this form asks for what that rung needs, and for nothing else.<br /><br />
          The last two are local to this run: what a count was read against was a population of this
          run, so those magnitudes do not travel between runs.
        </template>
      </PlDropdown>
      <!-- What the chosen rung still needs. Not an error: the rung is a legitimate choice and the fields that
           satisfy it are directly below, so this names the gap rather than refusing it. -->
      <PlAlert v-if="chosenNeeds" type="warn">{{ chosenNeeds }}</PlAlert>
      <PlDropdown
        v-if="chosenSource === 'declared'"
        :model-value="app.model.data.roleColumn"
        :options="panelPropertyOptions"
        label="Panel column declaring each tag's role"
        :disabled="panelUnread"
        clearable
        @update:model-value="setRoleColumn"
      >
        <template #tooltip>
          Name the panel column that declares each tag's role. One value of that column marks a tag
          as the baseline. The block then judges every other count in the same cell against that
          tag.<br /><br />
          Leave it blank if your panel declares no role.
        </template>
      </PlDropdown>
      <!-- Required exactly while a role column is named, and not otherwise. The column alone marks no tag: it is
           validated, recorded, and changes no number, so the pair is the setting and half of it is an unfinished
           form. Blank column plus blank values stays legitimate -- that is the panel which declares no baseline,
           which `292-no-declared-reference` serves. -->
      <PlDropdown
        v-if="chosenSource === 'declared'"
        :model-value="app.model.data.referenceValues?.[0]"
        :options="roleValueOptions"
        label="Value that marks the baseline tag"
        :disabled="panelUnread || !app.model.data.roleColumn"
        :required="!!app.model.data.roleColumn"
        clearable
        @update:model-value="setReferenceValue($event)"
      >
        <template #tooltip>
          Select which value of the role column marks the baseline tag. A tag is the baseline in
          every sample, or in none. You cannot give some samples a different baseline.<br /><br />
          Required once you name a role column. That column says where each tag's role is written;
          this value is what actually marks one. Named alone, the column changes nothing.<br /><br />
          The block reads counts against <b>one</b> baseline tag. A panel may carry several control
          tags, but only one is nominated to supply the baseline. If the value you pick marks more
          than one tag, the run stops and names the tags it found.
        </template>
      </PlDropdown>

      <!-- Directly under the baseline section rather than with the other accordion at the foot of the form.
           Grouping the accordions together sorted this form by how advanced a control is, which split the
           baseline's own thresholds away from the baseline. Collapsed, this costs the reader one line and puts
           every baseline control in one place. The heading does NOT repeat "(background)": that parenthetical
           glosses the word once, where the reader first meets it, and repeating it would read as part of the name
           and re-open the several-names-for-one-thing problem this form already closed. The shared word
           "Baseline" is the link. -->
      <PlAccordionSection label="Baseline thresholds">
        <PlNumberField
          v-model="app.model.data.distributionMinCells"
          v-if="chosenSource === 'distribution'"
          :min-value="1"
          :step="10"
          label="Cells needed to fit a tag's own distribution"
        >
          <template #tooltip>
            How many cells a sample needs before a tag's own distribution across those cells can
            serve as the baseline. Below this, the block cannot fit the two components and every
            reading in that sample is unreliable.<br /><br />
            The default of 300 comes from the study this method comes from. Lowering it is a
            departure from that method rather than a preference: below it the baseline is not
            conservative, it is wrong.
          </template>
        </PlNumberField>
        <PlNumberField
          v-model="app.model.data.distributionSeparation"
          v-if="chosenSource === 'distribution'"
          :min-value="0.01"
          :max-value="1"
          :step="0.05"
          label="Maximum dip height (0–1)"
        >
          <template #tooltip>
            The block sorts a tag's counts across the sample's cells into two groups. One group has
            few counts and is the background. The other has many counts and is the binders. A dip
            separates the two groups.<br /><br />
            The dip must fall to this share of the smaller group, or lower.
            <b>A lower value is stricter</b>, because it needs a deeper dip.<br /><br />
            A tag whose dip is too shallow gets no baseline. Only the antigens that tag carries read
            unreliable. The rest of the run is unaffected.<br /><br />
            At 1 any dent passes. A tag that nothing bound then stands in as its own background.<br /><br />
            No published work sets this line. The default of 0.5 is this block's own choice.
          </template>
        </PlNumberField>
        <PlNumberField
          v-model="app.model.data.highReferenceLine"
          v-if="chosenSource !== 'distribution'"
          :min-value="1"
          :step="1"
          label="High baseline reading"
        >
          <template #tooltip>
            The baseline count, in UMIs, at which a cell is in high background. This is a
            measurement, not a filter. The block counts these cells whether or not the gate below is
            on. You can therefore see the run's exposure even when no gate is set.
          </template>
        </PlNumberField>
        <PlNumberField
          v-model="app.model.data.gateThreshold"
          v-if="chosenSource !== 'distribution'"
          :min-value="1"
          :step="1"
          clearable
          label="Admissibility gate (baseline UMIs)"
        >
          <template #tooltip>
            The gate is off when this field is empty. When you set it, the block sets aside a cell
            whose baseline reading reaches this value. That cell reads unreliable at every identity
            and gives no verdict anywhere.<br /><br />
            Off is a deliberate default, and a contested one. Published practice uses a gate. The
            dominant tool does not. Off matches the tool, so first-run numbers stay recognisable.
            The cost is that a sticky cell remains in the set and returns a confident "not bound".
          </template>
        </PlNumberField>
      </PlAccordionSection>

      <PlSectionSeparator compact> Optional settings </PlSectionSeparator>
      <PlNumberField
        v-model="app.model.data.countFloor"
        :min-value="0"
        :step="1"
        label="Minimum count"
      >
        <template #tooltip>
          Counts below this are not evidence of binding. The block reads them as zero rather than as
          a small signal.<br /><br />
          The minimum does not apply to the baseline tag. A minimum on the baseline would lower the
          level every count is judged against, and push the whole run toward bound.<br /><br />
          The default is 4. It is a declared default, not a calibrated line.
        </template>
      </PlNumberField>
      <PlNumberField
        v-if="chosenSource === 'declared'"
        v-model="app.model.data.boundCutoff"
        :min-value="0"
        :max-value="100"
        :step="1"
        label="Bound cutoff (0–100)"
      >
        <template #tooltip>
          The score at or above which one cell reads that identity as bound. This is a per-cell
          reading. The clonotype's verdict is the majority of its cells.<br /><br />
          <b>The score is not a measure of binding strength</b> and cannot stand in for affinity. It
          is the probability that the identity's reading makes up more than 92.5% of that reading
          and the cell's baseline reading together, so the same ratio scores higher with more counts
          behind it.<br /><br />
          This belongs to the declared-baseline rule alone. Both the 92.5% and the default of 75
          come from the dominant tool, and neither this block nor anything published justifies them.
        </template>
      </PlNumberField>
      <PlNumberField
        v-model="app.model.data.minVotingCells"
        :min-value="1"
        :step="1"
        label="Minimum voting cells"
      >
        <template #tooltip>
          How many cells must answer before their majority settles a verdict. Below this number the
          verdict reads unreliable, and gives too few voters as the reason.<br /><br />
          At 1 a verdict may rest on a single cell. The table carries the answering-cell count, so
          you can see when it does.
        </template>
      </PlNumberField>
      <!-- One field per line. Side by side in a PlRow each got half the drawer's width, which truncated both
           labels -- "Control feature marker (outp..." told the user nothing about what it does not change,
           and that label carries the whole distinction from the baseline setting below. The sample column
           comes first: it changes what the run computes, while the control marker only labels a feature in
           the output. -->
      <PlDropdown
        :model-value="app.model.data.sampleColumn"
        :options="roleFreeColumnOptions"
        label="Panel column naming each row's sample"
        :disabled="tagMappingDisabled"
        clearable
        @update:model-value="setSampleColumn"
      >
        <template #tooltip>
          <b>Set this when your panel file has more than one row per tag barcode</b> — normally
          because it lists each barcode once per sample. Leave it blank when the panel file has
          exactly one row per tag barcode.<br /><br />
          Names the panel column holding each row's sample. Every sample in your dataset must appear
          in it. Extra values are allowed.<br /><br />
          The block then reads a separate panel for each sample. Each sample is asked only about the
          identities its own rows declare. One barcode can also name a different antigen in a
          different sample.<br /><br />
          The block selects this when it detects a matching column.
        </template>
      </PlDropdown>
      <!-- The combine-mode column selector is not offered (MILAB-6496): with it unset, every antigen uses the
           default "sum" mode. The parameter itself is live -- combineColumn and minUmi still reach
           per_cell_metrics.py -- so a value carried in from an older project is still honoured, and the alert
           below explains a Run button greyed out by one that collides with a role. -->
      <PlAlert v-if="combineColumnError" type="warn">
        {{ combineColumnError }}
      </PlAlert>
      <PlAlert v-if="sampleMappingWarning?.length" type="warn">
        <div v-for="(line, i) in sampleMappingWarning" :key="i">{{ line }}</div>
      </PlAlert>
      <!-- Beside its siblings rather than under the Sample column control: all three are about the same CSV,
           and a reader scanning for what is wrong should find them in one place.

           barcodeMappingIssue was computed by the model and rendered NOWHERE. The block knew, at config
           time, that a barcode sat on several rows and knew which column fixed it -- and said so to no one.
           What a user got instead was a QuickJS stack trace at the end of a run, from per_cell_metrics.py's
           own guard, minutes after the gesture that caused it. -->
      <!-- Same lesson as the note above, one rung earlier: this fires when the chosen column holds no
           sequences at all, so the panel the run is built on is not barcodes. First of the two, and the model
           suppresses the duplicate-barcode warning while it is showing -- that warning would send a reader
           chasing the sample column when the actual mistake is the barcode column itself. -->
      <PlAlert v-if="app.model.outputs.barcodeAlphabetIssue" type="warn">
        {{ app.model.outputs.barcodeAlphabetIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.barcodeMappingIssue" type="warn">
        {{ app.model.outputs.barcodeMappingIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.unkeyedSamplePanel" type="warn">
        {{ app.model.outputs.unkeyedSamplePanel }}
      </PlAlert>
      <PlAccordionSection label="Advanced reading settings">
        <PlCheckbox v-if="chosenSource === 'declared'" v-model="minimumAppliesToBaseline">
          Apply the minimum count to the baseline tag
          <template #tooltip>
            By default the minimum count is not applied to the tag your panel marks as the baseline.
            The minimum removes what is not evidence of binding, and the baseline is not evidence of
            binding — it is what binding is measured against.<br /><br />
            Turning this on changes no verdict. Each baseline source reads its own counts before the
            minimum, so the level a count is judged against is the same either way. What changes is
            the run's own accounting: how many readings it reports as removed, how many cells it
            reports as emptied, and which of a clonotype's cells count as empty.
          </template>
        </PlCheckbox>
        <PlNumberField
          v-model="app.model.data.minAgreement"
          :min-value="0"
          :max-value="1"
          :step="0.05"
          clearable
          label="Minimum cell agreement (0–1)"
        >
          <template #tooltip>
            This setting is off when the field is empty. A narrow majority then stands, and reports
            how narrow. Set it to leave a verdict unsettled where the answering cells agree less
            than this share of the time.
          </template>
        </PlNumberField>
      </PlAccordionSection>
      <!-- Less-common params. -->
      <PlAccordionSection label="Compute resources">
        <!-- Hidden with the control it belongs to. This is the AND-combine per-barcode floor, and the
             Combine-mode column selector is not offered, so every antigen uses "sum" and this value can
             never take effect. A setting a user can change that cannot do anything is worse than an
             absent one. Restore both together.
        <PlNumberField
          v-model="app.model.data.minUmi"
          :min-value="1"
          :step="1"
          clearable
          label="Min UMIs per barcode (AND combine)"
        >
          <template #tooltip>
            <b>For "all"-combine antigens only</b> — it applies when the run carries a combine-mode
            column, which is not offered today. Minimum distinct UMIs a barcode needs to count as
            "fired" (present) in a cell; below it, that barcode doesn't count toward the AND. Leave
            empty for the default (1).
          </template>
        </PlNumberField>
        -->
        <PlNumberField
          v-model="app.model.data.perProcessCPUs"
          :min-value="1"
          :step="1"
          clearable
          label="mitool CPUs per sample"
        >
          <template #tooltip>
            Leave empty unless a large sample runs slowly. CPUs given to each per-sample step:
            parse, refine tags, and tag statistics. Default 8.
          </template>
        </PlNumberField>
        <PlNumberField
          v-model="app.model.data.perProcessMemGB"
          :min-value="1"
          :step="1"
          clearable
          label="mitool memory per sample (GiB)"
        >
          <template #tooltip>
            Leave empty to size memory from the reads. The block then asks for 16 GiB plus four
            times the read volume, between 16 and 256 GiB.<br /><br />
            Set a number only if a sample runs out of memory. That number becomes a fixed request
            for every sample, so a value chosen for your largest sample is demanded for the smallest
            one too.
          </template>
        </PlNumberField>
      </PlAccordionSection>
    </PlSlideModal>

    <PlSlideModal v-model="logsOpen" width="80%">
      <template #title>Analysis logs</template>
      <PlLogView :value="logText" />
    </PlSlideModal>

    <PlSlideModal v-model="sampleReportOpen" width="60%">
      <template #title>{{ selectedSampleLabel }}</template>
      <SampleReportPanel v-model="selectedSample" />
    </PlSlideModal>
  </PlBlockPage>
</template>
