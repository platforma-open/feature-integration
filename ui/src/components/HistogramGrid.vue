<script setup lang="ts">
import { PlDialogModal } from "@platforma-sdk/ui-vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import CountHistogram from "./CountHistogram.vue";

// A grid of small multiples over ONE shared edge set, with any panel enlargeable on click.
//
// A grid rather than a selector: the judgement these panels are read for -- do two humps stand apart,
// does this sample reach the line -- reads at thumbnail size, and behind a selector nobody looks at all
// of them. The enlarged view is where a value gets read off an axis.
//
// Split out of `FittedBackgroundGrid`, which is its only caller today. What a panel IS belongs to the
// adapter; the button affordance, the modal, the caption line and the spacing belong here.
//
// Bar height is a plain cell count, and that is safe because every bar is the same width. The caller's
// edges decide that: `log1p_bin_edges` gives equal widths on a symlog axis, and a linear axis over equal
// linear bins gives them too. A caller mixing widths would have to divide by them, and getting that
// division wrong once flattened a real signal hump out of sight.
export type HistogramPanel = {
  /** Stable across redraws, so Vue keeps the DOM node and the chart is not rebuilt on every tick. */
  key: string;
  title: string;
  /** One weight per bin, in the order of the shared `edges`. */
  weights: number[];
  /** A vertical marker. Undefined draws none, and its absence is the statement that no line applies. */
  threshold?: number;
  /** One line under the thumbnail. Always present: a panel with nothing to say says why. */
  caption: string;
  /** Extra lines, shown only in the enlarged panel, under the caption. */
  detail?: string[];
};

const props = defineProps<{
  /** ONE edge set for every panel. Panels are comparable side by side only if they share an axis. */
  edges: number[];
  panels: HistogramPanel[];
  xAxisLabel: string;
}>();

const enlarged = ref<HistogramPanel | undefined>(undefined);
const isOpen = computed({
  get: () => enlarged.value !== undefined,
  set: (open: boolean) => {
    if (!open) enlarged.value = undefined;
  },
});

// Re-read from `panels` by key rather than held as a snapshot: the run can settle while the modal is
// open, and a snapshot would go on showing the numbers the panel carried when it was clicked.
const shown = computed(() =>
  enlarged.value === undefined
    ? undefined
    : (props.panels.find((p) => p.key === enlarged.value?.key) ?? enlarged.value),
);

// The uikit's `drawBins` appends its hover tooltip to the BODY on every draw and never removes it. It
// hides itself on mouseout, so a chart destroyed mid-hover -- changing the sample with the pointer over a
// bar -- leaves a visible readout stranded at the top of the window with nothing to dismiss it.
//
// Swept rather than tracked, because a div in the body carries nothing saying which chart owns it. Safe
// here only because of WHEN it runs: the panel keys carry their sample, so a change of sample replaces
// every panel, and each redraw makes itself a fresh div. A chart whose div is taken without a redraw
// following would lose its tooltip until its next one.
//
// This clears what is already on screen; it does not stop the leak, which belongs in the uikit.
const sweepStrandedTooltips = () => {
  for (const el of document.querySelectorAll("body > .svg-tooltip")) el.remove();
};

watch(() => props.panels, sweepStrandedTooltips);
onBeforeUnmount(sweepStrandedTooltips);
</script>

<template>
  <div :class="$style.grid">
    <!-- Enlarging is a button rather than a click handler on the panel: the affordance has to be
         reachable without a pointer. -->
    <button
      v-for="panel in panels"
      :key="panel.key"
      type="button"
      :class="$style.panel"
      :title="`Enlarge ${panel.title}`"
      @click="enlarged = panel"
    >
      <div :class="$style.header">
        <span :class="$style.title">{{ panel.title }}</span>
        <span :class="$style.enlarge">⤢</span>
      </div>
      <!-- `compact`: bars only. The thumbnail is scanned for a shape; the enlarged panel carries axes.
           Not negotiable at this size -- the component's non-compact mode fixes its top margin at 0 and
           its y ticks at six, so on a panel holding a handful of cells it clips the top label and counts
           them in fifths. Neither is reachable from the settings it accepts. -->
      <CountHistogram
        :edges="edges"
        :weights="panel.weights"
        :threshold="panel.threshold"
        :total-height="140"
        :x-axis-label="xAxisLabel"
        compact
      />
      <span :class="$style.caption">{{ panel.caption }}</span>
    </button>
  </div>

  <PlDialogModal v-model="isOpen" width="720px">
    <template #title>{{ shown?.title }}</template>
    <template v-if="shown">
      <CountHistogram
        :edges="edges"
        :weights="shown.weights"
        :threshold="shown.threshold"
        :total-height="420"
        :x-axis-label="xAxisLabel"
      />
      <div :class="$style.enlargedDetail">
        <div>{{ shown.caption }}</div>
        <div v-for="(line, i) in shown.detail ?? []" :key="i">{{ line }}</div>
      </div>
    </template>
  </PlDialogModal>
</template>

<style module>
/* Small multiples: as many per row as fit, each wide enough for two humps to read apart. */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px 16px;
  overflow-y: auto;
  /* Horizontal padding matches `.panel`'s -4px margin, so the panels' hover bleed stays inside this box.
     Without it the last column ends 4px past the content edge, and `overflow-y: auto` makes `overflow-x`
     compute to `auto` as well, which turns those 4px into a permanent horizontal scrollbar. */
  padding: 4px 4px 16px;
}

.panel {
  display: flex;
  flex-direction: column;
  /* GraphMaker sets its title 16px off the plot. Halved here, where the panels are thumbnails. */
  gap: 8px;
  min-width: 0;
  /* A button carrying a plot, so every button default is unset rather than styled around. */
  appearance: none;
  background: none;
  border: none;
  padding: 4px;
  margin: -4px;
  border-radius: 6px;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.panel:hover {
  background: var(--color-bg-elevated-01, rgb(0 0 0 / 4%));
}

.panel:hover .enlarge {
  opacity: 1;
}

/* Centred over the plot, which is where GraphMaker puts a chart's title, so the two kinds of chart on this
   page carry their names the same way. The enlarge affordance is taken out of the flow rather than given
   its own column, so it cannot pull the title off centre. */
.header {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 8px;
  position: relative;
}

/* GraphMaker's chart title is medium-weight and near-black, centred over the plot, so a panel here wears
   the same weight and colour. Not the same SIZE: that title sits over one full-width chart, and nine of
   it across a grid of small multiples would outweigh the plots it names. */
.title {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-txt-01);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enlarge {
  position: absolute;
  right: 0;
  font-size: 13px;
  color: var(--color-txt-03);
  opacity: 0;
  transition: opacity 0.1s;
}

/* Left, not centred like the title above it. A caption is a sentence that varies in length panel to
   panel, and centring makes a column of them ragged on both edges. */
.caption {
  font-size: 12px;
  color: var(--color-txt-03);
  font-variant-numeric: tabular-nums;
  text-align: left;
}

/* The enlarged panel's readout: one statement per line, because these are read rather than scanned and
   three facts on one line ran together. Tabular figures so numbers line up under each other. */
.enlargedDetail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--color-txt-02);
  font-variant-numeric: tabular-nums;
}
</style>

<!-- PlChartHistogram's settings carry data, labels and size, and nothing about how it looks: the bar fill,
     the bar outline and the threshold colour are written as INLINE styles by its d3 draw, which is why
     these rules carry `!important` rather than simply being more specific. Values match the GraphMaker
     charts on the same page, so a reader moving between tabs reads one chart style. Its gridlines and font
     already match, so neither is touched here. -->
<style scoped>
:deep(svg rect) {
  fill: #99e099 !important;
  stroke: var(--color-txt-01, #110529) !important;
  stroke-opacity: 1 !important;
  stroke-width: 1 !important;
}

:deep(svg line[style*="dasharray"]) {
  stroke: var(--color-txt-01, #110529) !important;
}
</style>
