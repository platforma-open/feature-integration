<script setup lang="ts">
import type { TagCountBins } from "@platforma-open/milaboratories.feature-integration.model";
import { computed } from "vue";
import type { HistogramPanel } from "./HistogramGrid.vue";
import HistogramGrid from "./HistogramGrid.vue";

// The fitted background, as one panel per (sample, tag), which is the grain the fit runs at. Aggregating
// to the tag would hide a reagent that separated in one sample and not in another.
//
// Its only caller scopes it to one sample, so each panel is titled with just its reagent and the sample
// is named once above the grid. Left unscoped it draws every (sample, tag) pair instead and puts the
// sample back in each title. That mode still works; nothing uses it today.
//
// This is the ADAPTER: it decides what a panel is and what it says. The grid, the enlarge affordance and
// the modal are `HistogramGrid`.
//
// The vertical marker is this pair's own bound count: the count from which its fit starts calling a cell
// bound. Each (sample, tag) pair is fitted on its own cells, so every panel needs its own line.
//
// A panel carries its title, its plot, and the fit's own three numbers. No separated / does-not-separate
// label: no criterion for it exists. This panel is the substitute for the check nobody has built, so
// withholding the fit leaves the rung with no safeguard at all.
//
// The edges come from `log1p_bin_edges` and the axis is symlog, which for these values is log1p, so equal
// steps in the edges draw as equal widths on screen. That was not always true: with integer count edges
// `[0, 1)` covered 0.301 of a decade and `[4, 5)` covered 0.079, so a raw count made a wide bar look
// taller than a narrow one holding the same density. Each bar had to be divided by its own width, and
// getting that division wrong once flattened a real signal hump out of sight. Equal widths remove it.
const props = defineProps<{
  bins: TagCountBins;
  /** Sample id -> the label a reader knows it by. A sample with no label renders as its own id. */
  sampleLabels: Record<string, string>;
  /** Show only this sample, which is how the page uses it. */
  onlySample?: string;
  /** Order the barcodes by this list rather than by label. Anything missing from it follows, by label. */
  tagOrder?: string[];
}>();

type Fit = NonNullable<TagCountBins["fitsBySample"][string]>[string] | undefined;

// Counts span orders of magnitude across a panel, so a fixed number of decimals prints either noise or
// nothing. Three SIGNIFICANT figures reads the same at 0.00049 and at 930.
const fmt = (value: number) => value.toLocaleString("en-US", { maximumSignificantDigits: 3 });

// The bound-count line, which has four possible readings.
const boundLine = (fit: Fit) => {
  if (fit === undefined) return "no fit for this barcode";
  // Three different facts, so three different sentences. A number is the fit's answer. `null` means the
  // fit ran and no count reached the line. `undefined` means the run predates this field and never
  // looked -- reporting that as "no count reaches it" would state a finding no run produced.
  if (typeof fit.boundAtCount === "number") {
    return `bound from ≥${fit.boundAtCount.toLocaleString("en-US")} UMI`;
  }
  if (fit.boundAtCount === null) return "no UMI count reaches the bound probability";
  return "this run recorded no bound threshold";
};

// Sorted by tag then sample, so the grid's reading order is stable across runs: `Record` iteration order
// follows insertion, and the JSON's own key order is whatever the writer produced.
//
// Tags sort on the NAME a panel is titled with, not on the barcode behind it, so the grid reads in the
// order it prints. A tag the panel named nowhere reads as its own barcode and sorts under it.
const panels = computed<HistogramPanel[]>(() => {
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
  const out: HistogramPanel[] = [];
  for (const tag of ordered) {
    for (const sample of samples) {
      const weights = props.bins.bySample[sample]?.[tag];
      // A tag absent from this sample's panel has nothing to draw.
      if (weights === undefined) continue;
      const fit: Fit = props.bins.fitsBySample?.[sample]?.[tag];
      out.push({
        key: `${tag} ${sample}`,
        title:
          props.onlySample === undefined
            ? `${tagName(tag)} · ${props.sampleLabels[sample] ?? sample}`
            : tagName(tag),
        weights,
        // `?? undefined` rather than passing the null through: `threshold` draws no marker only when it
        // is undefined, and a pair whose fit reaches no such count must draw none.
        threshold: fit?.boundAtCount ?? undefined,
        caption: boundLine(fit),
        detail:
          fit === undefined
            ? undefined
            : [
                `Mean UMI per cell — background ${fmt(fit.backgroundMean)} · signal ${fmt(fit.signalMean)}`,
                `Fit puts ${(fit.backgroundWeight * 100).toFixed(0)}% of cells in the background component`,
              ],
      });
    }
  }
  return out;
});
</script>

<template>
  <HistogramGrid :edges="bins.edges" :panels="panels" x-axis-label="UMI count for this barcode" />
</template>
