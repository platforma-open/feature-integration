<script setup lang="ts">
import type { SimpleOption } from "@platforma-sdk/ui-vue";
import { PlBtnGroup } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { sampleResults } from "../results";
import SampleReportPanelLogs from "./SampleReportPanelLogs.vue";
import SampleReportPanelQc from "./SampleReportPanelQc.vue";
import SampleReportPanelVisualReport from "./SampleReportPanelVisualReport.vue";

// Per-sample report, opened from the Main grid on row double-click. This file is the host that picks what
// the reader is looking at, and each view lives in its own child component.
const sampleId = defineModel<string | undefined>();

// The panel reads the same per-sample view model the grid renders, rather than going back to the raw model
// outputs, so the panel's numbers and the grid's Quality and Read recovery columns are not two independent
// derivations of one QC row.
const sampleData = computed(() => {
  if (sampleId.value === undefined) return undefined;
  return sampleResults.value?.find((result) => result.sampleId === sampleId.value);
});

// Visual Report is the default tab. Opening a finished sample should land on what happened to its reads,
// not on a log the reader has no reason to open once the sample is green.
type TabId = "visualReport" | "qc" | "logs";
const currentTab = ref<TabId>("visualReport");
const tabOptions: SimpleOption<TabId>[] = [
  { value: "visualReport", text: "Visual Report" },
  { value: "qc", text: "Quality Checks" },
  { value: "logs", text: "Log" },
];
</script>

<!-- One v-if / v-else-if chain over the tabs, and nothing else gating it. Each tab owns its own
     not-yet-available state: the two report tabs need QC that only settles when the sample finishes, while
     the Log tab needs nothing but the sample id, so a running sample must still reach it. -->
<template>
  <PlBtnGroup v-model="currentTab" :options="tabOptions" />
  <SampleReportPanelVisualReport v-if="currentTab === 'visualReport'" :sample-data="sampleData" />
  <SampleReportPanelQc v-else-if="currentTab === 'qc'" :sample-data="sampleData" />
  <SampleReportPanelLogs v-else-if="currentTab === 'logs'" :sample-id="sampleId" />
</template>
