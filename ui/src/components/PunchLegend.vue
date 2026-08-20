<script setup lang="ts">
import type { CSSProperties } from "vue";
import { PUNCH_DIAMETER_PX, PUNCH_PAINT, type PunchGlyph } from "./punchMarks";

// What the card's marks mean, stated once above it.
//
// The swatches are drawn from PUNCH_PAINT, the same map the cells paint from, so a legend cannot drift
// from the card it explains — the failure mode of every hand-drawn legend, and one nobody notices because
// both halves look deliberate.
//
// Size means nothing on the card any more -- every mark is one diameter -- so the swatches take that same
// diameter from the same constant. While the card sized its dots by evidence, a swatch had to pick one
// value and was then read as the meaning rather than as an example, which is why this legend used to be
// deliberately smaller than the thing it explains. That reason is gone, and matching is now the honest
// drawing. The counts still reach the reader, in the per-cell tooltip and in the clonotype expansion.
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
  width: `${PUNCH_DIAMETER_PX}px`,
  height: `${PUNCH_DIAMETER_PX}px`,
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
