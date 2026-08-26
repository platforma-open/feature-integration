<script setup lang="ts">
import type { PlAgHeaderComponentParams } from "@platforma-sdk/ui-vue";
import {
  AgGridTheme,
  PlAccordionSection,
  PlAgCellStatusTag,
  PlAgChartStackedBarCell,
  PlAgOverlayLoading,
  PlAgOverlayNoRows,
  PlAgTextAndButtonCell,
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
import {
  AGGREGATE_DETECTION_DEFAULTS,
  groupingColumns,
  QC_LINE_DEFAULTS,
} from "@platforma-open/milaboratories.feature-integration.model";
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

// A quality line and an aggregate-detection knob are both stored undefined until someone types one, and
// the workflow then substitutes the shipped default. The fields below bind through `setting` so an
// untouched one shows that substituted number instead of an empty box. Clearing writes undefined back.
// The value shown is never written to `data` on its own, so opening Settings stales nothing.
const SETTING_DEFAULTS = { ...QC_LINE_DEFAULTS, ...AGGREGATE_DETECTION_DEFAULTS };
type SettingKey = keyof typeof SETTING_DEFAULTS;

function setting(key: SettingKey) {
  return computed<number | undefined>({
    get: () => app.model.data[key] ?? SETTING_DEFAULTS[key],
    set: (value) => {
      app.model.data[key] = value;
    },
  });
}

const cellBarcodeValidWarn = setting("cellBarcodeValidWarn");
const cellBarcodeValidError = setting("cellBarcodeValidError");
const readsPerCellWarn = setting("readsPerCellWarn");
const aggregateBarcodeWarn = setting("aggregateBarcodeWarn");
const aggregateBarcodeError = setting("aggregateBarcodeError");
const undeclaredBarcodeWarn = setting("undeclaredBarcodeWarn");
const undeclaredBarcodeError = setting("undeclaredBarcodeError");
const usableReadWarn = setting("usableReadWarn");
const usableReadError = setting("usableReadError");
// No binding for the three aggregate-detection knobs. They keep their entries in SETTING_DEFAULTS, because
// the same defaults answer a stored project and the workflow, and only their controls are gone.
// Auto-open Settings for a fresh block. Stays closed once configured.
const settingsOpen = ref(app.model.data.fbFastqRef === undefined);
// Close the Settings drawer once a run starts. Watching an output and writing a local ref is not a hairpin:
// nothing writes to server-stored data.
watch(
  () => app.model.outputs.isRunning,
  (running) => {
    if (running) settingsOpen.value = false;
  },
);

// The block's "Analysis logs": a live completed-sample heartbeat while the run is in progress, then a
// run-level summary. Detailed per-sample statistics live on the QC page.
const analysisLog = computed(() => app.model.outputs.analysisLog ?? []);

// No run-level progress bar above the grid. The grid's own Progress column carries every sample's step and
// percent, so a second reading of the same numbers competed with it for the reader's attention.
// First line of the Analysis-logs drawer, pointing the user at the richer per-sample logs.
const LOGS_HINT =
  "Tip: double-click any sample in the progress table to open its own detailed per-step logs (parse, refine tags, count UMIs).";
const logText = computed(() => [LOGS_HINT, "", ...analysisLog.value].join("\n"));
const logsOpen = ref(false);

// Per-sample report slide-over of live per-step mitool logs. Opened by double-clicking a grid row.
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

// True while the panel CSV has been picked but not yet read. For a local pick that window is a single tick;
// for a remote pick it lasts until the upload lands and the blob watcher parses it. Drives a "reading
// columns..." note, so the empty dropdowns read as "loading" rather than "no columns".
const csvProcessing = computed(() => app.model.outputs.csvColumnsLoading === true);

// The CSV-derived tag-mapping dropdowns have nothing to offer until a tag-feature CSV is picked AND its
// columns are read. Disable and dim them until then, so their empty state reads as "waiting for a CSV"
// rather than "no columns found".
const tagMappingDisabled = computed(
  () => !app.model.data.tagFeatureCsvHandle || csvProcessing.value,
);

// CSV columns not already bound to the barcode-sequence or feature-name roles. A column holding DNA
// barcodes or antigen names is not a sample column, and offering it invites a mis-pick. args() rejects the
// collision too, and the data layer refuses it only at the end of the run.
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
// Rendered in the Main page's Settings drawer, and there ONLY. Two drawers editing one set of controls is
// not the idiom, whatever it costs to recompute.
//
// Everything this component EDITS is below the "binding reading" line in BlockArgs, so a change here
// recovers every per-sample mitool body from cache and re-runs the verdict stage alone. It also READS three
// fields it must never edit -- tagFeatureCsvHandle, barcodeSeqColumn, sampleColumn -- which force the whole
// per-sample fan-out to re-run and whose controls the Main page owns.
//
// VOCABULARY, and the split is deliberate. Everything a USER reads says "baseline". The DATA layer keeps
// `reference` -- `ReferenceSource`, the run-meta keys, the p-column domain values -- and cannot follow,
// because domain is part of column identity and renaming one would change what every emitted column IS.
// Comments here describe the data layer, so they still say reference and comparator.
//
// The form never says "control". Being a control is a property of the tag, and a panel may carry several,
// where being the reference that supplies the baseline is a job given to exactly one of them.

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
// `referenceSource` is NOT cleared here, and must not be. The rung is chosen first and the form then asks
// for what that rung needs, so clearing the rung on either gesture would unpick it at the moment its fields
// were filled in. Only on a GESTURE, never from a watcher: a watcher on a model output writing back into
// data is the hairpin, and two clients with the project open would race on it.
function setRoleColumn(column: string | undefined) {
  app.model.data.roleColumn = column || undefined;
  app.model.data.referenceValues = undefined;
  snapshotPanelColumns();
}

// ONE value, stored as a one-element list. Being a control is a property of the tag and a panel may carry
// several, but the reference is one job given to one of them. Several values here only ever described a
// panel whose role column spells one role more than one way.
//
// `data.referenceValues` stays a LIST. The field name, the `--reference-values` flag and every stored
// project keep their shape, so this tightens the control without a migration. The `> 1 tag` refusal in
// `verdict.py` stays too: one value can still mark several tags, which is a panel fact this control cannot
// see.
function setReferenceValue(value: string | undefined) {
  app.model.data.referenceValues = value ? [value] : undefined;
}

// The comparator sources this panel can serve. Both the option list and the reasons come from a model output
// rather than from a watcher: copying panel facts into data would make the output depend on the data it
// feeds.
const referenceSources = computed(() => app.model.outputs.referenceSources);

// The rungs this panel can serve, and the rung the run will be answered under. Both come from model outputs:
// the option list from `referenceSources`, the shown value from `effectiveReferenceSource`.
//
// NEVER derive the shown value here. Writing the rule twice -- once to decide what to display, once in
// `args()` to decide what to send -- makes the field lie the moment a stored choice stops being
// serviceable: the form would show a scientist the exact value they are being asked to supply while Run
// stays greyed out. The block does not choose a baseline, because a baseline nobody chose is a methodology
// nobody knows they used.
//
// EVERY rung, always, and every one selectable. Offering only the rungs already satisfied made the declared
// rung unreachable: its requirements are the two fields that appear once it is chosen.
const allSources = computed(() => referenceSources.value?.options ?? []);

// The agreement limit as a PERCENTAGE, where the data holds a share from 0 to 1. Agreement is the share of
// voting cells holding the state the verdict took, so a majority can never fall below a half: the useful
// range is 51 to 100, and "0-1" hid that.
//
// The data keeps the share, so this is a display conversion and not a migration. Rounded on the way in,
// because a percentage entered as an integer must come back as the same integer.
const agreementPercent = computed({
  get: () => {
    const share = app.model.data.minAgreement;
    // NOT rounded. `step` drives only the arrow buttons, and `commitValue` in PlNumberField assigns
    // whatever was typed, so rounding here would show 51 for a stored 50.5 and the number on screen would
    // stop being the number in force.
    return typeof share === "number" ? share * 100 : undefined;
  },
  set: (percent: number | undefined) => {
    app.model.data.minAgreement = typeof percent === "number" ? percent / 100 : undefined;
  },
});

// The chosen rung, from DATA. Read from data and never from `effectiveReferenceSource`: the form reveals
// fields against what the scientist picked, and that must not move on its own.
const chosenSource = computed(() => app.model.data.referenceSource);

// What the chosen rung still needs, if anything. The model computes it, because whether a rung can serve
// turns on panel facts this component must not re-derive.
const chosenNeeds = computed(
  () => allSources.value.find((o) => o.value === chosenSource.value)?.needs,
);
function setBaselineSource(value: string | undefined) {
  app.model.data.referenceSource = value === undefined ? undefined : (value as ReferenceSource);
}

// The panel-derived dropdowns have nothing to offer until the panel file is uploaded and read. Disabled and
// dimmed, so their empty state reads as "waiting" rather than "nothing found".
const panelUnread = computed(
  () => !app.model.data.tagFeatureCsvHandle || app.model.outputs.csvColumnsLoading === true,
);

// The panel's PROPERTY columns: every header except the ones the panel reader consumes as keys, which are
// the barcode column and the sample column where one is set. Mirrors panel.py's own rule.
// emit_verdicts.py ends the run rather than degrading when handed a column the reader strips.
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
// finest one available -- rather than a mode beside grouping. It cannot be offered as a property column,
// since the panel reader consumes it as the `tag` key, so a sentinel maps it to the `tag` rule. The
// sentinel is prefixed with a space so no real column name can collide.
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
    // Labelled so it cannot be mistaken for one of the panel's own columns, which is what naming it
    // after the barcode column did.
    label: "Each barcode on its own — one identity per barcode",
  },
  ...panelPropertyOptions.value,
]);

function setGrouping(selected: string[] | undefined) {
  const picked = (selected ?? []).filter((c) => c !== "");
  // The barcode column is the finest grouping there is, so a combination including it is already one
  // identity per barcode. Picking it wins alone, and picking nothing leaves the rule absent, which reads
  // the same way.
  const rule: GroupingRule | undefined = picked.includes(TAG_GROUPING_VALUE)
    ? { by: "tag" }
    : picked.length > 0
      ? { by: "property", columns: picked }
      : undefined;
  app.model.data.grouping = rule;
  // The identities ARE the values of the grouping columns, so groups declared under the previous rule name
  // things that no longer exist.
  app.model.data.contendingGroups = undefined;
  snapshotPanelColumns();
}

// Visible reason when the Combine-mode column is invalid, so a disabled Run button is explained. args() is
// the authoritative gate, and this mirrors the same condition into an inline alert. The selector is not
// offered today, but a project saved or migrated while it was can still carry a colliding value.
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
// mixcr-clonotyping and demultiplex-fastq use, Preview first. Feature-barcode is single-cell and shallow
// per cell, so the dry-run default matches mixcr's single-cell recommendation of 500k reads per sample.
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
// re-emitting the value it already held -- a re-pick of the dataset already picked, or a re-render after a
// block-pack update -- otherwise discards configuration nobody touched.
//
// Concretely: re-emitting an UNCHANGED FASTQ ref wipes `sampleColumn`, the run reaches per_cell_metrics.py
// with no `--sample-col`, and its duplicate-barcode guard refuses a sample-keyed panel. The user meets that
// as a QuickJS stack trace minutes after a gesture that changed nothing.
//
// The previous value has to be remembered HERE: `v-model` writes the new one into data before the handler
// runs. Keyed by JSON, so a ref object and a file handle compare the same way.
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
// map into data, so the args projection stays pure and the per-sample workflow body can translate its
// iteration key.
function setSampleColumn(col: string | undefined) {
  app.model.data.sampleColumn = col || undefined;
  // Snapshot both the dataset's sampleId->name map AND the chosen column's CSV values, so args() can both
  // filter per sample and gate Run purely from data.
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
// giving different columns and values. Split from its gesture handler because `clearOnCsvChange` calls it
// too, and THAT path must clear unconditionally.
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
// args() needs are snapshotted. args() is data-only and the CSV meta lives on ctx.prerun, so without this
// the model can see the problem and still not refuse the run.
const seenBarcodeColumn = ref(keyOf(app.model.data.barcodeSeqColumn));

// Called from every gesture that can make a duplicate mapping RELEVANT, not only from the one that makes it
// knowable. The case that actually happens is the SAMPLE column being cleared long after the barcode column
// was picked, which turns a legal sample-keyed panel into an illegal duplicate one. Idempotent.
function snapshotPanelCounts() {
  const col = app.model.data.barcodeSeqColumn;
  app.model.data.panelRowCount = col ? app.model.outputs.csvRowCount : undefined;
  app.model.data.panelBarcodeDistinct = col
    ? (app.model.outputs.csvValuesByColumn?.[col]?.length ?? undefined)
    : undefined;
}

// Claiming a column as a key invalidates any verdict setting that names it. The panel reader strips the
// barcode and sample columns before the properties are read, so the setting would name a column that is no
// longer a property. args() refuses the run in that state, but the user still has to find the stale pick in
// a dropdown that stopped offering it.
function clearVerdictSettingsNaming(column: string | undefined) {
  if (!column) return;
  if (app.model.data.roleColumn === column) {
    // The values designate values of THIS column, so they go with it -- the same pairing setRoleColumn keeps.
    app.model.data.roleColumn = undefined;
    app.model.data.referenceValues = undefined;
  }
  const remaining = groupingColumns(app.model.data.grouping).filter((c) => c !== column);
  if (remaining.length !== groupingColumns(app.model.data.grouping).length) {
    // A grouping may name several columns, so losing one leaves the others standing. Losing the last leaves
    // no rule, which reads as one identity per tag.
    app.model.data.grouping =
      remaining.length > 0 ? { by: "property", columns: remaining } : undefined;
    // The identities ARE the values of the grouping columns, so declared groups now name things that do
    // not exist.
    app.model.data.contendingGroups = undefined;
  }
}

function onBarcodeColumnChanged(next: unknown) {
  if (!changed(seenBarcodeColumn, next)) return;
  snapshotPanelCounts();
  clearVerdictSettingsNaming(app.model.data.barcodeSeqColumn);
}

// A CSV swap invalidates every CSV-derived selection: the barcode and feature-name columns, the negative
// control, the sample-aware selection, and every binding-reading setting that names a panel column or
// value. The last group matters most: emit_verdicts.py ends the whole run when the role column or the
// grouping column is not one the panel carries, and reports it where the user never looks.
function onCsvChanged(next: unknown) {
  if (!changed(seenCsvHandle, next)) return;
  // The feature-name column is about to be cleared, so its own guard must not later read a stale key and
  // decide the user's re-pick was a no-op.
  seenFeatureColumn.value = "";
  clearOnCsvChange();
  // Read the panel NOW, from the file the user just chose, rather than waiting for the upload to land and a
  // workflow step to describe it. Reading the argument rather than data makes the handler independent of
  // listener order.
  void readPanelFrom(next as ImportFileHandle | undefined);
}

// Fills csvMetaSnapshot from the picked file. Local picks are read from disk, which is what makes the
// column dropdowns fill on the gesture. A remote pick reads nothing here and is served by the blob watcher
// below once the upload lands.
//
// The handle re-check before the write is the rapid-re-pick guard: the read is async, so a user who swaps
// files twice in quick succession can have the FIRST read resolve last.
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
// This watcher writes to data and cannot feed itself. The output it watches comes from the prerun, and the
// prerun re-renders only when the prerunArgs PROJECTION changes (canonical-JSON compared in
// pl-middle-layer's setStates, which gates renderStagingFor). That projection is tagFeatureCsvHandle alone.
// Adding the snapshot to prerunArgs WOULD close that loop, and because a staging re-render resets staging,
// each turn would throw away the uploaded blob. Leave the projection alone.
//
// Two clients open on one project both run this and both write, which is safe because they cannot disagree:
// the parse is pure and both read the same blob.
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

// Sample-aware mapping sanity warning from the model. Only present once a sample column is chosen.
const sampleMappingWarning = computed(() => app.model.outputs.sampleMappingWarning);

// Sample-aware mapping is auto-selected. Where the model spots a CSV column whose distinct values match the
// dataset's sample names, pre-populate the Sample column dropdown through setSampleColumn, which snapshots
// the sample map into data. Guarded to run only while NO column is set, so a manual clear or pick is never
// overridden. suggestedSampleColumn derives from the CSV meta and sample labels alone, and depends on
// neither sampleColumn nor the snapshot fields setSampleColumn writes, so applying it cannot re-trigger the
// suggestion.
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
    // The Open affordance, the same one the Explore readout puts on its clonotype column.
    // `invokeRowsOnDoubleClick` makes the button fire the ROW's double-click, so it routes through the
    // `onRowDoubleClicked` handler below: one path, and double-clicking anywhere on the row keeps working.
    cellRendererSelector: () => ({
      component: PlAgTextAndButtonCell,
      params: { invokeRowsOnDoubleClick: true },
    }),
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
  // Quality status tag (OK / WARN / ALERT): the sample's rolled-up status, taken by the software over its
  // own measurements. Blank while the sample is still running, and blank on a finished sample where no
  // measurement carried a line to judge it.
  createAgGridColDef<SampleResult, QcStatus | undefined>({
    colId: "quality",
    field: "quality",
    headerName: "Quality",
    headerComponentParams: {
      type: "Text",
      info:
        "The worst status among this sample's own quality measurements. A measurement carries a status " +
        "only where a published or stated line stands behind it; the rest are shown with their value and " +
        "no status.\n" +
        "Blank means no measurement of this sample carried a line, which is not the same as OK.\n" +
        "Double-click the sample to see every measurement, its value, and the reason where it has none.",
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

    <!-- Main shows ONLY per-sample progress. The per-cell results table lives on its own "Per-cell results"
         tab. The in-memory progress grid is always shown: it handles layout, its Progress cell, and the
         loading overlay for the pre-roster window. results.ts settles every row into "Done" from
         completedSamples. -->
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
        required
        @update:model-value="onFastqRefChanged"
      >
        <template #tooltip>
          Select the FASTQ dataset with the tag-barcode reads. These reads give each tag's unique
          count in each cell.
        </template>
      </PlDropdownRef>
      <PlDropdownRef
        v-model="app.model.data.datasetRef"
        :options="app.model.outputs.datasetOptions"
        label="Single-cell V(D)J dataset"
        required
      >
        <template #tooltip>
          Select the dataset that supplies the clonotypes. Every verdict is about one clonotype.
        </template>
      </PlDropdownRef>
      <!-- Read layout: preset dropdown plus pattern builder/string (mitool tag pattern). -->
      <PatternEditor />
      <!-- Above Panel Settings: it scopes the whole run rather than reading the panel, and it is the choice a
           user revisits between runs while the panel columns stay put. -->
      <PlBtnGroup v-model="app.model.data.runMode" :options="runModeOptions" label="Run mode">
        <template #tooltip>
          Preview uses only the first reads of each sample, up to the limit below. Use it to check
          settings before a full run.<br /><br />
          Preview verdicts rest on fewer cells, so more antigens read unreliable. Do not compare a
          Preview card with a full run.
        </template>
      </PlBtnGroup>
      <template v-if="app.model.data.runMode === 'dry'">
        <PlNumberField
          v-model="app.model.data.limitInput"
          label="Reads per sample"
          :clearable="true"
          :min-value="1"
          :error-message="
            app.model.data.limitInput == null
              ? 'Read limit is required for Preview mode'
              : undefined
          "
        >
          <template #tooltip>
            How many reads Preview takes from each sample. The block takes the first reads of the
            file, not a random sample. 500,000 reads is enough to check settings.
          </template>
        </PlNumberField>
      </template>
      <PlSectionSeparator compact> Panel Settings </PlSectionSeparator>
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Panel file"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
        @update:model-value="onCsvChanged"
      >
        <template #tooltip>
          Upload the CSV that maps each tag barcode to an antigen. One row per barcode, or one row
          per barcode and sample.<br /><br />
          The block asks a sample only about the antigens this file declares for it.
        </template>
      </PlFileInput>
      <PlAlert v-if="csvProcessing" type="info"> Reading columns from the uploaded CSV… </PlAlert>
      <PlAlert v-if="app.model.data.csvImportError" type="warn">
        Could not read the tag-feature CSV: {{ app.model.data.csvImportError }}
      </PlAlert>
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
        @update:model-value="onBarcodeColumnChanged"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          Select the panel column with each tag barcode's nucleotide sequence. The block matches
          these against the barcode captured on Read 2.
        </template>
      </PlDropdown>
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Antigen name column"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          Select the panel column with the antigen name for each barcode. These names label the
          antigens in every result.<br /><br />
          Where two samples give one barcode different names, the antigen carries both names,
          joined.
        </template>
      </PlDropdown>
      <!-- Identity is what a verdict is about, so the rule that mints identities belongs with the panel
           columns that supply it rather than among the reading thresholds below. -->
      <PlDropdownMulti
        :model-value="groupingSelection"
        :options="groupingOptions"
        label="Identity grouping"
        :disabled="panelUnread"
        :required="true"
        @update:model-value="setGrouping"
      >
        <template #tooltip>
          Select the panel columns that define an identity. Tags that share a value in all of them
          become one identity. A verdict is about an identity, not a barcode.<br /><br />
          Select the barcode column to get one identity per barcode.<br /><br />
          An identity's reading in a cell is the highest of its tags, never their sum.
        </template>
      </PlDropdownMulti>
      <PlDropdown
        :model-value="app.model.data.sampleColumn"
        :options="roleFreeColumnOptions"
        label="Sample column"
        :disabled="tagMappingDisabled"
        clearable
        @update:model-value="setSampleColumn"
      >
        <template #tooltip>
          <b>Set this when the panel file lists each barcode once per sample.</b> Leave it blank
          when each barcode has one row.<br /><br />
          Each sample is then asked only about the identities its own rows declare. Every sample in
          your dataset must appear in the column.<br /><br />
          The block selects this when it detects a matching column.
        </template>
      </PlDropdown>
      <!-- The baseline comes before the optional settings because it decides which of them apply: a setting
           belonging to one rule is shown only where that rule runs. -->
      <PlSectionSeparator compact> Baseline Parameters </PlSectionSeparator>
      <PlDropdown
        :model-value="chosenSource"
        :options="allSources"
        label="Baseline source"
        :disabled="panelUnread"
        required
        @update:model-value="setBaselineSource"
      >
        <template #tooltip>
          Select what each count is read against. The block does not choose for you. Two baselines
          give numbers that do not compare.<br /><br />
          <b>Declared baseline tag</b> — the tag your panel marks as binding nothing.<br />
          <b>Each tag's own distribution</b> — that tag's counts across the sample's cells, split in
          two.<br /><br />
          The form then asks only for what your choice needs.
        </template>
      </PlDropdown>
      <!-- What the chosen rung still needs. Not an error: the rung is a legitimate choice and the fields that
           satisfy it are directly below. -->
      <PlAlert v-if="chosenNeeds" type="warn">{{ chosenNeeds }}</PlAlert>
      <PlDropdown
        v-if="chosenSource === 'declared'"
        :model-value="app.model.data.roleColumn"
        :options="panelPropertyOptions"
        label="Role column"
        :disabled="panelUnread"
        required
        @update:model-value="setRoleColumn"
      >
        <template #tooltip>
          Select the panel column that declares each tag's role. One value in it marks the baseline
          tag.<br /><br />
          If your panel declares no role, change the baseline source instead.
        </template>
      </PlDropdown>
      <!-- Required exactly while a role column is named, and not otherwise. The column alone marks no tag: it
           is validated, recorded, and changes no number, so half the pair is an unfinished form. A panel that
           declares no role chooses a different baseline source rather than leaving these blank. -->
      <PlDropdown
        v-if="chosenSource === 'declared'"
        :model-value="app.model.data.referenceValues?.[0]"
        :options="roleValueOptions"
        label="Baseline value"
        :disabled="panelUnread || !app.model.data.roleColumn"
        :required="!!app.model.data.roleColumn"
        clearable
        @update:model-value="setReferenceValue($event)"
      >
        <template #tooltip>
          Select the value that marks the baseline tag. Required once you name a role column.<br /><br />
          The block reads counts against <b>one</b> baseline tag. If the value marks more than one
          tag, the run stops and names them.
        </template>
      </PlDropdown>

      <!-- Directly under the baseline section rather than with the other accordion at the foot of the form,
           which split the baseline's own thresholds away from the baseline. -->
      <PlNumberField
        v-model="app.model.data.distributionMinCells"
        v-if="chosenSource === 'distribution'"
        :min-value="1"
        :step="10"
        label="Minimum cells per sample"
      >
        <template #tooltip>
          How many cells a sample needs before a tag's own counts can serve as the baseline. Below
          this, every reading in that sample is unreliable.<br /><br />
          The default of 300 comes from the published method. Lowering it departs from that method.
        </template>
      </PlNumberField>

      <!-- The sticky-cell controls sit in the baseline section rather than under a header of their own. They
           read a cell's own baseline reading, so they exist only where a declared tag supplies one -- a fitted
           population gives no per-cell reading to compare against. ONE threshold, not two: only a declared gate
           supplies a *high*, so with the gate off the quality readout carries the spread of the readings
           instead. -->
      <PlNumberField
        v-model="app.model.data.gateThreshold"
        v-if="chosenSource === 'declared'"
        :min-value="1"
        :step="1"
        clearable
        label="Admissibility gate"
      >
        <template #tooltip>
          Empty means off, which is the default. Set it, in baseline unique counts, and the block
          sets aside any cell whose baseline reading goes above this value. That cell gives no
          verdict anywhere.<br /><br />
          Off matches Cell Ranger defaults. The cost is that a sticky cell stays in the set and
          returns a confident "not bound".
        </template>
      </PlNumberField>

      <!-- Closable, and closed until opened. Every field inside ships a default a run is valid under. -->
      <PlAccordionSection label="Threshold Parameters">
        <!-- Paired on one line: both are minimums on how much evidence a reading needs, and side by side a
               reader sets them as the one decision they are. -->
        <PlRow>
          <PlNumberField
            :class="$style.half"
            v-model="app.model.data.countFloor"
            :min-value="0"
            :step="1"
            label="Min count"
          >
            <template #tooltip>
              Counts below this are not evidence of binding. The block reads them as zero.<br /><br />
              The minimum never applies to the baseline tag. A minimum there would push the whole
              run toward bound.<br /><br />
              The default of 4 is declared, not calibrated.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="app.model.data.minVotingCells"
            :min-value="1"
            :step="1"
            label="Min voting cells"
          >
            <template #tooltip>
              How many cells must answer before their majority settles a verdict. Below this the
              verdict reads unreliable.<br /><br />
              At 1 a verdict may rest on one cell. The table carries the answering-cell count.
            </template>
          </PlNumberField>
        </PlRow>
        <!-- Both are conditions on the same verdict: the cutoff decides what one cell says, the agreement
             limit decides how many cells must say it. `half` on each splits the row; once the cutoff is hidden,
             the same `flex: 1 1 0` gives the lone field the whole width. -->
        <PlRow>
          <PlNumberField
            v-if="chosenSource === 'declared'"
            :class="$style.half"
            v-model="app.model.data.boundCutoff"
            :min-value="0"
            :max-value="100"
            :step="1"
            label="Bound score cutoff"
          >
            <template #tooltip>
              A cell reads bound where its score reaches this number, from 0 to 100. The score is
              how certain it is that the antigen makes up more than 92.5% of the antigen and
              baseline counts.<br /><br />
              <b>Certainty, not strength</b> — two counts against zero score low. Cell Ranger says
              this score does not measure binding strength.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="agreementPercent"
            :min-value="50.001"
            :max-value="100"
            :step="1"
            clearable
            label="Min agreement (>50%)"
          >
            <template #tooltip>
              <b>Empty means off</b>, which is the default. A narrow majority then stands, and the
              verdict reports how narrow it was.<br /><br />
              Set it and a verdict reads unreliable below this share of the answering cells.<br /><br />
              The lowest value is 50.001%, because the verdict already takes the majority.
            </template>
          </PlNumberField>
        </PlRow>
      </PlAccordionSection>
      <!-- The combine-mode column selector is not offered: with it unset, every antigen uses the default
           "sum" mode. combineColumn and minUmi still reach per_cell_metrics.py, so a value carried in from an
           older project is still honoured, and the alert below explains a Run button greyed out by one that
           collides with a role. -->
      <PlAlert v-if="combineColumnError" type="warn">
        {{ combineColumnError }}
      </PlAlert>
      <PlAlert v-if="sampleMappingWarning?.length" type="warn">
        <div v-for="(line, i) in sampleMappingWarning" :key="i">{{ line }}</div>
      </PlAlert>
      <!-- Beside its siblings rather than under the Sample column control: all three are about the same CSV,
           and a reader scanning for what is wrong should find them in one place. barcodeMappingIssue was once
           computed by the model and rendered NOWHERE, so a user met a QuickJS stack trace from
           per_cell_metrics.py's own guard minutes after the gesture that caused it. -->
      <!-- One rung earlier: this fires when the chosen column holds no sequences at all, so the panel the run
           is built on is not barcodes. First of the two, and the model suppresses the duplicate-barcode warning
           while it is showing -- that warning would send a reader chasing the sample column. -->
      <PlAlert v-if="app.model.outputs.barcodeAlphabetIssue" type="warn">
        {{ app.model.outputs.barcodeAlphabetIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.barcodeMappingIssue" type="warn">
        {{ app.model.outputs.barcodeMappingIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.unkeyedSamplePanel" type="warn">
        {{ app.model.outputs.unkeyedSamplePanel }}
      </PlAlert>
      <!-- Less-common params. -->
      <PlAccordionSection label="Compute resources">
        <!-- Hidden with the control it belongs to. This is the AND-combine per-barcode floor, and the
             Combine-mode column selector is not offered, so every antigen uses "sum" and this value can never
             take effect. Restore both together.
        <PlNumberField
          v-model="app.model.data.minUmi"
          :min-value="1"
          :step="1"
          clearable
          label="Minimum unique counts per tag barcode"
        >
          <template #tooltip>
            <b>For "all"-combine antigens only</b> — it applies when the run carries a combine-mode
            column, which is not offered today. Minimum unique counts a barcode needs to count as
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
            one too. It reaches the parse and refine-tags steps only. Tag-stat stays sized from its
            own input.
          </template>
        </PlNumberField>
      </PlAccordionSection>

      <!-- The QC lines, separate from Compute resources: these change what a measurement's own status reads,
           never what the run computes. Every field here is clearable -- empty means the shipped default, the
           same number the tooltip names. -->
      <PlAccordionSection label="Quality lines">
        <PlRow>
          <PlNumberField
            :class="$style.half"
            v-model="cellBarcodeValidWarn"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Barcode validity warn"
          >
            <template #tooltip>
              The share of reads whose cell barcode corrects onto the chemistry's whitelist. The
              measurement warns below this share.<br /><br />
              Default 0.75. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="cellBarcodeValidError"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Barcode validity alert"
          >
            <template #tooltip>
              The same share. The measurement alerts below this share instead of warning.<br /><br />
              Default 0.50. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.
            </template>
          </PlNumberField>
        </PlRow>
        <PlNumberField
          v-model="readsPerCellWarn"
          :min-value="0"
          :step="100"
          clearable
          label="Reads per cell warn"
        >
          <template #tooltip>
            Reads matched per cell in the cell list. The measurement warns below this count. It has
            no alert line, because the vendor published one boundary.<br /><br />
            Default 5000. The vendor recommends this minimum for this assay type. Nothing calibrates
            it against your own data, and no test asserts it.
          </template>
        </PlNumberField>
        <PlRow>
          <PlNumberField
            :class="$style.half"
            v-model="aggregateBarcodeWarn"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Aggregate reads warn"
          >
            <template #tooltip>
              The share of reads in barcodes flagged as aggregates. The measurement warns above this
              share.<br /><br />
              Default 0.05. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.<br /><br />
              The three detection settings below decide which barcodes count as aggregates, so each
              one changes what this line judges.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="aggregateBarcodeError"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Aggregate reads alert"
          >
            <template #tooltip>
              The same share. The measurement alerts where the share equals this value, and not
              above it.<br /><br />
              Default 1.0, which is total failure. The field supplies this number. Nothing
              calibrates it for this assay, and no test asserts it.
            </template>
          </PlNumberField>
        </PlRow>
        <PlRow>
          <PlNumberField
            :class="$style.half"
            v-model="undeclaredBarcodeWarn"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Undeclared reads warn"
          >
            <template #tooltip>
              The share of a sample's reads in barcodes the panel never declared. The measurement
              warns above this share.<br /><br />
              Default 0.50. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="undeclaredBarcodeError"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Undeclared reads alert"
          >
            <template #tooltip>
              The same share. The measurement alerts where the share equals this value, and not
              above it.<br /><br />
              Default 1.0, which is total failure. The field supplies this number. Nothing
              calibrates it for this assay, and no test asserts it.
            </template>
          </PlNumberField>
        </PlRow>
        <PlRow>
          <PlNumberField
            :class="$style.half"
            v-model="usableReadWarn"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Usable reads warn"
          >
            <template #tooltip>
              The share of the library's reads that reach a called cell with a panel-recognised
              barcode. The measurement warns below this share.<br /><br />
              Default 0.20. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.
            </template>
          </PlNumberField>
          <PlNumberField
            :class="$style.half"
            v-model="usableReadError"
            :min-value="0"
            :max-value="1"
            :step="0.01"
            clearable
            label="Usable reads alert"
          >
            <template #tooltip>
              The same share. The measurement alerts where the share equals this value, and not
              below it.<br /><br />
              Default 0.0, which is total failure. At that default the alert fires only where no
              read is usable. The field supplies this number. Nothing calibrates it for this assay,
              and no test asserts it.
            </template>
          </PlNumberField>
        </PlRow>
      </PlAccordionSection>

      <!-- No Aggregate-barcode detection section. The three knobs behind it stay in BlockData and in args, so
           a stored project keeps whatever it set, and an unset one runs on Cell Ranger's own constants. They
           have no control because the 0.05 warn line was calibrated against those constants, and no line in the
           field covers a moved position. -->
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

<style module>
/* Two fields sharing a row split it evenly. Left to their natural width they sit left with a gap
   beside them, which reads as a field missing rather than as a pair. */
.half {
  flex: 1 1 0;
  min-width: 0;
}
</style>
