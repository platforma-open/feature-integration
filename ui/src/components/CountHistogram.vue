<script setup lang="ts">
import { PlChartHistogram } from "@platforma-sdk/ui-vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { valuesFromBins } from "./binValues";

// One binned count distribution, drawn from precomputed bins.
//
// `PlChartHistogram` rather than `GraphMaker`: this is a plot, not a chart builder. A reader here is asked
// one question -- do these two humps stand apart -- and every control a builder offers is a way to stop
// answering it. It also takes a `threshold`, which is how a declared gate draws its marker; a builder has
// no marker mechanism at all.
//
// Two forms, picked by `scale`. `log-bins` takes the bins as given and draws a symlog axis with decade
// ticks. `basic` is the only linear axis the component has, and it bins raw values itself, so linear
// callers hand it values reconstructed from the same bins.
//
// `totalWidth` MUST be passed. The component draws an SVG at a fixed width and measures nothing, so its
// 674px default overflows any narrower container and paints over whatever sits beside it. Measured here
// with a ResizeObserver, so a grid cell of any width holds its own chart.
const props = defineProps<{
  /** Bin boundaries, `weights.length + 1` of them, shared across every plot of a run. */
  edges: number[];
  /** Cells per bin, in edge order. */
  weights: number[];
  /**
   * The x axis. `log` suits counts per cell, which span orders of magnitude: on a linear axis the ambient
   * population occupies one bar with emptiness above it. `linear` suits a quantity read against a number
   * typed in the same units -- a specificity score, a reference reading -- which a log axis puts where the
   * reader cannot find it.
   */
  scale?: "log" | "linear";
  title?: string;
  /** Drawn as a marker. Undefined draws none, which is the statement that no gate is declared. */
  threshold?: number;
  /**
   * Zeroes every margin, which drops the axes, the axis labels and the title. For a thumbnail whose one
   * question is whether two humps stand apart. Without it the fixed 85px left and 40px bottom margins take
   * most of a small panel, leaving a plot narrower than its own axis gutter.
   */
  compact?: boolean;
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

// `drawThreshold` returns on a falsy threshold, so a declared gate of exactly 0 would draw no marker and
// read as no gate at all -- the one distinction this marker carries. The smallest positive double sits at
// the same pixel as 0 on either scale.
const threshold = computed(() => (props.threshold === 0 ? Number.MIN_VALUE : props.threshold));

// `basic` bins the values it is handed over `nBins` thresholds of its own. More of them than the source
// carries would draw the reconstruction's interpolation as if it were data, so this is the source's own bin
// count at most.
const LINEAR_BIN_TARGET = 20;

const settings = computed(() => {
  const common = {
    ...(threshold.value === undefined ? {} : { threshold: threshold.value }),
    ...(props.title === undefined ? {} : { title: props.title }),
    xAxisLabel: props.xAxisLabel ?? "Counts per cell",
    yAxisLabel: props.yAxisLabel ?? "Cells",
    totalWidth: width.value,
    totalHeight: props.totalHeight,
    compact: props.compact,
  };

  if (props.scale === "linear") {
    return {
      ...common,
      type: "basic" as const,
      numbers: valuesFromBins(props.edges, props.weights),
      nBins: Math.min(LINEAR_BIN_TARGET, props.weights.length),
    };
  }

  return {
    ...common,
    type: "log-bins" as const,
    // A bin's own bounds travel with its weight, since this form bins nothing itself.
    bins: props.weights.map((weight, i) => ({
      from: props.edges[i]!,
      to: props.edges[i + 1]!,
      weight,
    })),
  };
});
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
