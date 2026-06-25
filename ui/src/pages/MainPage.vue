<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdown,
  PlDropdownRef,
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
      Per-cell results appear once the feature-barcode workflow is implemented (pending the assay /
      mitool design spike). Configure inputs via Settings.
    </PlAlert>

    <PlSlideModal v-model="settingsOpen">
      <template #title>Settings</template>
      <PlDropdownRef
        v-model="app.model.data.fbFastqRef"
        :options="app.model.outputs.fastqOptions"
        label="Feature-barcode FASTQ"
      />
      <PlDropdownRef
        v-model="app.model.data.tagFeatureCsvRef"
        :options="app.model.outputs.csvOptions"
        label="Tag → feature CSV"
      />
      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Negative-control feature (optional)"
      />
      <PlNumberField v-model="app.model.data.dominanceThreshold" label="Dominance threshold" />
    </PlSlideModal>
  </PlBlockPage>
</template>
