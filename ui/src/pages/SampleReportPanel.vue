<script setup lang="ts">
import type { SimpleOption } from "@platforma-sdk/ui-vue";
import { PlBtnGroup, PlLogView } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

// Per-sample report: the live per-step logs across the whole pipeline (parse / refine / tag-stat +
// the Python per-cell-metrics step). Opened from the Main grid on row double-click. Mirrors
// blocks/peptide-extraction's SampleReportPanel (Logs tab).
const sampleId = defineModel<string | undefined>();

const app = useApp();

// Log step selector. Refine / tag-stat only run when reads matched the pattern, so a no-match sample
// carries only its 1-parse log (fb-refine-tagstat emits a variable key set); those steps then show
// "No log available". Order matches the pipeline. 4-metrics (the Python per-cell-metrics step) comes
// from a separate [sampleId]-keyed model output (metricsLog) — see logHandle.
type StepId = "1-parse" | "2-refine" | "3-tagstat" | "4-metrics";
const stepOptions: SimpleOption<StepId>[] = [
  { value: "1-parse", text: "Parse" },
  { value: "2-refine", text: "Refine tags" },
  { value: "3-tagstat", text: "Count UMIs" },
  { value: "4-metrics", text: "Per-cell metrics" },
];
const currentStep = ref<StepId>("1-parse");

const logHandle = computed(() => {
  if (sampleId.value === undefined) return undefined;
  // The Python metrics step is surfaced flat (metricsLog, keyed [sampleId]); the three mitool steps
  // live in the [sampleId, step] stepLogs map.
  if (currentStep.value === "4-metrics") {
    return app.model.outputs.metricsLog?.data.find((p) => p.key[0] === sampleId.value)?.value;
  }
  const logs = app.model.outputs.stepLogs;
  if (!logs) return undefined;
  return logs.data.find((p) => p.key[0] === sampleId.value && p.key[1] === currentStep.value)
    ?.value;
});
</script>

<template>
  <PlBtnGroup v-model="currentStep" :options="stepOptions" />
  <PlLogView v-if="logHandle" :log-handle="logHandle" />
  <div v-else style="padding: 24px; color: var(--color-txt-03); font-size: 14px">
    No log available for this step yet.
  </div>
</template>
