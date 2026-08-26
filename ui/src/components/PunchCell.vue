<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed, ref } from "vue";
import type { Punch, VerdictState } from "./punchMarks";
import { PUNCH_DIAMETER_PX, PUNCH_PAINT, parsePunch } from "./punchMarks";

// One punch:
//
//   bound        a GREEN dot
//   not bound    a RED dot
//   unreliable   a GREY dot
//   never asked  nothing at all
//
// This departs from `assets/punch-card.svg`, which fills a dot for bound, leaves the cell blank for not
// bound, and rings it for never-offered. Operator decision, taken after reading a live card: a clonotype
// binds one antigen out of the panel, so the grid is ~92% negative before anything goes wrong and
// blank-for-negative is unreadable at that density. Blank is reserved for the one state that has no answer
// in it. The three states that ARE answers read as dots of one family.
//
// EVERY mark is one size, and a large one. Never size a mark by the share of a clonotype's cells that
// answered: the scientist is handed the two counts in this component's own tooltip and as columns in the
// clonotype expansion, and a grid of dots at eight diameters reads as noise at panel density.
//
// The cell's value carries everything needed to explain itself, in ONE value because a grid pairs a cell
// with another column's cell only by position and no import guarantees that. The format and its decoder
// live in `punchMarks.ts`, shared with the clonotype expansion. Anything that does not parse is drawn as
// its own mark rather than guessed at.
//
// There is deliberately no score and no binding level here. The tooltip explains a verdict by what it RESTS
// on and never by how strongly anything bound.
const props = defineProps<{
  params: { value: unknown; antigen?: string; mergedNote?: string; showCouldAnswer?: boolean };
}>();

const punch = computed<Punch>(() => parsePunch(props.params.value));

type Glyph = "bound" | "not-bound" | "unreliable" | "none" | "unknown";
const GLYPH_OF: Record<VerdictState, Glyph> = {
  bound: "bound",
  "not bound": "not-bound",
  unreliable: "unreliable",
  "never asked": "none",
};
const glyph = computed<Glyph>(() =>
  punch.value.kind === "read" ? GLYPH_OF[punch.value.state] : "unknown",
);

// Painted from the shared map so the legend above the card cannot describe a colour the card does not draw.
// See punchMarks.ts for why these are inline values rather than CSS classes.
const punchStyle = computed<CSSProperties>(() => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: `${PUNCH_DIAMETER_PX}px`,
  height: `${PUNCH_DIAMETER_PX}px`,
  ...(glyph.value === "none" ? {} : PUNCH_PAINT[glyph.value]),
}));

// Fills the cell, so the hover target is the whole cell rather than the dot. The measured wrapper was 130px
// inside a 161px cell, which leaves a strip on each side where a reader aiming at the column gets nothing.
const cellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  height: "100%",
};

// Why this mark is this colour, in the order a reader asks it: what the verdict is, what it rests on, and
// -- where the verdict is unsettled -- which of the six ways it failed to settle. The reason tokens are
// machine values (`no-comparator`, `tie`, ...), so each is expanded here rather than shown raw.
//
// Each line is capitalised at the source rather than by a transform over `lines`. A blanket transform would
// also capitalise the antigen name, which is panel data.
const WHY_UNSETTLED: Record<string, string> = {
  "never-offered": "No sample holding these cells declared this antigen",
  "no-comparator": "No baseline reading existed for these cells",
  "all-cells-gated": "Every cell was set aside by the admissibility gate",
  tie: "The cells split evenly, so no majority settled it",
  "below-agreement-floor": "The cells agreed less than the run required",
  "too-few-voters": "Fewer cells answered than the run required",
};

const EXPLANATION: Record<VerdictState, string> = {
  bound:
    "Green: a majority of the cells that answered read this antigen as bound against the baseline that served",
  "not bound":
    "Red: the cells that answered read this antigen as not bound against the baseline that served",
  unreliable: "Grey: the experiment asked, and the readings could not settle it",
  "never asked":
    "No mark: this clonotype's cells were never offered this antigen, so there is nothing to answer",
};

const lines = computed<string[]>(() => {
  const p = punch.value;
  if (p.kind !== "read") return ["No readable verdict for this clonotype at this identity"];

  // The antigen first. The header row scrolls out of view on a long grid, so the panel must say which column
  // this dot belongs to before it says anything about the verdict.
  const out = props.params.antigen === undefined ? [] : [props.params.antigen];
  out.push(p.state.toUpperCase(), EXPLANATION[p.state]);
  if (p.state !== "never asked") {
    // How many COULD answer is shown only where the run carried panels that differ, which is where it
    // varies. Under one panel it is the clonotype's own cell count at every identity, already beside its
    // name in the grid.
    out.push(
      props.params.showCouldAnswer
        ? `${p.answered} of ${p.couldAnswer} cells answered`
        : `${p.answered} cells answered`,
    );
    if (p.agreement !== undefined) out.push(`${Math.round(p.agreement * 100)}% of them agreed`);
  }
  if (p.reason !== undefined) out.push(WHY_UNSETTLED[p.reason] ?? p.reason);
  // Last, because it is about the COLUMN rather than this verdict: why this identity is one merged reagent
  // while its neighbours are single antigens.
  if (props.params.mergedNote !== undefined) out.push(props.params.mergedNote);
  return out;
});

// The panel is rendered by this component and teleported to <body>, rather than left to the browser's
// native `title`. A `title` inside a virtualised grid cell did not fire at all in the app. It also cannot be
// styled or laid out -- a five-line explanation arrives as one run-together string -- and it waits for the
// OS hover delay, which for a grid a reader is scanning is long enough to feel absent.
//
// Teleported because the cell clips: ag-grid gives each cell `overflow: hidden`, so a panel rendered in
// place is cut to a 40px row.
const hover = ref<{ x: number; y: number } | null>(null);

// Positioned beside the cursor and flipped when it would leave the window, so a punch in the last column or
// the bottom row still shows its whole explanation.
const panelStyle = computed<CSSProperties>(() => {
  const at = hover.value;
  if (at === null) return { display: "none" };
  const W = 320;
  const flipX = at.x + W + 24 > window.innerWidth;
  const flipY = at.y + 160 > window.innerHeight;
  return {
    position: "fixed",
    left: `${flipX ? at.x - W - 16 : at.x + 16}px`,
    top: `${flipY ? Math.max(8, at.y - 150) : at.y + 16}px`,
    width: `${W}px`,
    zIndex: "2000",
    pointerEvents: "none",
    background: "#1f2329",
    color: "#f5f6f7",
    borderRadius: "6px",
    padding: "8px 10px",
    fontSize: "12px",
    lineHeight: "1.45",
    boxShadow: "0 4px 16px rgba(0, 0, 0, 0.28)",
  };
});

function show(e: MouseEvent) {
  hover.value = { x: e.clientX, y: e.clientY };
}
function hide() {
  hover.value = null;
}
</script>

<template>
  <div :style="cellStyle" @mouseenter="show" @mousemove="show" @mouseleave="hide">
    <span v-if="glyph !== 'none'" :style="punchStyle" />
    <Teleport to="body">
      <div v-if="hover" :style="panelStyle">
        <div v-for="(line, i) in lines" :key="i" :style="i === 0 ? { fontWeight: 600 } : {}">
          {{ line }}
        </div>
      </div>
    </Teleport>
  </div>
</template>
