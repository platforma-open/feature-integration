<script setup lang="ts">
import SampleReportPanelLogs from "./SampleReportPanelLogs.vue";

// Per-sample report, opened from the Main grid on row double-click. Mirrors
// blocks/mixcr-clonotyping's SampleReportPanel: this file is the host that picks what the reader is
// looking at, and each view lives in its own child component.
const sampleId = defineModel<string | undefined>();
</script>

<template>
  <SampleReportPanelLogs :sample-id="sampleId" />
</template>
