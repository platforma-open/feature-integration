<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed, ref } from "vue";
import type { Punch, VerdictState } from "./punchMarks";
import { PUNCH_DIAMETER_PX, PUNCH_PAINT, parsePunch } from "./punchMarks";

// One punch:
//
//   bound        a filled BLUE dot
//   not bound    a hollow BLUE ring
//   unreliable   a filled GREY dot
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
  params: {
    value: unknown;
    antigen?: string;
    mergedNote?: string;
    showAsked?: boolean;
    minAgreement?: number;
    minVoters?: number;
  };
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

// The five ways a reading fails to settle, plus never-offered, which belongs to *never asked* rather than to
// an unsettled reading. The tokens are machine values (`no-comparator`, `tie`, ...), so each is expanded
// here rather than shown raw. A token with no case falls through to itself, so an unknown one is shown
// rather than swallowed.
//
// A function rather than a map because two of them quote the run's cutoffs. Each line is capitalised at the
// source rather than by a transform over `lines`: a blanket transform would also capitalise the antigen
// name, which is panel data.
const whyUnsettled = (reason: string): string => {
  const voters = props.params.minVoters;
  const agreement = props.params.minAgreement;
  switch (reason) {
    // Reached two ways, so it names both. `_dominant_reason` returns ALL_CELLS_GATED only where EVERY
    // asked cell was gated; a set mixing gated cells with cells lacking a comparator falls through to
    // here, and claiming a missing background for all of them would be false of half the set.
    case "no-comparator":
      return "None of these cells could be compared: they had no background value, or were set aside as non-specific.";
    case "all-cells-gated":
      return "Every one of these cells bound the negative control strongly, so all were set aside as non-specific.";
    case "tie":
      return "The cells split evenly, with no majority either way.";
    case "below-agreement-floor":
      return agreement === undefined
        ? "The cells agreed less than this run requires."
        : `The cells agreed less than the ${Math.round(agreement * 100)}% this run requires.`;
    case "too-few-voters":
      return voters === undefined
        ? "This run requires more cells that can answer."
        : `This run requires at least ${voters} cell${voters === 1 ? "" : "s"} that can answer.`;
    case "never-offered":
      return "This antigen was not in the panel for the samples these cells came from.";
    default:
      return reason;
  }
};

// No line restating what the mark means. The legend sits above the card and stays there as the grid
// scrolls, so a sentence here repeats it in different words -- and did, naming the mark the legend's own
// swatch shows and the baseline in vocabulary that lives inside this block. What this panel alone can
// give is the evidence at THIS position: the counts, and where the reading did not settle, its reason.
// Every state but the two verdicts carries one, `never-offered` included, so nothing goes unexplained.
const lines = computed<string[]>(() => {
  const p = punch.value;
  if (p.kind !== "read") return ["No readable verdict for this clonotype at this identity"];

  // The antigen first. The header row scrolls out of view on a long grid, so the panel must say which column
  // this dot belongs to before it says anything about the verdict.
  const out = props.params.antigen === undefined ? [] : [props.params.antigen];
  out.push(p.state.toUpperCase());
  if (p.state !== "never asked") {
    // Two different sets, and the pair is shown wherever they differ: a gate set cells aside, or no
    // baseline could be computed for them. Also wherever the run carried panels that differ, where a
    // clonotype's cells were asked different things. With neither in play the two agree, and the second
    // number is the clonotype's own cell count, already beside its name in the grid.
    out.push(
      props.params.showAsked || p.asked !== p.answered
        ? `${p.answered} of the ${p.asked} cell${p.asked === 1 ? "" : "s"} asked could answer`
        : `${p.answered} cell${p.answered === 1 ? "" : "s"} could answer`,
    );
    // Both numbers travel with the verdict wherever it appears, and this one is the vote itself. Without
    // it a tie or a refused majority carries no split at all, since agreement is absent on those.
    //
    // Suppressed where nothing answered: "0 read bound" under "0 of the 4 cells asked could answer" is
    // not a split, it is the same zero said twice, and the reason below is what that reader needs.
    if (p.bound !== undefined && p.answered > 0) out.push(`${p.bound} read bound`);
    if (p.agreement !== undefined) out.push(`${Math.round(p.agreement * 100)}% agreed`);
  }
  if (p.reason !== undefined) out.push(whyUnsettled(p.reason));
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
