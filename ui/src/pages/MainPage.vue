<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdown,
  PlDropdownRef,
  PlFileInput,
  PlNumberField,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { ref } from "vue";
import { useApp } from "../app";

const app = useApp();
const settingsOpen = ref(false);

const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
    <template #append>
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
        label="Feature-barcode FASTQ"
      />
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Tag → feature CSV"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
      />
      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Negative-control feature (optional)"
      />
      <PlNumberField v-model="app.model.data.dominanceThreshold" label="Dominance threshold" />

      <!-- Read geometry (DP-1): 10x 5' v2 defaults; confirm against the actual assay FASTQs. -->
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
    </PlSlideModal>
  </PlBlockPage>
</template>
