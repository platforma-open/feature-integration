<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();

const tagstatSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.tagstatQcTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>Raw tag-stat counts</template>
    <PlAgDataTableV2
      v-if="app.model.outputs.tagstatQcTable"
      v-model="app.model.data.tagstatTableState"
      :settings="tagstatSettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      The raw per-(cell, feature-barcode) UMI counts appear here once the block has run.
    </PlAlert>
  </PlBlockPage>
</template>
