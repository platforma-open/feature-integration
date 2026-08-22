<script setup lang="ts">
import { computed } from "vue";
import QcSection from "../components/QcSection.vue";
import { qcChecks, type SampleResult } from "../results";

// The Quality Checks tab: the per-sample QC status broken out into one row per check. The Main grid's Quality
// column can only show the worst of these. Here the reader sees which metric produced it.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

// qc is absent until the sample's QC settles at completion. qcChecks is the same function the grid's tag is
// derived from, so a row here can never disagree with the tag in the grid.
const checks = computed(() => {
  const qc = props.sampleData?.qc;
  return qc === undefined ? undefined : qcChecks(qc);
});
</script>

<template>
  <div v-if="checks">
    <QcSection v-for="(check, i) in checks" :key="i" :value="check" />
  </div>
  <div v-else class="qc-pending">
    Quality checks appear when this sample finishes — its QC metrics are computed from the completed
    parse, refine-tags and UMI-count reports. The Log tab already works: the per-step logs are what
    the running pipeline is producing right now.
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
