<script setup lang="ts">
import {
  PlAccordionSection,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdown,
  PlDropdownRef,
  PlFileInput,
  PlLogView,
  PlNumberField,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

const app = useApp();
// Auto-open Settings for a fresh block (no FASTQ chosen yet); stay closed once configured.
const settingsOpen = ref(app.model.data.fbFastqRef === undefined);

const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});

// Per-sample × per-step mitool/Python log streams (key = [sampleId, step]; value = log handle),
// surfaced in a wide Logs slide-over. A sample selector shows one sample's steps at a time so a
// many-sample run doesn't stack hundreds of log views.
const logEntries = computed(() => app.model.outputs.stepLogs?.data ?? []);
const logsOpen = ref(false);

// Distinct sampleIds (key[0]) that produced logs, sorted for a stable dropdown.
const logSamples = computed(() => {
  const ids = new Set<string>();
  for (const e of logEntries.value) ids.add(String(e.key[0]));
  return [...ids].sort();
});
const sampleOptions = computed(() => logSamples.value.map((s) => ({ value: s, label: s })));

// Selected sample is local view state (a ref, never written to data — output→ref is not a hairpin).
const selectedLogSample = ref<string>();
function openLogs() {
  if (
    selectedLogSample.value === undefined ||
    !logSamples.value.includes(selectedLogSample.value)
  ) {
    selectedLogSample.value = logSamples.value[0];
  }
  logsOpen.value = true;
}

// The selected sample's per-step logs, ordered by the "N-step" key prefix.
const sampleStepLogs = computed(() =>
  logEntries.value
    .filter((e) => String(e.key[0]) === selectedLogSample.value)
    .slice()
    .sort((a, b) => String(a.key[1]).localeCompare(String(b.key[1]))),
);

// Human labels for the workflow step keys (see fb-pipeline.tpl.tengo stepLogs).
const STEP_LABELS: Record<string, string> = {
  "0-panel": "Panel build",
  "1-parse": "Parse",
  "2-refine": "Refine tags",
  "3-tagstat": "Tag-stat",
  "4-metrics": "Per-cell metrics",
  "5-qc": "QC report",
};
const stepLabel = (step: string) => STEP_LABELS[step] ?? step;

// mitool steps emit progress lines with this marker (fb-pipeline sets MI_PROGRESS_PREFIX); PlLogView
// renders them as a compact progress bar instead of a raw stream. Matches PlLogView's own default.
const MITOOL_PROGRESS_PREFIX = "[==PROGRESS==]";
const MITOOL_STEPS = new Set(["1-parse", "2-refine", "3-tagstat"]);

// No-negative-control info note in the Settings drawer: appears once the tag-feature CSV is added,
// and hides as soon as a negative control feature is selected.
const controlInfoVisible = computed(
  () => !!app.model.data.tagFeatureCsvHandle && !app.model.data.controlFeature,
);
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
    <template #append>
      <PlBtnGhost v-if="logEntries.length > 0" @click.stop="openLogs">Logs</PlBtnGhost>
      <PlBtnGhost @click.stop="settingsOpen = true">Settings</PlBtnGhost>
    </template>

    <PlAgDataTableV2
      v-if="app.model.outputs.perCellTable"
      v-model="app.model.data.tableState"
      :settings="tableSettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      Per-cell feature results appear after you set the inputs in Settings and run the block.
    </PlAlert>

    <PlSlideModal v-model="settingsOpen">
      <template #title>Settings</template>
      <PlDropdownRef
        v-model="app.model.data.fbFastqRef"
        :options="app.model.outputs.fastqOptions"
        label="Select dataset"
      />
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Tag-feature CSV"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
      />
      <PlDropdown
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
        required
      />
      <PlDropdown
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature name column"
        required
      />
      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Negative control feature (optional)"
        clearable
      />
      <PlAlert v-if="controlInfoVisible" type="info">
        Specificity scores will not be computed without a negative control feature
      </PlAlert>
      <!-- Less-common params: dominance threshold + read geometry (DP-1: 10x 5' v2 defaults). -->
      <PlAccordionSection label="Advanced Settings">
        <PlNumberField
          v-model="app.model.data.dominanceThreshold"
          :min-value="0.5"
          :max-value="1"
          :step="0.05"
          label="Dominance threshold"
          helper="Fraction of a cell's signal one feature must reach to be the consensus. Floor 0.5 (spec A-0012)."
        />
        <PlNumberField
          v-model="app.model.data.cellLen"
          :min-value="1"
          :step="1"
          label="Cell barcode length (R1)"
        />
        <PlNumberField
          v-model="app.model.data.umiLen"
          :min-value="1"
          :step="1"
          label="UMI length (R1)"
        />
        <PlNumberField
          v-model="app.model.data.featureLen"
          :min-value="1"
          :step="1"
          label="Feature barcode length (R2)"
        />
      </PlAccordionSection>
    </PlSlideModal>

    <PlSlideModal v-model="logsOpen" width="80%">
      <template #title>Pipeline logs</template>
      <PlDropdown
        v-if="logSamples.length > 1"
        v-model="selectedLogSample"
        :options="sampleOptions"
        label="Sample"
      />
      <PlLogView
        v-for="entry in sampleStepLogs"
        :key="entry.key.join('/')"
        :label="stepLabel(String(entry.key[1]))"
        :log-handle="entry.value"
        :progress-prefix="
          MITOOL_STEPS.has(String(entry.key[1])) ? MITOOL_PROGRESS_PREFIX : undefined
        "
      />
    </PlSlideModal>
  </PlBlockPage>
</template>
