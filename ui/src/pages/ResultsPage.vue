<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();

// The collapsed per-cell results table, one row per [sampleId, cellId]: the per-cell aggregates (Max feature
// UMI count, Max feature fraction) and the "Feature breakdown" string. Main is the progress grid, so this
// table lives on its own tab, reading the perCellTable model output.
const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>Per-cell tag counts</template>
    <PlAgDataTableV2
      v-if="app.model.outputs.perCellTable"
      v-model="app.model.data.tableState"
      :settings="tableSettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      Per-cell feature results appear here once you set the inputs in Settings (on the Main page)
      and run the block.
    </PlAlert>
  </PlBlockPage>
</template>
