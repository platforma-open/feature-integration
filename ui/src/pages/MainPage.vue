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
  PlNumberField,
  PlProgressCell,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import { sampleResults } from "../results";

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

const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});

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
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
    <template #append>
      <PlBtnGhost v-if="analysisLog.length > 0" @click.stop="logsOpen = true">Logs</PlBtnGhost>
      <PlBtnGhost @click.stop="settingsOpen = true">Settings</PlBtnGhost>
    </template>

    <!-- While the run is in progress: a live per-sample progress list — every sample appears at once,
         shows live parse progress, then flips to Done. perCellTable is a withStatus output (truthy even
         while loading, so it would otherwise show its own generic overlay) — gate on isRunning so this
         list wins during the run, and fall through to the results table once the run finishes. -->
    <div
      v-if="app.model.outputs.isRunning"
      :style="{ display: 'flex', flexDirection: 'column', gap: '8px' }"
    >
      <PlProgressCell
        v-for="row in sampleResults ?? []"
        :key="row.sampleId"
        :stage="row.stage"
        :step="`${row.label} — ${row.step}`"
        :progress-string="row.progressString"
        :progress="row.percent"
      />
    </div>
    <PlAgDataTableV2
      v-else-if="app.model.outputs.perCellTable"
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
      <template #title>Analysis logs</template>
      <pre>{{ analysisLog.join("\n") }}</pre>
    </PlSlideModal>
  </PlBlockPage>
</template>
