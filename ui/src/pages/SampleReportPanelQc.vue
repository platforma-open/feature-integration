<script setup lang="ts">
import { computed } from "vue";
import QcSection from "../components/QcSection.vue";
import type { SampleResult } from "../results";

// The Quality Checks tab: this sample's own report. Every sample-level measurement the software declares
// takes a row, including the ones it could not compute.
//
// The rollup and the coverage triple are NOT repeated here. The Main grid's Quality column already carries
// the rollup, and each row below states its own status, so a second copy at the top said what the column
// and the rows had both already said. `qcReport.status` and the triple still travel, for that column.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

const report = computed(() => props.sampleData?.qcReport);

// DECLARATION ORDER, exactly as the software emits it. That order is deliberate -- the reads first, then
// every observed barcode, then the V(D)J-matched cells, then what the reading rules removed -- and rows
// that belong together are declared together.
//
// This used to float the judgeable rows to the top, which scrambled all of that: a reader saw the five
// rows carrying a threshold, then the other eight, and neither half was the pipeline's order. Scanning
// for trouble is what the status tags are for.
const orderedMeasurements = computed(() => report.value?.measurements ?? []);
</script>

<template>
  <div v-if="report">
    <QcSection v-for="m in orderedMeasurements" :key="m.id" :value="m" />
  </div>
  <div v-else class="qc-pending">
    Quality checks appear when this sample finishes — its measurements are computed from the
    completed parse, refine-tags and UMI-count reports and from the binding read itself. The Log tab
    already works: the per-step logs are what the running pipeline is producing right now.
  </div>
</template>

<style lang="css" scoped>
.qc-pending {
  padding: 24px 8px;
  color: var(--color-txt-03);
  font-size: 14px;
  line-height: 20px;
}
</style>
