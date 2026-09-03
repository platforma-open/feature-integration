<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();

const qcSummarySettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.qcSummaryTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>Per-sample QC</template>
    <PlAgDataTableV2
      v-if="app.model.outputs.qcSummaryTable"
      v-model="app.model.data.qcSummaryTableState"
      :settings="qcSummarySettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      One row per sample, carrying that sample's rolled-up status and every quality measurement,
      appears here once a V(D)J dataset is chosen and the block has run.
    </PlAlert>
  </PlBlockPage>
</template>
