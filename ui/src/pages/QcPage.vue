<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlLogView,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "../app";

const app = useApp();

const tagstatSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.tagstatQcTable,
});

// Per-sample × per-step mitool/Python log streams. key = [sampleId, step]; value = log handle.
const logEntries = computed(() => app.model.outputs.stepLogs?.data ?? []);
</script>

<template>
  <PlBlockPage>
    <template #title>QC</template>

    <h3>Raw tag-stat counts</h3>
    <PlAgDataTableV2
      v-if="app.model.outputs.tagstatQcTable"
      v-model="app.model.data.tagstatTableState"
      :settings="tagstatSettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      The raw per-(cell, feature-barcode) UMI counts appear here once the block has run.
    </PlAlert>

    <h3>Pipeline logs</h3>
    <PlAlert v-if="logEntries.length === 0" type="info">
      Per-step mitool and Python logs appear here while the block runs.
    </PlAlert>
    <template v-else>
      <PlLogView
        v-for="entry in logEntries"
        :key="entry.key.join('/')"
        :label="entry.key.join(' / ')"
        :log-handle="entry.value"
      />
    </template>
  </PlBlockPage>
</template>
