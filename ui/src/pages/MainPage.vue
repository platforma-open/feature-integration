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
  PlBlockPage,
  PlBtnGhost,
  PlBtnGroup,
  PlDropdown,
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
import { groupingColumns } from "@platforma-open/milaboratories.feature-integration.model";
import type { ColDef, GridReadyEvent } from "ag-grid-enterprise";
import { ClientSideRowModelModule, ModuleRegistry } from "ag-grid-enterprise";
import { AgGridVue } from "ag-grid-vue3";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import PatternEditor from "../components/PatternEditor.vue";
import VerdictSettings from "../components/VerdictSettings.vue";
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
// Close the Settings drawer once a run starts. Watching an output and writing a local ref is not a
// hairpin, since nothing writes to server-stored data.
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

// No-negative-control info note in the Settings drawer. Appears once the tag-feature CSV is added, and
// hides as soon as a negative control feature is selected.
const controlInfoVisible = computed(
  () => !!app.model.data.tagFeatureCsvHandle && !app.model.data.controlFeature,
);

// True while staging is still parsing the uploaded tag-feature CSV: the handle is set, but the column and
// value metadata have not resolved. Drives a "reading columns…" note and disables the CSV-derived
// dropdowns, so their empty state reads as "loading" rather than "no columns found".
const csvProcessing = computed(() => app.model.outputs.csvColumnsLoading === true);

// The CSV-derived tag-mapping dropdowns (barcode / feature / control / sample columns) have nothing to
// offer until a tag-feature CSV is uploaded AND its columns are parsed. Disable and dim them while no CSV
// handle exists, or while staging is still reading its columns, so their empty state reads as "waiting for
// a CSV" rather than "no columns found". Reuses the SDK disabled and dimmed affordance the parse window
// (csvProcessing) already uses.
const tagMappingDisabled = computed(
  () => !app.model.data.tagFeatureCsvHandle || csvProcessing.value,
);

// CSV columns not already bound to the barcode-sequence or feature-name roles. A column holding DNA
// barcodes or antigen names is not a sample column, and offering it invites a mis-pick. The data layer
// refuses two roles on one column, but only at the end of the run. The model's args() rejects the
// collision too, and filtering here prevents the mistake up front.
const roleFreeColumnOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) =>
      o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.featureNameColumn,
  ),
);

// Visible reason when the Combine-mode column is invalid, so a disabled Run button is explained rather
// than mysterious. The model's args() is the authoritative gate, throwing and greying out Run, and this
// mirrors the same condition into an inline alert the user sees. The selector is not offered today, but a
// project saved while it was, or migrated, can still carry a value that collides with the barcode or
// feature roles, and without this the Run button would simply be grey.
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

// A negative control is one of the feature-name column's values, so changing the CSV or the feature-name
// column can leave the current selection naming a feature that no longer exists. Clear it on that user
// gesture. A data-to-data write on an explicit gesture, and never a watcher on the controlOptions output,
// which is the spec-facts-resync hairpin (see hairpin.md). Left stale, args() would still send it and the
// workflow would silently score specificity against a zero control: inflated scores, no error. Where the
// control is still valid after the change the user re-picks, which is cheaper than snapshotting the valid
// set into data to validate in args().
function clearControlOnInputChange() {
  app.model.data.controlFeature = undefined;
}

// A GESTURE IS NOT A CHANGE. Every clear below must compare the new value against the old one: a control
// re-emitting the value it already held — a user re-picking the dataset they had picked, or a re-render
// after the block pack was updated — otherwise discards configuration nobody touched.
//
// Concretely: re-emitting an UNCHANGED FASTQ ref wipes `sampleColumn`, the run reaches
// per_cell_metrics.py with no `--sample-col`, and its duplicate-barcode guard refuses a sample-keyed
// panel. The user meets that as a QuickJS stack trace minutes after a gesture that changed nothing.
// `clearOnCsvChange` is the same shape over nine more fields, the whole binding reading included.
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

// Sample-aware mapping (optional). Picking the sample column snapshots the CURRENT dataset's sampleId→name
// map into data, so the args projection stays pure (model.md) and the per-sample workflow body can
// translate its iteration key.
function setSampleColumn(col: string | undefined) {
  app.model.data.sampleColumn = col || undefined;
  // Snapshot both the dataset's sampleId→name map AND the chosen column's CSV values, so args() can both
  // filter per sample and gate Run (block when a dataset sample has no CSV rows) purely from data.
  app.model.data.sampleLabelSnapshot = col ? app.model.outputs.sampleLabels : undefined;
  app.model.data.sampleColumnValues = col
    ? (app.model.outputs.csvValuesByColumn?.[col] ?? [])
    : undefined;
  // Clearing the sample column is what makes a duplicate barcode illegal again, so the numbers args() gates
  // on are refreshed here rather than assumed present from an earlier gesture.
  snapshotPanelCounts();
  clearVerdictSettingsNaming(col || undefined);
}

// The snapshot goes stale where the dataset changes, giving a different sampleId→name, or the CSV changes,
// giving different columns and values. Clear the sample-aware selection on those gestures and let the user
// re-pick. Split from its gesture handler because `clearOnCsvChange` calls it too, and THAT path must
// clear unconditionally: a new panel file invalidates the sample selection whatever the FASTQ ref does.
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

// Called from every gesture that can make a duplicate mapping RELEVANT, not only from the one that makes
// it knowable. Taken on the barcode-column pick alone, the gate is inert in the case that actually
// happens: the barcode column was picked long ago, and what changes now is the SAMPLE column being
// cleared, which is what turns a legal sample-keyed panel into an illegal duplicate one. Idempotent, so
// calling it from three places costs nothing.
function snapshotPanelCounts() {
  const col = app.model.data.barcodeSeqColumn;
  app.model.data.panelRowCount = col ? app.model.outputs.csvRowCount : undefined;
  app.model.data.panelBarcodeDistinct = col
    ? (app.model.outputs.csvValuesByColumn?.[col]?.length ?? undefined)
    : undefined;
}

// Claiming a column as a key invalidates any verdict setting that names it. The panel reader strips the
// barcode and sample columns before the properties are read, so the setting would name a column that is no
// longer a property. args() refuses the run in that state, which is a blocked Run button rather than a
// dead run, but the user still has to find the stale pick in a dropdown that stopped offering it. Clearing
// it on the gesture that invalidates it is the treatment clearOnCsvChange gives the panel swap. This is
// the reassignment case, reaching the same stale pick by a different gesture.
function clearVerdictSettingsNaming(column: string | undefined) {
  if (!column) return;
  if (app.model.data.roleColumn === column) {
    // The values designate values of THIS column, so they go with it — the same pairing setRoleColumn keeps.
    app.model.data.roleColumn = undefined;
    app.model.data.referenceValues = undefined;
  }
  const remaining = groupingColumns(app.model.data.grouping).filter((c) => c !== column);
  if (remaining.length !== groupingColumns(app.model.data.grouping).length) {
    // A grouping may name several columns, so losing one leaves the others standing. Losing the last leaves
    // no rule, which reads as one identity per tag: the same state as never having set it.
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

function onFeatureColumnChanged(next: unknown) {
  if (!changed(seenFeatureColumn, next)) return;
  clearControlOnInputChange();
}

// CSV swap invalidates every CSV-derived selection: the barcode / feature-name columns (the new file's
// headers differ), the negative control, the sample-aware selection (columns/values change), and every
// setting of the binding reading that names a panel column or a panel value. The last group matters most:
// emit_verdicts.py ends the whole run when the role column or the grouping column is not one the panel
// carries, so a stale pick left behind here costs a run and reports it where the user never looks.
function onCsvChanged(next: unknown) {
  if (!changed(seenCsvHandle, next)) return;
  // The feature-name column is about to be cleared, so its own guard must not later read a stale key and
  // decide the user's re-pick was a no-op.
  seenFeatureColumn.value = "";
  clearOnCsvChange();
}

function clearOnCsvChange() {
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
  clearControlOnInputChange();
  clearSampleAwareState();
}

// Sample-aware mapping sanity warning from the model (dataset samples missing from the CSV / CSV sample
// values matching no dataset sample). Only present once a sample column is chosen.
const sampleMappingWarning = computed(() => app.model.outputs.sampleMappingWarning);

// Sample-aware mapping is auto-selected. Where the model spots a CSV column whose distinct values match
// the dataset's sample names (suggestedSampleColumn), pre-populate the Sample column dropdown with it
// through setSampleColumn, which snapshots the sample map into data. Guarded to run only while NO column
// is set, so a manual clear or a manual pick is never overridden. Safe from the reactive-write hairpin the
// block otherwise avoids: suggestedSampleColumn derives from the CSV meta and sample labels alone, and
// depends on neither sampleColumn nor the snapshot fields setSampleColumn writes, so applying it cannot
// re-trigger the suggestion. Clearing with X sticks. A CSV or dataset change re-clears through
// clearOnCsvChange, then re-suggests.
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

    <!-- Main shows ONLY per-sample progress (like MiXCR Clonotyping); the per-cell results table lives
         on its own "Per-cell results" tab (pages/ResultsPage.vue). The in-memory progress grid (same
         pattern as blocks/peptide-extraction — the grid handles layout, its Progress cell, and the
         loading overlay for the pre-roster window) is always shown: when the run finishes, every row
         settles into its "Done" state (results.ts sets status "done" from completedSamples). -->
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
        label="Feature-barcode FASTQ dataset"
        @update:model-value="onFastqRefChanged"
      >
        <template #tooltip>
          The feature-barcode FASTQ dataset to analyse. Its reads give the UMI count for each
          antigen barcode in each cell.<br /><br />
          Counts are not verdicts. Whether a cell bound an antigen is decided later, against the
          baseline, and only when you also give a V(D)J dataset.
        </template>
      </PlDropdownRef>
      <!-- Read layout: preset dropdown + pattern builder/string (mitool tag pattern). Replaces the
           former cell/UMI/feature length fields — their values are now decided inside the editor. -->
      <PatternEditor />
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Tag-feature CSV"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
        @update:model-value="onCsvChanged"
      >
        <template #tooltip>
          The CSV that declares your panel: which feature barcode is which antigen. One row per
          barcode — or one row per barcode and sample, when you set a sample column below.
          <br /><br />
          This file is also the authority on what each sample was offered. An antigen it does not
          declare for a sample is never asked about there.
        </template>
      </PlFileInput>
      <PlAlert v-if="csvProcessing" type="info"> Reading columns from the uploaded CSV… </PlAlert>
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
        @update:model-value="onBarcodeColumnChanged"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          CSV column holding the feature-barcode nucleotide sequence. The block matches these
          sequences against the barcode that the <code>FEATURE</code> tag captures on Read 2 — the
          second read of each pair.
        </template>
      </PlDropdown>
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature name column"
        :disabled="tagMappingDisabled"
        required
        @update:model-value="onFeatureColumnChanged"
      >
        <template #tooltip>
          The CSV column holding the antigen name each barcode maps to. These names label the
          antigens everywhere the block reports them.<br /><br />
          Where two samples give one barcode different names, the antigen carries both names,
          joined.
        </template>
      </PlDropdown>
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
            Feature-barcode libraries are shallow per cell. 500,000 reads is enough to check that
            settings work.
          </template>
        </PlNumberField>
      </template>
      <PlSectionSeparator compact> Optional settings </PlSectionSeparator>
      <!-- One field per line. Side by side in a PlRow each got half the drawer's width, which truncated
           both labels — "Control feature marker (outp…" told the user nothing about what it does not
           change, and that label carries the whole distinction from the baseline setting below.
           The sample column comes first: it changes what the run computes, while the control marker
           only labels a feature in the output. -->
      <PlDropdown
        :model-value="app.model.data.sampleColumn"
        :options="roleFreeColumnOptions"
        label="Sample column"
        :disabled="tagMappingDisabled"
        clearable
        @update:model-value="setSampleColumn"
      >
        <template #tooltip>
          <b>Set this when your panel CSV has more than one row per barcode</b> — normally because
          it lists each barcode once per sample. Leave it blank when the CSV has exactly one row per
          barcode.<br /><br />
          Names the CSV column holding each row's sample. Every sample in your dataset must appear
          in it. Extra values are allowed.<br /><br />
          The block then reads a separate panel for each sample. Each sample is asked only about the
          identities its own rows declare. One barcode can also name a different antigen in a
          different sample.<br /><br />
          The block selects this when it detects a matching column.
        </template>
      </PlDropdown>

      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Control feature marker (output only)"
        :disabled="tagMappingDisabled"
        clearable
      >
        <template #tooltip>
          <b>Pick the panel member nothing should bind, if the panel has one.</b><br /><br />
          This setting only labels that feature in the block output. A downstream block reads the
          label and can keep the control out of its antigen metrics. It changes no count and no
          verdict here.<br /><br />
          <b>It does not make the control your baseline.</b> To measure counts against it, declare
          the same feature under "Baseline (background) level" below.<br /><br />
          Leave it blank if you have no control.
        </template>
      </PlDropdown>
      <!-- The combine-mode column selector is not offered (MILAB-6496): with it unset, every antigen
           uses the default "sum" mode. The parameter itself is live — combineColumn and minUmi still
           reach per_cell_metrics.py — so a value carried in from an older project is still honoured,
           and the alert below explains a Run button greyed out by one that collides with a role. -->
      <PlAlert v-if="combineColumnError" type="warn">
        {{ combineColumnError }}
      </PlAlert>
      <!-- "Nothing else changes" was the only informative half of this alert, and it was the vague half:
           what it meant is that this block's own numbers do not move. Said outright instead.
           No downstream block is named here on purpose. The mark's only consumer today reads it out of
           the feature axis, and which block that is can change without this text being revisited, so
           naming one would drift into a lie. "A downstream block" stays true either way. -->
      <PlAlert v-if="controlInfoVisible" type="info">
        You marked no control, so the output marks no feature as one. A downstream block cannot then
        tell your control apart from the antigens. This block's own counts and verdicts stay the
        same.
      </PlAlert>
      <PlAlert v-if="sampleMappingWarning?.length" type="warn">
        <div v-for="(line, i) in sampleMappingWarning" :key="i">{{ line }}</div>
      </PlAlert>
      <!-- Beside its siblings rather than under the Sample column control: all three are about the same
           CSV, and a reader scanning for what is wrong should find them in one place.

           barcodeMappingIssue was computed by the model and rendered NOWHERE. The block knew, at config
           time, that a barcode sat on several rows and knew which column fixed it — and said so to no
           one. What a user got instead was a QuickJS stack trace at the end of a run, from
           per_cell_metrics.py's own guard, minutes after the gesture that caused it. -->
      <!-- Same lesson as the note above, one rung earlier: this fires when the chosen column holds no
           sequences at all, so the panel the run is built on is not barcodes. First of the two, and the
           model suppresses the duplicate-barcode warning while it is showing — that warning would send
           a reader chasing the sample column when the actual mistake is the barcode column itself. -->
      <PlAlert v-if="app.model.outputs.barcodeAlphabetIssue" type="warn">
        {{ app.model.outputs.barcodeAlphabetIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.barcodeMappingIssue" type="warn">
        {{ app.model.outputs.barcodeMappingIssue }}
      </PlAlert>
      <PlAlert v-if="app.model.outputs.unkeyedSamplePanel" type="warn">
        {{ app.model.outputs.unkeyedSamplePanel }}
      </PlAlert>
      <!-- The binding reading's own settings. The same component is mounted in the Explore readout page's
           Settings drawer, so the rule that produced the card can be changed from the card — which is
           where a reader sees what the rule did, as a wall of red or a column of grey.

           Safe to offer there because everything this component EDITS sits below the "binding reading"
           line in BlockArgs: a change recovers every per-sample mitool body from cache and re-runs the
           verdict stage alone. The three input fields it reads (tagFeatureCsvHandle, barcodeSeqColumn,
           sampleColumn) are read-only here — used to tell whether the panel has loaded and to keep a
           role or grouping setting from naming a column the panel reader consumes as a key. They are
           edited on THIS page only, and they do force the whole fan-out to re-run, so keep it that way:
           putting one of them in this component would make a results-page drawer silently expensive. -->
      <VerdictSettings />
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
