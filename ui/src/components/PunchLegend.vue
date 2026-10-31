<script setup lang="ts">
import { PlTooltip } from "@platforma-sdk/ui-vue";
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

// A phrase inside an entry's `meaning`, shown with its own definition as a tooltip.
type Term = { text: string; definition: string };
type Entry = { glyph: PunchGlyph | "none"; label: string; meaning: string; term?: Term };

// Both card verdicts rest on this term, so it is defined where it is used: hovering it shows what makes a
// cell count.
const COULD_ANSWER: Term = {
  text: "cells that could answer",
  definition:
    "A cell that could answer is a cell that belongs to a given clonotype, comes from a sample whose reads " +
    "contain the antigen's barcode, is not above the sticky cell limit, and has a background to compare " +
    "against. A cell with no counts for the antigen still answers, voting not bound.",
};

const SET_ENTRIES: Entry[] = [
  {
    glyph: "bound",
    label: "Bound",
    meaning: "enough of the cells that could answer agreed on binding",
    term: COULD_ANSWER,
  },
  {
    glyph: "not-bound",
    label: "Not bound",
    meaning: "enough of the cells that could answer agreed on no binding",
    term: COULD_ANSWER,
  },
  {
    glyph: "unreliable",
    label: "Unreliable",
    meaning: "this antigen was in the panel, but the cells could not settle it. Hover for why.",
  },
  {
    glyph: "none",
    label: "Never asked",
    meaning: "this antigen was not in the panel for these samples",
  },
];

const CELL_ENTRIES: Entry[] = [
  {
    glyph: "bound",
    label: "Bound",
    meaning: "this cell shows binding",
  },
  {
    glyph: "not-bound",
    label: "Not bound",
    // The zero count is said explicitly, because it is the one thing about this face a reader would otherwise
    // get wrong: a cell that returned no count for an antigen it WAS asked about reads here, never blank. A
    // zero count is a reading, and the same cell votes that way in its clonotype's verdict.
    meaning: "no binding, including where the count came back as zero",
  },
  {
    glyph: "unreliable",
    label: "Unreliable",
    meaning: "this cell's reading was not counted. Hover for why.",
  },
  {
    glyph: "none",
    label: "Never asked",
    meaning: "this antigen was not in the panel for this sample",
  },
];

// An entry with its meaning split around its term, once per entry, so the template reads plain fields.
// `parts` is absent where the entry defines nothing, or where the term is not in its meaning.
type Row = Entry & { parts?: { before: string; term: Term; after: string } };

const withParts = (e: Entry): Row => {
  if (e.term === undefined) return e;
  const at = e.meaning.indexOf(e.term.text);
  if (at < 0) return e;
  return {
    ...e,
    parts: {
      before: e.meaning.slice(0, at),
      term: e.term,
      after: e.meaning.slice(at + e.term.text.length),
    },
  };
};

const ENTRIES = computed<Row[]>(() =>
  (props.variant === "cell" ? CELL_ENTRIES : SET_ENTRIES).map(withParts),
);

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

// A dotted underline is the conventional mark of a term that explains itself on hover.
const termStyle: CSSProperties = {
  textDecoration: "underline dotted",
  textUnderlineOffset: "3px",
  cursor: "help",
};
</script>

<template>
  <div :style="listStyle">
    <div v-for="e in ENTRIES" :key="e.label" :style="rowStyle">
      <span :style="swatch(e.glyph)" />
      <!-- PlTooltip anchors to its slot's first ELEMENT, so the term is wrapped in a span: a bare text node
           leaves nothing to anchor to and the bubble drops to the bottom of the page. -->
      <span v-if="e.parts"
        ><b>{{ e.label }}</b
        >: {{ e.parts.before
        }}<PlTooltip element="span" position="bottom"
          ><span :style="termStyle">{{ e.parts.term.text }}</span
          ><template #tooltip>{{ e.parts.term.definition }}</template></PlTooltip
        >{{ e.parts.after }}</span
      >
      <span v-else
        ><b>{{ e.label }}</b
        >: {{ e.meaning }}</span
      >
    </div>
  </div>
</template>
