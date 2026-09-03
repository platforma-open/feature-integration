<script setup lang="ts">
import { PlStatusTag } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import QcSection from "../components/QcSection.vue";
import { qcStatusTag, type SampleResult } from "../results";

// The Quality Checks tab: this sample's own report. Every sample-level measurement the software declares
// takes a row, including the ones it could not compute. The Main grid's Quality column carries the rollup
// at the top of this list and nothing else, so the tag and the rows beneath it cannot disagree.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

const report = computed(() => props.sampleData?.qcReport);

// Measurements that can be JUDGED first, the rest below them.
const orderedMeasurements = computed(() => {
  const all = report.value?.measurements ?? [];
  const judgeable = all.filter((m) => m.implies !== null);
  return [...judgeable, ...all.filter((m) => m.implies === null)];
});
const tag = computed(() => (report.value ? qcStatusTag(report.value.status) : undefined));

// How much of the sample was checked. Kept beside the status and out of it: a status says whether something
// is wrong, coverage says whether anybody looked.
const coverage = computed(() => {
  const r = report.value;
  if (r === undefined) return undefined;
  return [
    `${r.judged} judged`,
    `${r.unjudged} with no line to judge them against`,
    `${r.notEvaluated} not computed`,
  ].join(" · ");
});
</script>

<template>
  <div v-if="report">
    <div class="qc-rollup">
      <PlStatusTag v-if="tag" :type="tag" />
      <!-- Nothing carried a status, so the sample makes no claim. A word here would say the sample was
           checked and found fine, which is the one thing it is not. -->
      <span v-else class="qc-rollup__no-status"
        >No measurement of this sample carried a line to judge it</span
      >
      <span class="qc-rollup__coverage">{{ coverage }}</span>
    </div>
    <QcSection v-for="m in orderedMeasurements" :key="m.id" :value="m" />
  </div>
  <div v-else class="qc-pending">
    Quality checks appear when this sample finishes — its measurements are computed from the
    completed parse, refine-tags and UMI-count reports and from the binding read itself. The Log tab
    already works: the per-step logs are what the running pipeline is producing right now.
  </div>
</template>

<style lang="css" scoped>
.qc-rollup {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  padding: 12px 24px 12px 8px;
}

.qc-rollup__no-status,
.qc-rollup__coverage {
  font-size: 13px;
  color: var(--color-txt-03);
}

.qc-pending {
  padding: 24px 8px;
  color: var(--color-txt-03);
  font-size: 14px;
  line-height: 20px;
}
</style>
