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
// surfaced in a Logs slide-over so the run isn't a black box.
const logEntries = computed(() => app.model.outputs.stepLogs?.data ?? []);
const logsOpen = ref(false);

// No-negative-control info banner: shown when results exist and no control is set, until the user
// dismisses it (dismissal persisted in data so it stays hidden).
const controlInfoVisible = computed(
  () =>
    app.model.outputs.perCellTable !== undefined &&
    !app.model.data.controlFeature &&
    !app.model.data.controlInfoDismissed,
);
function dismissControlInfo() {
  app.model.data.controlInfoDismissed = true;
}
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
    <template #append>
      <PlBtnGhost v-if="logEntries.length > 0" @click.stop="logsOpen = true">Logs</PlBtnGhost>
      <PlBtnGhost @click.stop="settingsOpen = true">Settings</PlBtnGhost>
    </template>

    <PlAlert
      v-if="controlInfoVisible"
      type="info"
      closeable
      @update:model-value="dismissControlInfo"
    >
      No negative control selected — specificity scores are not computed. Pick a "Negative-control
      feature" in Settings to add them. Consensus feature shows the assigned antigen,
      <b>ambiguous</b> when no feature passes the dominance threshold, and is empty when the cell
      has no feature signal.
    </PlAlert>

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
        v-if="app.model.data.tagFeatureCsvHandle"
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode sequence column"
      />
      <PlDropdown
        v-if="app.model.data.tagFeatureCsvHandle"
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature name column"
      />
      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Negative control feature (optional)"
      />
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

    <PlSlideModal v-model="logsOpen">
      <template #title>Logs</template>
      <PlLogView
        v-for="entry in logEntries"
        :key="entry.key.join('/')"
        :label="entry.key.join(' / ')"
        :log-handle="entry.value"
      />
    </PlSlideModal>
  </PlBlockPage>
</template>
