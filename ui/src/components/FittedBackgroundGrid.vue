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
// A grid rather than a selector, with any panel enlargeable on click: the judgement asked for -- do these
// two humps stand apart -- reads at thumbnail size, and behind a selector nobody looks at all of them.
//
// No marker is drawn. The threshold slot means "the declared gate" on the reference-reading plot and "the
// bound cutoff" on the scores plot, so a third meaning here would make one marker say three things.
//
// A panel carries its title and its plot, and no text below. A separated / does-not-separate label belongs
// here, and no criterion for it exists yet: the fitter's only failure is NO_FIT, which is a fit that could
// not be computed rather than one that computed and split a single population.
const props = defineProps<{
  bins: TagCountBins;
  /** Sample id -> the label a reader knows it by. A sample with no label renders as its own id. */
  sampleLabels: Record<string, string>;
}>();

type Panel = {
  key: string;
  title: string;
  weights: number[];
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
      });
    }
  }
  return out;
});

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
         without a pointer, and a thumbnail nobody can tell is clickable is a thumbnail nobody clicks. -->
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
      <CountHistogram :edges="bins.edges" :weights="panel.weights" :total-height="140" compact />
    </button>
  </div>

  <PlDialogModal v-model="isOpen" width="720px">
    <template #title>{{ enlarged?.title }}</template>
    <template v-if="enlarged">
      <CountHistogram :edges="bins.edges" :weights="enlarged.weights" :total-height="420" />
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
</style>
