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
// Auto-open Settings for a fresh block (no FASTQ chosen yet); stay closed once configured.
const settingsOpen = ref(app.model.data.fbFastqRef === undefined);
// Close the Settings drawer once a run starts. Watching an output → writing a local ref is not a
// hairpin (no write to server-stored data).
watch(
  () => app.model.outputs.isRunning,
  (running) => {
    if (running) settingsOpen.value = false;
  },
);

// The block's "Analysis logs": a live completed-sample heartbeat while the run is in progress, then a
// run-level summary when it finishes (the model builds the lines from the per-sample QC). Shown in a
// wide slide-over as one text area; detailed per-sample statistics live on the QC page.
const analysisLog = computed(() => app.model.outputs.analysisLog ?? []);
// First line of the Analysis-logs drawer: point users at the richer per-sample logs, which live behind a
// double-click on each sample row (the run-level analysisLog below is only a summary heartbeat).
const LOGS_HINT =
  "Tip: double-click any sample in the progress table to open its own detailed per-step logs (parse, refine tags, count UMIs).";
const logText = computed(() => [LOGS_HINT, "", ...analysisLog.value].join("\n"));
const logsOpen = ref(false);

// Per-sample report slide-over (live per-step mitool logs). Opened by double-clicking a grid row; the
// modal is shown whenever a sample is selected.
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

// No-negative-control info note in the Settings drawer: appears once the tag-feature CSV is added,
// and hides as soon as a negative control feature is selected.
const controlInfoVisible = computed(
  () => !!app.model.data.tagFeatureCsvHandle && !app.model.data.controlFeature,
);

// True while staging is still parsing the uploaded tag-feature CSV (handle set, but the column/value
// metadata hasn't resolved yet). Drives a "reading columns…" note and disables the CSV-derived
// dropdowns, so their empty state reads as "loading" rather than "no columns found".
const csvProcessing = computed(() => app.model.outputs.csvColumnsLoading === true);

// The CSV-derived tag-mapping dropdowns (barcode / feature / control / sample columns) have nothing to
// offer until a tag-feature CSV is uploaded AND its columns are parsed. Disable + dim them when no CSV
// handle exists yet, or while staging is still reading its columns — so their empty state reads as
// "waiting for a CSV" rather than "no columns found". Reuses the SDK disabled/dimmed affordance already
// used for the parse window (csvProcessing).
const tagMappingDisabled = computed(
  () => !app.model.data.tagFeatureCsvHandle || csvProcessing.value,
);

// CSV columns not already bound to the barcode-sequence or feature-name roles. A column holding DNA
// barcodes or antigen names is not a sample column, and offering it only invites a mis-pick: the data
// layer refuses two roles on one column, but it refuses it at the end of the run. The model's args() also
// rejects the collision; filtering here prevents the mistake up front.
const roleFreeColumnOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) =>
      o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.featureNameColumn,
  ),
);

// Visible reason when the Combine-mode column is invalid, so a disabled Run button is explained rather
// than mysterious. The model's args() is the authoritative gate (it throws and greys out Run); this
// mirrors the same condition into an inline alert the user actually sees. The selector itself is not
// offered today, but a project saved while it was — or migrated — can still carry a value that collides
// with the barcode/feature roles, and without this the Run button would simply be grey.
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

// Run mode: read-limited Preview (dry run) vs full run — same PlBtnGroup pattern as mixcr-clonotyping /
// demultiplex-fastq (Preview first). Feature-barcode is single-cell + shallow per cell, so the dry-run
// default matches mixcr's single-cell recommendation (500k reads/sample).
const runModeOptions = [
  { label: "Preview", value: "dry" as const },
  { label: "Full run", value: "full" as const },
];
const DRY_RUN_READS_DEFAULT = 500_000;
// Auto-fill the read limit when the user switches to Preview and hasn't set one (mirrors mixcr).
watch(
  () => app.model.data.runMode,
  (mode) => {
    if (mode === "dry" && app.model.data.limitInput == null)
      app.model.data.limitInput = DRY_RUN_READS_DEFAULT;
  },
);

// A negative control is one of the feature-name column's values, so changing the CSV or the feature-name
// column can make the current selection reference a feature that no longer exists. Clear it on that user
// gesture. This is a data→data write on an explicit gesture — NOT a watcher on the controlOptions output
// (that would be the spec-facts-resync hairpin; see hairpin.md). Left stale, args() would still send it
// and the workflow would silently score specificity against a zero control (inflated scores, no error).
// If the control is still valid after the change the user re-picks — cheaper than snapshotting the valid
// set into data to validate in args().
function clearControlOnInputChange() {
  app.model.data.controlFeature = undefined;
}

// Sample-aware mapping (optional). Picking the sample column snapshots the CURRENT dataset's
// sampleId→name map into data, so the args projection stays pure (model.md) and the per-sample workflow
// body can translate its iteration key.
function setSampleColumn(col: string | undefined) {
  app.model.data.sampleColumn = col || undefined;
  // Snapshot both the dataset's sampleId→name map AND the chosen column's CSV values, so args() can both
  // filter per sample and gate Run (block when a dataset sample has no CSV rows) purely from data.
  app.model.data.sampleLabelSnapshot = col ? app.model.outputs.sampleLabels : undefined;
  app.model.data.sampleColumnValues = col
    ? (app.model.outputs.csvValuesByColumn?.[col] ?? [])
    : undefined;
}

// The snapshot goes stale if the dataset changes (different sampleId→name) or the CSV changes (different
// columns/values), so clear the sample-aware selection on those gestures — the user re-picks.
function clearSampleAwareOnInputChange() {
  app.model.data.sampleColumn = undefined;
  app.model.data.sampleLabelSnapshot = undefined;
  app.model.data.sampleColumnValues = undefined;
}

// CSV swap invalidates every CSV-derived selection: the barcode / feature-name columns (the new file's
// headers differ), the negative control, the sample-aware selection (columns/values change), and every
// setting of the binding reading that names a panel column or a panel value. The last group matters most:
// emit_verdicts.py ends the whole run when the role column or the grouping column is not one the panel
// carries, so a stale pick left behind here costs a run and reports it where the user never looks.
function clearOnCsvChange() {
  app.model.data.barcodeSeqColumn = undefined;
  app.model.data.featureNameColumn = undefined;
  app.model.data.combineColumn = undefined;
  app.model.data.roleColumn = undefined;
  app.model.data.referenceValues = undefined;
  app.model.data.grouping = undefined;
  app.model.data.contendingGroups = undefined;
  app.model.data.panelColumnSnapshot = undefined;
  clearControlOnInputChange();
  clearSampleAwareOnInputChange();
}

// Sample-aware mapping sanity warning from the model (dataset samples missing from the CSV / CSV sample
// values matching no dataset sample). Only present once a sample column is chosen.
const sampleMappingWarning = computed(() => app.model.outputs.sampleMappingWarning);

// Sample-aware mapping is auto-selected. When the model spots a CSV column whose distinct values match
// the dataset's sample names (suggestedSampleColumn), pre-populate the Sample column dropdown with it via
// setSampleColumn (which snapshots the sample map into data). Guarded to run only while NO column is set,
// so a manual clear or a manual pick is never overridden. Safe from the reactive-write hairpin the block
// otherwise avoids: suggestedSampleColumn is derived from the CSV meta + sample labels only — it does not
// depend on sampleColumn or the snapshot fields setSampleColumn writes, so applying it can't re-trigger
// the suggestion. Clearing (X) sticks; a CSV/dataset change re-clears (clearOnCsvChange) then re-suggests.
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

// The progress grid is now always shown (the results-table view is retired — see template). Before the
// run starts, show the "not-ready" overlay; once it begins, show "running" until the sample roster loads.
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
      info: "Double-click a sample to open its per-step logs (parse, refine tags, count UMIs).",
    } satisfies PlAgHeaderComponentParams,
    flex: 2,
    // results.ts already produces the cell config (status / percent / text / suffix); pass it through.
    progress: (value) => value,
  }),
  // Quality status tag (OK / WARN / ALERT), worst-case per sample from the QC metrics (results.ts).
  // Blank while the sample is still running (quality is undefined until its QC settles).
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
  // Read recovery: a compact stacked bar (usable / off-panel / no pattern match). Blank until QC settles.
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
        label="Select dataset"
        @update:model-value="clearSampleAwareOnInputChange"
      >
        <template #tooltip>
          Feature-barcode FASTQ dataset to analyze. Its reads are parsed to identify which antigen
          (feature) each cell bound.
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
        @update:model-value="clearOnCsvChange"
      >
        <template #tooltip>
          CSV mapping each feature barcode to its feature (antigen) name — one row per barcode. Use
          the columns below to tell the block which column holds the barcode sequence and which
          holds the feature name.
        </template>
      </PlFileInput>
      <PlAlert v-if="csvProcessing" type="info"> Reading columns from the uploaded CSV… </PlAlert>
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
        :disabled="tagMappingDisabled"
        required
      >
        <template #tooltip>
          CSV column holding the feature-barcode nucleotide sequence. Matched against the
          <code>FEATURE</code> capture on Read 2.
        </template>
      </PlDropdown>
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature name column"
        :disabled="tagMappingDisabled"
        required
        @update:model-value="clearControlOnInputChange"
      >
        <template #tooltip>
          CSV column holding the feature (antigen) name each barcode maps to. These names label the
          per-cell antigen assignments.
        </template>
      </PlDropdown>
      <PlBtnGroup v-model="app.model.data.runMode" :options="runModeOptions" label="Run mode">
        <template #tooltip>
          Preview — processes only the first N reads per sample. Use it to check that settings (read
          geometry, tag CSV, negative control) are correct and results look reasonable before
          launching a full run, which may take much longer.
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
            Number of reads to use per sample in the dry run. Feature-barcode libraries are shallow
            per cell; 500,000 gives a representative slice.
          </template>
        </PlNumberField>
      </template>
      <PlSectionSeparator compact> Optional settings </PlSectionSeparator>
      <PlRow>
        <PlDropdown
          v-model="app.model.data.controlFeature"
          :options="app.model.outputs.controlOptions"
          label="Negative control feature"
          :disabled="tagMappingDisabled"
          clearable
          :style="{ flex: 1 }"
        >
          <template #tooltip>
            <b>If your panel includes a non-binding background control</b>, pick it here. The block
            marks that feature in its output, so a downstream reader can tell the background control
            apart from the antigens. It changes no per-cell number and no verdict. Leave blank if
            you have no control.
          </template>
        </PlDropdown>

        <PlDropdown
          :model-value="app.model.data.sampleColumn"
          :options="roleFreeColumnOptions"
          label="Sample column"
          :disabled="tagMappingDisabled"
          clearable
          :style="{ flex: 1 }"
          @update:model-value="setSampleColumn"
        >
          <template #tooltip>
            <b>When the same barcode means different antigens in different samples</b> — leave blank
            otherwise. Names the Tag-feature CSV column giving each row's sample (values must match
            the dataset's sample names); the block then builds a separate barcode→antigen mapping
            per sample. Auto-selected when a matching column is detected.
          </template>
        </PlDropdown>
      </PlRow>
      <!-- The combine-mode column selector is not offered (MILAB-6496): with it unset, every antigen
           uses the default "sum" mode. The parameter itself is live — combineColumn and minUmi still
           reach per_cell_metrics.py — so a value carried in from an older project is still honoured,
           and the alert below explains a Run button greyed out by one that collides with a role. -->
      <PlAlert v-if="combineColumnError" type="warn">
        {{ combineColumnError }}
      </PlAlert>
      <PlAlert v-if="controlInfoVisible" type="info">
        No negative control is designated, so no feature will be marked as the background control in
        the output. Nothing else changes.
      </PlAlert>
      <PlAlert v-if="sampleMappingWarning?.length" type="warn">
        <div v-for="(line, i) in sampleMappingWarning" :key="i">{{ line }}</div>
      </PlAlert>
      <!-- The binding reading's own settings. The same component is mounted in the Verdicts page's
           Settings drawer, so the rule that produced a table can be changed from the table. -->
      <VerdictSettings />
      <!-- Less-common params. -->
      <PlAccordionSection label="Advanced Settings">
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
        <PlNumberField
          v-model="app.model.data.perProcessCPUs"
          :min-value="1"
          :step="1"
          clearable
          label="mitool CPUs per sample"
        >
          <template #tooltip>
            <b>Performance tuning</b> — leave empty unless a large sample is slow. CPUs given to
            each per-sample mitool step (parse / refine / tag-stat); raising it can speed up big
            samples. Default 8.
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
            <b>Performance tuning</b> — leave empty to size RAM automatically from read volume. Set
            a fixed GiB per per-sample mitool step only if a sample runs out of memory.
          </template>
        </PlNumberField>
      </PlAccordionSection>
    </PlSlideModal>

    <PlSlideModal v-model="logsOpen" width="80%">
      <template #title>Analysis logs</template>
      <PlLogView :value="logText" />
    </PlSlideModal>

    <PlSlideModal v-model="sampleReportOpen" width="60%">
      <template #title>{{ selectedSampleLabel }} — step logs</template>
      <SampleReportPanel v-model="selectedSample" />
    </PlSlideModal>
  </PlBlockPage>
</template>
