<script setup lang="ts">
import type { SimpleOption } from "@platforma-sdk/ui-vue";
import { PlBtnGroup, PlLogView } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

// The Log tab of the per-sample report: the live per-step logs across the whole pipeline, meaning parse,
// refine, tag-stat and the Python per-cell-metrics step. Mirrors blocks/peptide-extraction's
// SampleReportPanel Logs tab.
//
// A plain prop rather than a model, because the log view only reads the selection. Nothing here can change
// which sample the slide-over is showing.
const props = defineProps<{
  sampleId: string | undefined;
}>();

const app = useApp();

// Log step selector. Refine and tag-stat run only where reads matched the pattern, so a no-match sample
// carries its 1-parse log alone, since fb-refine-tagstat emits a variable key set, and those steps then
// show "No log available". Order matches the pipeline. 4-metrics, the Python per-cell-metrics step, comes
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
  if (props.sampleId === undefined) return undefined;
  // The Python metrics step is surfaced flat, as metricsLog keyed [sampleId]. The three mitool steps live
  // in the [sampleId, step] stepLogs map.
  if (currentStep.value === "4-metrics") {
    return app.model.outputs.metricsLog?.data.find((p) => String(p.key[0]) === props.sampleId)
      ?.value;
  }
  const logs = app.model.outputs.stepLogs;
  if (!logs) return undefined;
  return logs.data.find(
    (p) => String(p.key[0]) === props.sampleId && p.key[1] === currentStep.value,
  )?.value;
});
</script>

<template>
  <PlBtnGroup v-model="currentStep" :options="stepOptions" />
  <PlLogView v-if="logHandle" :log-handle="logHandle" />
  <div v-else style="padding: 24px; color: var(--color-txt-03); font-size: 14px">
    No log available for this step yet.
  </div>
</template>
