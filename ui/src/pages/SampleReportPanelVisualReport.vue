<script setup lang="ts">
import { PlChartStackedBar } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import type { SampleResult } from "../results";

// The Visual Report tab: where this sample's reads went. RecoveryBar is already the settings object a
// stacked bar wants ({ title, data: [{ label, value, color, description }] }), so this tab is a
// presentation of data results.ts has been building for the grid all along.
//
// PlChartStackedBar (not PlAgChartStackedBarCell) is the component used here: the Ag* one is an ag-grid
// cell renderer that takes ICellRendererParams, so using it outside a grid would mean fabricating a
// fake cell params object. PlChartStackedBar is the same chart's standalone form — the grid's cell
// renderer is itself only a thin wrapper around the compact variant of it.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

// The chart's own legend gives colour and label only. The segment meanings are spelled out below the
// chart instead, so the legend would be a redundant second colour key.
const settings = computed(() => {
  const recovery = props.sampleData?.recovery;
  return recovery === undefined ? undefined : { ...recovery, showLegends: false };
});

// Read counts and shares per segment, for the written breakdown under the chart. The descriptions
// results.ts attaches to each segment are otherwise reachable only by hovering the bar, and the
// equivalent prose in the grid only by hovering the column header — a reader who does neither should
// still learn what the segments mean.
const segments = computed(() => {
  const recovery = props.sampleData?.recovery;
  if (recovery === undefined) return undefined;
  const total = recovery.data.reduce((sum, segment) => sum + segment.value, 0);
  return recovery.data.map((segment) => ({
    label: segment.label,
    color: segment.color.toString(),
    // The description results.ts builds carries the label and the read count on their own lines; those
    // are shown as fields here, so keep only the explanatory middle line.
    meaning: segment.description.split("\n")[1] ?? "",
    printedValue:
      total > 0
        ? `${segment.value.toLocaleString()} reads (${((segment.value / total) * 100).toFixed(1)}%)`
        : `${segment.value.toLocaleString()} reads`,
  }));
});
</script>

<template>
  <div v-if="settings && segments" class="visual-report">
    <PlChartStackedBar :settings="settings" />
    <div class="visual-report__segments">
      <div v-for="segment in segments" :key="segment.label" class="visual-report__segment">
        <div class="visual-report__swatch" :style="{ backgroundColor: segment.color }" />
        <div class="visual-report__text">
          <div class="visual-report__label">{{ segment.label }}: {{ segment.printedValue }}</div>
          <div class="visual-report__meaning">{{ segment.meaning }}</div>
        </div>
      </div>
    </div>
  </div>
  <div v-else class="visual-report__pending">
    The read-recovery breakdown appears when this sample finishes — it is computed from the read
    counts in the completed parse and refine-tags reports. The Log tab already works: the per-step
    logs are what the running pipeline is producing right now.
  </div>
</template>

<style lang="css" scoped>
.visual-report {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.visual-report__segments {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.visual-report__segment {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  gap: 12px;
}

.visual-report__swatch {
  width: 12px;
  min-width: 12px;
  height: 12px;
  margin-top: 4px;
  border-radius: 2px;
}

.visual-report__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.visual-report__label {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
}

.visual-report__meaning {
  font-weight: 500;
  font-size: 14px;
  line-height: 20px;
  color: var(--color-txt-03);
}

.visual-report__pending {
  padding: 24px 8px;
  color: var(--color-txt-03);
  font-size: 14px;
  line-height: 20px;
}
</style>
