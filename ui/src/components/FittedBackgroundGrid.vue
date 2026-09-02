<script setup lang="ts">
import type { TagCountBins } from "@platforma-open/milaboratories.feature-integration.model";
import { PlDialogModal } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import CountHistogram from "./CountHistogram.vue";

// The fitted background, as a grid of small multiples: one panel per (sample, tag), which is the grain the
// fit runs at. Aggregating to the tag would hide a reagent that separated in one sample and not in another.
//
// Its only caller scopes it to one sample, so each panel is titled with just its reagent and the sample
// is named once above the grid. Left unscoped it draws every (sample, tag) pair instead and puts the
// sample back in each title. That mode still works; nothing uses it today.
//
// A grid rather than a selector, with any panel enlargeable on click: the judgement asked for reads at
// thumbnail size, and behind a selector nobody looks at all of them.
//
// The vertical marker is this pair's own bound count: the count from which its fit starts calling a cell
// bound. Other plots use the same slot for the same purpose -- a line to judge the distribution against.
// Each (sample, tag) pair is fitted on its own cells, so every panel needs its own line.
//
// A panel carries its title, its plot, its cell count, and the fit's own three numbers. No separated /
// does-not-separate label: no criterion for it exists. This panel is the substitute for the check nobody
// has built, so withholding the fit leaves the rung with no safeguard at all.
//
// Bar height is a plain cell count, and that is safe because every bar is the same width. The edges come
// from `log1p_bin_edges` and the axis is symlog, which for these values is log1p, so equal steps in the
// edges draw as equal widths on screen.
//
// That was not always true. With integer count edges the bars had different widths -- `[0, 1)` covered
// 0.301 of a decade and `[4, 5)` covered 0.079 -- so a raw count made a wide bar look taller than a
// narrow one holding the same density. Each bar had to be divided by its own width, and getting that
// division wrong once flattened a real signal hump out of sight. Equal widths remove the problem.
const props = defineProps<{
  bins: TagCountBins;
  /** Sample id -> the label a reader knows it by. A sample with no label renders as its own id. */
  sampleLabels: Record<string, string>;
  /**
   * Show only this sample, which is how the page uses it.
   */
  onlySample?: string;
  /**
   * Order the barcodes by this list rather than by label. Anything missing from it follows, by label.
   */
  tagOrder?: string[];
}>();

type Panel = {
  key: string;
  title: string;
  weights: number[];
  /** The fit's own numbers, absent where nothing was fitted for this pair. */
  fit?: {
    backgroundMean: number;
    signalMean: number;
    backgroundWeight: number;
    boundAtCount?: number | null;
  };
};

// Sorted by tag then sample, so the grid's reading order is stable across runs: `Record` iteration order
// follows insertion, and the JSON's own key order is whatever the writer produced.
//
// Tags sort on the NAME a panel is titled with, not on the barcode behind it, so the grid reads in the
// order it prints. A tag the panel named nowhere reads as its own barcode and sorts under it.
const panels = computed<Panel[]>(() => {
  const all = Object.keys(props.bins.bySample).sort();
  const samples = props.onlySample === undefined ? all : all.filter((s) => s === props.onlySample);
  const tags = new Set<string>();
  for (const sample of samples) {
    for (const tag of Object.keys(props.bins.bySample[sample] ?? {})) tags.add(tag);
  }
  const tagName = (tag: string) => props.bins.tagLabels?.[tag] ?? tag;
  // Declared order where the caller gave one, label order for whatever it does not mention. A declared
  // list from one run and bins from another need not agree, so neither side is assumed to cover the other.
  const declared = new Map((props.tagOrder ?? []).map((tag, i) => [tag, i]));
  const ordered = [...tags].sort((a, b) => {
    const ia = declared.get(a);
    const ib = declared.get(b);
    if (ia !== undefined && ib !== undefined) return ia - ib;
    if (ia !== undefined) return -1;
    if (ib !== undefined) return 1;
    return tagName(a).localeCompare(tagName(b));
  });
  const out: Panel[] = [];
  for (const tag of ordered) {
    for (const sample of samples) {
      const weights = props.bins.bySample[sample]?.[tag];
      // A tag absent from this sample's panel has nothing to draw.
      if (weights === undefined) continue;
      out.push({
        key: `${tag} ${sample}`,
        title:
          props.onlySample === undefined
            ? `${tagName(tag)} · ${props.sampleLabels[sample] ?? sample}`
            : tagName(tag),
        weights,
        fit: props.bins.fitsBySample?.[sample]?.[tag],
      });
    }
  }
  return out;
});

// Counts span orders of magnitude across a panel, so a fixed number of decimals prints either noise or
// nothing. Three SIGNIFICANT figures reads the same at 0.00049 and at 930.
//
// Three SIGNIFICANT digits, via `maximumSignificantDigits`.
const fmt = (value: number) => value.toLocaleString("en-US", { maximumSignificantDigits: 3 });

// The bound-count line, which has four possible readings. Written once here because two places show it:
// the thumbnail shows it alone, the enlarged panel shows it under the fit's numbers.
const boundLine = (fit: Panel["fit"]) => {
  if (fit === undefined) return "no fit for this barcode";
  // Three different facts, so three different sentences. A number is the fit's answer. `null` means the
  // fit ran and no count reached the line. `undefined` means the run predates this field and never
  // looked -- reporting that as "no count reaches it" would state a finding no run produced.
  if (typeof fit.boundAtCount === "number") {
    return `bound from \u2265${fit.boundAtCount.toLocaleString("en-US")} UMI`;
  }
  if (fit.boundAtCount === null) return "no UMI count reaches the bound probability";
  return "this run recorded no bound threshold";
};

const enlarged = ref<Panel | undefined>(undefined);
const isOpen = computed({
  get: () => enlarged.value !== undefined,
  set: (open: boolean) => {
    if (!open) enlarged.value = undefined;
  },
});
</script>

<template>
  <div :class="$style.grid">
    <!-- Enlarging is a button rather than a click handler on the panel: the affordance has to be reachable
             without a pointer. -->
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
      <!-- `compact`: bars only. The thumbnail is scanned for whether two humps stand apart, and the
                 enlarged panel below is where a value gets read off an axis. -->
      <!-- The marker is the rung's own cutoff, carried into counts by the fit. `?? undefined` rather
                 than passing the null through: `threshold` draws no marker only when it is undefined, and
                 a pair whose fit reaches no such count must draw none. -->
      <CountHistogram
        :edges="bins.edges"
        :weights="panel.weights"
        :threshold="panel.fit?.boundAtCount ?? undefined"
        :total-height="140"
        x-axis-label="UMI count for this barcode"
        compact
      />
      <span :class="$style.fit">{{ boundLine(panel.fit) }}</span>
    </button>
  </div>

  <PlDialogModal v-model="isOpen" width="720px">
    <template #title>{{ enlarged?.title }}</template>
    <template v-if="enlarged">
      <CountHistogram
        :edges="bins.edges"
        :weights="enlarged.weights"
        :threshold="enlarged.fit?.boundAtCount ?? undefined"
        :total-height="420"
        x-axis-label="UMI count for this barcode"
      />
      <div v-if="enlarged.fit" :class="$style.enlargedFit">
        <div>
          Mean UMI per cell — background {{ fmt(enlarged.fit.backgroundMean) }} · signal
          {{ fmt(enlarged.fit.signalMean) }}
        </div>
        <div>
          Fit puts {{ (enlarged.fit.backgroundWeight * 100).toFixed(0) }}% of cells in the
          background component
        </div>
        <div>{{ boundLine(enlarged.fit) }}</div>
      </div>
      <div v-else :class="$style.enlargedFit">{{ boundLine(enlarged.fit) }}</div>
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
  gap: 4px;
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

.header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-txt-01);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enlarge {
  font-size: 13px;
  color: var(--color-txt-03);
  opacity: 0;
  transition: opacity 0.1s;
}

.fit {
  font-size: 12px;
  color: var(--color-txt-03);
  font-variant-numeric: tabular-nums;
}

/* The enlarged panel's readout: one statement per line, because these are read rather than scanned and
   three facts on one line ran together. Tabular figures so the two means line up under each other. */
.enlargedFit {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--color-txt-02);
  font-variant-numeric: tabular-nums;
}
</style>
