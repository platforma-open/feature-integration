<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();

// The collapsed per-cell results table (one row per [sampleId, cellId]): consensus feature + per-cell
// aggregates (Max UMI / fraction / specificity) + the "Feature breakdown" string. This was the block's
// original Main page; Main is now the progress grid, so the table lives on its own tab (operator
// feedback 2026-07-03). The perCellTable model output has stayed in place throughout.
const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>Per-cell results</template>
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
