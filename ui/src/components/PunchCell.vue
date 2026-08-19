<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed, ref } from "vue";
import { PUNCH_PAINT } from "./punchMarks";

// One punch, in the operator's vocabulary rather than the use case figure's:
//
//   bound        a GREEN dot
//   not bound    a RED dot
//   unreliable   a GREY dot
//   never asked  nothing at all
//
// This departs from `assets/punch-card.svg`, which fills a dot for bound, leaves the cell blank for not
// bound, and rings it for never-offered. Operator decision, taken after reading a live card: the figure's
// blank-for-negative is unreadable at real density, because a clonotype binds one antigen out of the panel
// and the grid is therefore ~92% negative before anything goes wrong. Here blank is reserved for the one
// state that genuinely has no answer in it - a position the experiment never put to this clonotype.
//
// The three states that ARE answers all read as dots of one family, so the card is a field of colour and a
// reader is never asked to tell a shape from an absence.
//
// SIZE carries what the reading rests on, and it applies to bound and not bound alike.
// `support-travels-with-the-reading` obliges both counts to travel with a verdict wherever it appears, and
// its reason is confidence, not magnitude: a reading resting on three cells must not look like one resting
// on forty, and that is as true of a negative as of a positive. An earlier revision of this file sized only
// `bound`, arguing a negative carries no magnitude worth showing; that conflated how much was bound with
// how much was measured, and the atom is about the second. `unreliable` and `never asked` stay fixed,
// because neither asserts anything for evidence to support.
//
// The cell's value carries everything needed to explain itself,
// `state|answered|couldAnswer|agreement|reason`, in ONE value because a grid pairs a cell with another
// column's cell only by position and no import guarantees that. Anything that does not parse is drawn as
// its own mark rather than guessed at, so an unreadable value cannot pass as an answer.
//
// There is deliberately no score and no binding level here: `binary-narrowing` forbids one leaving the
// block, so the tooltip explains a verdict by what it RESTS on and never by how strongly anything bound.
const props = defineProps<{ params: { value: unknown; antigen?: string; mergedNote?: string } }>();

const VERDICT_STATES = ["bound", "not bound", "unreliable", "never asked"] as const;
type VerdictState = (typeof VERDICT_STATES)[number];

type Punch =
  | {
      kind: "read";
      state: VerdictState;
      answered: number;
      couldAnswer: number;
      agreement?: number;
      reason?: string;
    }
  | { kind: "unparsed" };

const punch = computed<Punch>(() => {
  const raw = props.params.value;
  if (typeof raw !== "string") return { kind: "unparsed" };
  const parts = raw.split("|");
  if (parts.length !== 5) return { kind: "unparsed" };
  const [state, answered, couldAnswer, agreement, reason] = parts;
  const known = VERDICT_STATES.find((s) => s === state);
  const a = Number(answered);
  const c = Number(couldAnswer);
  if (known === undefined || !Number.isFinite(a) || !Number.isFinite(c))
    return { kind: "unparsed" };
  // Both are legitimately empty: a settled verdict has no reason, and a set nobody could ask has no
  // agreement. Empty is carried as absent rather than as zero, which would read as total disagreement.
  const ag = agreement === "" ? undefined : Number(agreement);
  return {
    kind: "read",
    state: known,
    answered: a,
    couldAnswer: c,
    agreement: ag !== undefined && Number.isFinite(ag) ? ag : undefined,
    reason: reason === "" ? undefined : reason,
  };
});

const diameter = computed(() => {
  const p = punch.value;
  if (p.kind !== "read") return 11;
  // Neither of these rests on anything an evidence count could describe.
  if (p.state === "unreliable" || p.state === "never asked") return 11;
  if (p.couldAnswer <= 0) return 8;
  // Area rather than diameter tracks the support: doubling a diameter quadruples the ink, which would read
  // as four times the evidence. The floor keeps a one-cell reading visible instead of shrinking it away.
  const fraction = Math.min(1, Math.max(0, p.answered / p.couldAnswer));
  return 8 + Math.sqrt(fraction) * 7;
});

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

// Painted from the shared map so the legend above the card cannot describe a colour the card does not
// draw. See punchMarks.ts for why these are inline values rather than CSS classes.
const punchStyle = computed<CSSProperties>(() => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: `${diameter.value}px`,
  height: `${diameter.value}px`,
  ...(glyph.value === "none" ? {} : PUNCH_PAINT[glyph.value]),
}));

// Fills the cell, so the hover target is the whole cell rather than the dot. The measured wrapper was
// 130px inside a 161px cell, which leaves a strip on each side where a reader aiming at the column would
// get nothing back.
const cellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  height: "100%",
};

// Why this mark is this colour, in the order a reader asks it: what the verdict is, what it rests on, and
// - where the verdict is unsettled - which of the seven ways it failed to settle. The reason tokens are
// machine values (`thin-comparator`, `tie`, ...), so each is expanded here rather than shown raw; a token
// is a key, not a sentence.
const WHY_UNSETTLED: Record<string, string> = {
  "never-offered": "no sample holding these cells declared this antigen",
  "no-comparator": "no baseline reading existed for these cells",
  "thin-comparator": "the baseline rested on too little to judge against",
  "all-cells-gated": "every cell was set aside by the admissibility gate",
  tie: "the cells split evenly, so no majority settled it",
  "below-agreement-floor": "the cells agreed less than the run required",
  "too-few-voters": "fewer cells answered than the run required",
};

const EXPLANATION: Record<VerdictState, string> = {
  bound:
    "green: a majority of the cells that answered read this antigen as bound against the baseline that served",
  "not bound":
    "red: the cells that answered read this antigen as not bound against the baseline that served",
  unreliable: "grey: the experiment asked, and the readings could not settle it",
  "never asked":
    "no mark: this clonotype's cells were never offered this antigen, so there is nothing to answer",
};

const lines = computed<string[]>(() => {
  const p = punch.value;
  if (p.kind !== "read") return ["No readable verdict for this clonotype at this identity"];

  // The antigen first: its header is truncated by default and may be scrolled out of view entirely, so
  // the panel has to say which column this dot belongs to before it says anything about the verdict.
  const out = props.params.antigen === undefined ? [] : [props.params.antigen];
  out.push(p.state.toUpperCase(), EXPLANATION[p.state]);
  if (p.state !== "never asked") {
    out.push(`${p.answered} of ${p.couldAnswer} cells answered`);
    if (p.agreement !== undefined) out.push(`${Math.round(p.agreement * 100)}% of them agreed`);
  }
  if (p.reason !== undefined) out.push(WHY_UNSETTLED[p.reason] ?? p.reason);
  // Last, because it is about the COLUMN rather than this verdict: why this identity is one merged
  // reagent while its neighbours are single antigens.
  if (props.params.mergedNote !== undefined) out.push(props.params.mergedNote);
  return out;
});

// The panel is rendered by this component and teleported to <body>, rather than left to the browser's
// native `title`. Three reasons, and the first is decisive: a `title` inside a virtualised grid cell did
// not fire at all in the app, which is what a reader reported after the tooltip was "verified" by
// inspecting attributes in the DOM. A title also cannot be styled or laid out - a five-line explanation
// arrives as one run-together string - and it appears only after the OS hover delay, which for a grid a
// reader is scanning is long enough to feel absent.
//
// Teleported because the cell clips: ag-grid gives each cell `overflow: hidden`, so a panel rendered in
// place is cut to a 40px row.
const hover = ref<{ x: number; y: number } | null>(null);

// Positioned beside the cursor and flipped when it would leave the window, so a punch in the last column
// or the bottom row still shows its whole explanation.
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
