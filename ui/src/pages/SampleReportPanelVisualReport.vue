<script setup lang="ts">
import { PlChartStackedBar } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import type { SampleResult } from "../results";
// DEFERRED with the antigen-count section below. Uncomment these three together with it.
// import { PlAlert } from "@platforma-sdk/ui-vue";
// import { useApp } from "../app";
// import CountHistogram from "../components/CountHistogram.vue";

// The Visual Report tab: where this sample's reads went. The antigen-count half is deferred, so the tab
// carries the read-recovery breakdown alone. RecoveryBar is already the settings object a stacked bar wants
// ({ title, data: [{ label, value, color, description }] }).
//
// PlChartStackedBar, never PlAgChartStackedBarCell. The Ag* one is an ag-grid cell renderer taking
// ICellRendererParams, so using it outside a grid would mean fabricating a fake cell params object.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

// DEFERRED with the antigen-count section below.
// const app = useApp();

// The chart's own legend gives colour and label only. The segment meanings are spelled out below the chart
// instead, so the legend would be a redundant second colour key.
const settings = computed(() => {
  const recovery = props.sampleData?.recovery;
  return recovery === undefined ? undefined : { ...recovery, showLegends: false };
});

// Read counts and shares per segment, for the written breakdown under the chart. The descriptions
// results.ts attaches to each segment are otherwise reachable only by hovering the bar, and the equivalent
// prose in the grid only by hovering the column header.
const segments = computed(() => {
  const recovery = props.sampleData?.recovery;
  if (recovery === undefined) return undefined;
  const total = recovery.data.reduce((sum, segment) => sum + segment.value, 0);
  return recovery.data.map((segment) => ({
    label: segment.label,
    color: segment.color.toString(),
    // The description results.ts builds carries the label and the read count on their own lines. Those are
    // shown as fields here, so keep only the explanatory middle line.
    meaning: segment.description.split("\n")[1] ?? "",
    printedValue:
      total > 0
        ? `${segment.value.toLocaleString()} reads (${((segment.value / total) * 100).toFixed(1)}%)`
        : `${segment.value.toLocaleString()} reads`,
  }));
});

// --- this sample's antigen counts, one plot per tag ----------------------------------------------
//
// DEFERRED, with the template block and the three imports above. Uncomment all of them together.
//
// The shape of the antigen counts is a plot about ONE sample, so it belongs with that sample rather than on
// the run's own page. Per TAG, because a total pools a tag that bound nothing with one that bound
// everything and draws one hump from the two.
//
// Drawn with PlChartHistogram from precomputed bins, never the chart builder. The bins come off the RAW
// counts, before the minimum, which is one of the things the plot is read in order to set.
//
// The model output, the `tagCountBins` JSON behind it and the `.visual-report__tag*` styles below are all
// untouched and still live.
//
// const tagBins = computed(() => app.model.outputs.tagCountBins);
//
// const panels = computed(() => {
//   const bins = tagBins.value;
//   const sampleId = props.sampleData?.sampleId;
//   if (bins === undefined || sampleId === undefined) return undefined;
//   const byTag = bins.bySample[sampleId];
//   if (byTag === undefined) return [];
//   // Sorted by the NAME a reader sees, so the reading order is the one on screen rather than the barcode
//   // order behind it. A tag the panel named nowhere reads as its own barcode and sorts under it.
//   return Object.keys(byTag)
//     .map((tag) => ({ tag, label: bins.tagLabels?.[tag] ?? tag, weights: byTag[tag]! }))
//     .sort((a, b) => a.label.localeCompare(b.label));
// });
</script>

<template>
  <div class="visual-report">
    <div v-if="settings && segments" class="visual-report__block">
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

    <!-- DEFERRED: the antigen counts for this sample, one plot per barcode. Uncomment this block
             together with `tagBins`, `panels` and the three imports in the script above.

             Two states are told apart here. The first alert is the run having reported nothing yet. The
             second is a report in which this sample held no counted reading on any barcode.

             `compact` drew bars only. This grid has no enlarge action, so no panel showed an axis. Restore an
             enlarge action or drop `compact` if a reader has to read a count off one of these.

    <div class="visual-report__block">
      <div class="visual-report__title">Antigen count per cell, by barcode</div>
      <PlAlert v-if="panels === undefined" type="info">
        No count distributions have arrived from this run yet. They are taken by the same verdict
        stage as the read-recovery breakdown, so they arrive once the run reports.
      </PlAlert>
      <PlAlert v-else-if="panels.length === 0" type="info">
        No barcode in this sample held a counted reading, so there is no distribution to draw.
      </PlAlert>
      <div v-else class="visual-report__tagGrid">
        <div v-for="panel in panels" :key="panel.tag" class="visual-report__tagPanel">
          <div class="visual-report__tagTitle">{{ panel.label }}</div>
          <CountHistogram
            :edges="tagBins!.edges"
            :weights="panel.weights"
            :total-height="140"
            compact
          />
        </div>
      </div>
    </div>
    -->
  </div>
</template>

<style lang="css" scoped>
.visual-report {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.visual-report__block {
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

/* Small multiples, one per barcode: as many per row as fit, each wide enough to read a shape. */
.visual-report__tagGrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px 16px;
}

.visual-report__tagPanel {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.visual-report__tagTitle {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-txt-01);
}

.visual-report__title {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
}

.visual-report__pending {
  padding: 24px 8px;
  color: var(--color-txt-03);
  font-size: 14px;
  line-height: 20px;
}
</style>
