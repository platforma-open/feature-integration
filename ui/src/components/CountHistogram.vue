<script setup lang="ts">
import { PlChartHistogram } from "@platforma-sdk/ui-vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

// One binned count distribution, drawn from precomputed bins.
//
// `PlChartHistogram` rather than `GraphMaker`: this is a plot, not a chart builder. A reader here is asked
// one question -- do these two humps stand apart -- and every control a builder offers is a way to stop
// answering it. It also takes a `threshold`, which is how a declared gate draws its marker; a builder has
// no marker mechanism at all.
//
// The `log-bins` form, because the edges are log-spaced: UMI counts per cell span orders of magnitude, and
// on a linear axis the ambient population occupies one bar with emptiness above it.
//
// `totalWidth` MUST be passed. The component draws an SVG at a fixed width and measures nothing, so its
// 674px default overflows any narrower container and paints over whatever sits beside it. Measured here
// with a ResizeObserver, so a grid cell of any width holds its own chart.
const props = defineProps<{
  /** Bin boundaries, `weights.length + 1` of them, shared across every plot of a run. */
  edges: number[];
  /** Cells per bin, in edge order. */
  weights: number[];
  title?: string;
  /** Drawn as a marker. Undefined draws none, which is the statement that no gate is declared. */
  threshold?: number;
  totalHeight?: number;
  xAxisLabel?: string;
  yAxisLabel?: string;
}>();

const host = ref<HTMLElement | undefined>(undefined);
// Wide enough for an axis and a few bars. Below this the chart is unreadable rather than merely small, and
// a zero from a container not yet laid out would make d3 compute a negative plot width.
const MIN_WIDTH = 220;
const width = ref(MIN_WIDTH);
let observer: ResizeObserver | undefined;

onMounted(() => {
  const el = host.value;
  if (el === undefined) return;
  observer = new ResizeObserver((entries) => {
    const measured = entries[0]?.contentRect.width ?? 0;
    width.value = Math.max(MIN_WIDTH, Math.floor(measured));
  });
  observer.observe(el);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = undefined;
});

const settings = computed(() => ({
  type: "log-bins" as const,
  // A bin's own bounds travel with its weight, since the component bins nothing itself.
  bins: props.weights.map((weight, i) => ({
    from: props.edges[i]!,
    to: props.edges[i + 1]!,
    weight,
  })),
  ...(props.threshold === undefined ? {} : { threshold: props.threshold }),
  ...(props.title === undefined ? {} : { title: props.title }),
  xAxisLabel: props.xAxisLabel ?? "Counts per cell",
  yAxisLabel: props.yAxisLabel ?? "Cells",
  totalWidth: width.value,
  totalHeight: props.totalHeight,
}));
</script>

<template>
  <!-- The host is what gets measured, so it must be free to be narrower than the chart's default. -->
  <div ref="host" :class="$style.host">
    <PlChartHistogram :settings="settings" />
  </div>
</template>

<style module>
.host {
  width: 100%;
  min-width: 0;
  /* A drawn SVG wider than the measurement it was built from -- one frame during a resize -- is clipped
     rather than allowed to paint over a neighbour. */
  overflow: hidden;
}
</style>
