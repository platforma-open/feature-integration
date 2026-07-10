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
const logsOpen = ref(false);

// No-negative-control info note in the Settings drawer: appears once the tag-feature CSV is added,
// and hides as soon as a negative control feature is selected.
const controlInfoVisible = computed(
  () => !!app.model.data.tagFeatureCsvHandle && !app.model.data.controlFeature,
);

// True while staging is still parsing the uploaded tag-feature CSV (handle set, but the column/value
// metadata hasn't resolved yet). Drives a "reading columns…" note and disables the CSV-derived
// dropdowns, so their empty state reads as "loading" rather than "no columns found".
const csvProcessing = computed(() => app.model.outputs.csvColumnsLoading === true);

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

// CSV swap invalidates both the negative control and the sample-aware selection (columns/values change).
function clearOnCsvChange() {
  clearControlOnInputChange();
  clearSampleAwareOnInputChange();
}

// Sample-aware mapping sanity warning from the model (dataset samples missing from the CSV / CSV sample
// values matching no dataset sample). Only present once a sample column is chosen.
const sampleMappingWarning = computed(() => app.model.outputs.sampleMappingWarning);

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
    headerComponentParams: { type: "Progress" } satisfies PlAgHeaderComponentParams,
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
    headerComponentParams: { type: "Text" } satisfies PlAgHeaderComponentParams,
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
    headerComponentParams: { type: "Text" } satisfies PlAgHeaderComponentParams,
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
};
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
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
      />
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
      />
      <PlAlert v-if="csvProcessing" type="info"> Reading columns from the uploaded CSV… </PlAlert>
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
        :disabled="csvProcessing"
        required
      />
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature name column"
        :disabled="csvProcessing"
        required
        @update:model-value="clearControlOnInputChange"
      />
      <PlSectionSeparator compact> Optional settings </PlSectionSeparator>
      <PlRow>
        <PlDropdown
          v-model="app.model.data.controlFeature"
          :options="app.model.outputs.controlOptions"
          label="Negative control feature"
          :disabled="csvProcessing"
          clearable
          :style="{ flex: 1 }"
        />

        <PlDropdown
          :model-value="app.model.data.sampleColumn"
          :options="app.model.outputs.csvColumnOptions"
          label="Sample column"
          :disabled="csvProcessing"
          clearable
          :style="{ flex: 1 }"
          @update:model-value="setSampleColumn"
        >
          <template #tooltip>
            <b>Optional</b> — leave empty unless the same feature barcode maps to a different
            feature depending on the sample. If so, pick the Tag-feature CSV column holding the
            sample name (must match the dataset's sample names).
          </template>
        </PlDropdown>
      </PlRow>
      <PlAlert v-if="controlInfoVisible" type="info">
        Specificity scores will not be computed without a negative control feature
      </PlAlert>
      <PlAlert v-if="sampleMappingWarning?.length" type="warn">
        <div v-for="(line, i) in sampleMappingWarning" :key="i">{{ line }}</div>
      </PlAlert>
      <!-- Less-common params. -->
      <PlAccordionSection label="Advanced Settings">
        <PlNumberField
          v-model="app.model.data.dominanceThreshold"
          :min-value="0.5"
          :max-value="1"
          :step="0.05"
          label="Dominance threshold"
          helper="Fraction of a cell's signal one feature must reach to be the consensus. Floor 0.5."
        />
      </PlAccordionSection>
    </PlSlideModal>

    <PlSlideModal v-model="logsOpen" width="80%">
      <template #title>Analysis logs</template>
      <PlLogView :value="analysisLog.join('\n')" />
    </PlSlideModal>
  </PlBlockPage>
</template>
