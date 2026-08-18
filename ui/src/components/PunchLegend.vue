<script setup lang="ts">
import type { CSSProperties } from "vue";
import { PUNCH_PAINT, type PunchGlyph } from "./punchMarks";

// What the card's marks mean, stated once above it.
//
// The swatches are drawn from PUNCH_PAINT, the same map the cells paint from, so a legend cannot drift
// from the card it explains — the failure mode of every hand-drawn legend, and one nobody notices because
// both halves look deliberate.
//
// Size is deliberately NOT in the legend. On the card a dot's diameter carries how many of a clonotype's
// cells answered, which is a continuous quantity; a swatch would have to pick one value and would then be
// read as the meaning rather than as an example. The per-cell tooltip carries the counts in words instead.
const ENTRIES: { glyph: PunchGlyph | "none"; label: string; meaning: string }[] = [
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
    meaning: "asked, and the readings could not settle it — hover for which of the seven ways",
  },
  {
    glyph: "none",
    label: "Never asked",
    meaning: "no sample holding these cells declared this antigen",
  },
];

const swatch = (glyph: PunchGlyph | "none"): CSSProperties => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: "11px",
  height: "11px",
  flex: "0 0 auto",
  // "Never asked" draws nothing on the card, so its swatch is an empty well rather than a mark — a legend
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
        ><b>{{ e.label }}</b> — {{ e.meaning }}</span
      >
    </div>
  </div>
</template>
