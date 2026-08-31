<script setup lang="ts">
import type { TagCountBins } from "@platforma-open/milaboratories.feature-integration.model";
import { PlDialogModal } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import CountHistogram from "./CountHistogram.vue";

// The fitted background, as a grid of small multiples: one panel per (sample, tag), which is the grain the
// fit runs at. Aggregating to the tag would hide a reagent that separated in one sample and not in another.
//
// Ordered by TAG first, so one reagent's samples sit side by side and the row is the comparison. A panel is
// titled `tag · sample` in the same order.
//
// A grid rather than a selector, with any panel enlargeable on click: the judgement asked for reads at
// thumbnail size, and behind a selector nobody looks at all of them.
//
// No marker is drawn. The threshold slot means "the declared gate" on the reference-reading plot and "the
// bound cutoff" on the scores plot, so a third meaning here would make one marker say three things.
//
// A panel carries its title, its plot, its cell count, and the fit's own three numbers. No separated /
// does-not-separate label: no criterion for it exists. This panel is the substitute for the check nobody
// has built, so withholding the fit leaves the rung with no safeguard at all.
//
// Bars are DENSITY, cells per count. Bin width in counts rises across the edge set, so weight makes a
// wide bin stand above a narrow one at equal density and puts a hump where the data holds none. The
// panel's own question is whether two humps stand apart, so a hump the bins invented is the one error
// this surface cannot carry.
//
// Density costs the hover readout, which reports the number the chart was handed. The cell count sits in
// the caption instead.
const props = defineProps<{
  bins: TagCountBins;
  /** Sample id -> the label a reader knows it by. A sample with no label renders as its own id. */
  sampleLabels: Record<string, string>;
}>();

type Panel = {
  key: string;
  title: string;
  weights: number[];
  /** The fit's own numbers, absent where nothing was fitted for this pair. */
  fit?: { backgroundMean: number; signalMean: number; backgroundWeight: number };
};

// Sorted by tag then sample, so the grid's reading order is stable across runs: `Record` iteration order
// follows insertion, and the JSON's own key order is whatever the writer produced.
//
// Tags sort on the NAME a panel is titled with, not on the barcode behind it, so the grid reads in the
// order it prints. A tag the panel named nowhere reads as its own barcode and sorts under it.
const panels = computed<Panel[]>(() => {
  const samples = Object.keys(props.bins.bySample).sort();
  const tags = new Set<string>();
  for (const sample of samples) {
    for (const tag of Object.keys(props.bins.bySample[sample] ?? {})) tags.add(tag);
  }
  const tagName = (tag: string) => props.bins.tagLabels?.[tag] ?? tag;
  const out: Panel[] = [];
  for (const tag of [...tags].sort((a, b) => tagName(a).localeCompare(tagName(b)))) {
    for (const sample of samples) {
      const weights = props.bins.bySample[sample]?.[tag];
      // A tag absent from this sample's panel has nothing to draw.
      if (weights === undefined) continue;
      out.push({
        key: `${tag} ${sample}`,
        title: `${tagName(tag)} · ${props.sampleLabels[sample] ?? sample}`,
        weights,
        fit: props.bins.fitsBySample?.[sample]?.[tag],
      });
    }
  }
  return out;
});

// Counts span orders of magnitude across a panel, so a fixed number of decimals prints either noise or
// nothing. Three significant figures reads the same at 0.33 and at 930.
const fmt = (value: number) => Number(value.toPrecision(3)).toLocaleString();

// Cells holding any count of this tag in this sample. The bins are taken over the cell list, so the sum
// is that population and nothing wider.
const cellsIn = (panel: Panel) => panel.weights.reduce((total, weight) => total + weight, 0);

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
      <CountHistogram
        :edges="bins.edges"
        :weights="panel.weights"
        :total-height="140"
        density
        compact
      />
      <span :class="$style.fit">
        {{ cellsIn(panel).toLocaleString() }} cells
        <template v-if="panel.fit">
          · bg {{ fmt(panel.fit.backgroundMean) }} · signal {{ fmt(panel.fit.signalMean) }} ·
          {{ (panel.fit.backgroundWeight * 100).toFixed(0) }}% background
        </template>
        <template v-else> · no fit for this pair</template>
      </span>
    </button>
  </div>

  <PlDialogModal v-model="isOpen" width="720px">
    <template #title>{{ enlarged?.title }}</template>
    <template v-if="enlarged">
      <CountHistogram :edges="bins.edges" :weights="enlarged.weights" :total-height="420" density />
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
</style>
