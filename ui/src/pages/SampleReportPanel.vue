<script setup lang="ts">
import type { SimpleOption } from "@platforma-sdk/ui-vue";
import { PlBtnGroup, PlLogView } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

// Per-sample report: the live per-step mitool logs (parse / refine / tag-stat). Opened from the Main
// grid on row double-click. Mirrors blocks/peptide-extraction's SampleReportPanel (Logs tab), scoped to
// FI's three mitool steps — the Python per-cell-metrics step (4-metrics) is a planned follow-on.
const sampleId = defineModel<string | undefined>();

const app = useApp();

// Log step selector. Refine / tag-stat only run when reads matched the pattern, so a no-match sample
// carries only its 1-parse log (fb-refine-tagstat emits a variable key set); those steps then show
// "No log available". Order matches the pipeline.
type StepId = "1-parse" | "2-refine" | "3-tagstat";
const stepOptions: SimpleOption<StepId>[] = [
  { value: "1-parse", text: "Parse" },
  { value: "2-refine", text: "Refine tags" },
  { value: "3-tagstat", text: "Count UMIs" },
];
const currentStep = ref<StepId>("1-parse");

const logHandle = computed(() => {
  const logs = app.model.outputs.stepLogs;
  if (!logs || sampleId.value === undefined) return undefined;
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
