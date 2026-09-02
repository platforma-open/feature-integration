<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed } from "vue";
import { PUNCH_LEGEND_DIAMETER_PX, PUNCH_PAINT, type PunchGlyph } from "./punchMarks";

// What the card's marks mean, stated once above it.
//
// The swatches are drawn from PUNCH_PAINT, the same map the cells paint from, so a legend cannot drift from
// the card it explains. They are smaller than the card's marks, which is harmless: a diameter carries no
// meaning on the card.
//
// The four glyphs are the same on both faces, but what a glyph MEANS is not: a mark on the card is a verdict
// a majority of cells produced, and a mark on the by-cell face is one cell's own reading. Both wordings live
// here, in the one component, so a glyph can never be paired with the wrong swatch.
const props = withDefaults(defineProps<{ variant?: "set" | "cell" }>(), { variant: "set" });

type Entry = { glyph: PunchGlyph | "none"; label: string; meaning: string };

const SET_ENTRIES: Entry[] = [
  {
    glyph: "bound",
    label: "Bound",
    meaning: "a majority of the cells that answered read it as bound",
  },
  {
    glyph: "not-bound",
    label: "Not bound",
    meaning: "the cells that answered read it as not bound",
  },
  {
    glyph: "unreliable",
    label: "Unreliable",
    meaning: "asked, and the readings could not settle it. Hover for which of the five ways",
  },
  {
    glyph: "none",
    label: "Never asked",
    meaning: "no sample holding these cells declared this antigen",
  },
];

const CELL_ENTRIES: Entry[] = [
  {
    glyph: "bound",
    label: "Bound",
    meaning: "this cell read the antigen as bound",
  },
  {
    glyph: "not-bound",
    label: "Not bound",
    // Said explicitly, because it is the one thing about this face a reader would otherwise get wrong: a cell
    // that returned no count for an antigen it WAS asked about reads here, never blank. A zero count is a
    // reading, and the same cell votes that way in its clonotype's verdict.
    meaning: "this cell read it as not bound, a returned count of zero included",
  },
  {
    glyph: "unreliable",
    label: "Unreliable",
    meaning: "this cell could not be compared at all, so it cast no vote. Hover for why",
  },
  {
    glyph: "none",
    label: "Never asked",
    meaning: "no sample holding this cell declared this antigen",
  },
];

const ENTRIES = computed<Entry[]>(() => (props.variant === "cell" ? CELL_ENTRIES : SET_ENTRIES));

const swatch = (glyph: PunchGlyph | "none"): CSSProperties => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: `${PUNCH_LEGEND_DIAMETER_PX}px`,
  height: `${PUNCH_LEGEND_DIAMETER_PX}px`,
  flex: "0 0 auto",
  // "Never asked" draws nothing on the card, so its swatch is an empty well rather than a mark. A legend
  // entry with no glyph beside it reads as a missing image.
  ...(glyph === "none" ? { border: "1px dashed #cfcfcf" } : PUNCH_PAINT[glyph]),
});

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "8px 24px",
  padding: "8px 12px",
};
</script>

<template>
  <div :style="listStyle">
    <div v-for="e in ENTRIES" :key="e.label" :style="rowStyle">
      <span :style="swatch(e.glyph)" />
      <span
        ><b>{{ e.label }}</b>: {{ e.meaning }}</span
      >
    </div>
  </div>
</template>
