<script setup lang="ts">
import { computed } from "vue";

// One punch. The glyph vocabulary is the use case's own punch-card figure
// (catalogue/discovery/antigen-barcode-binding-profiling/assets/punch-card.svg): a filled dot is bound, a
// BLANK cell is not bound, and a dashed grey ring is a position never offered to this clonotype. Blank
// carries meaning there, so nothing is drawn for a negative answer and the eye counts only what is filled.
//
// The figure has three glyphs because it predates the fourth state. `unreliable` — asked, and the data
// cannot settle it — gets the one shape left that reads as neither an answer nor an absence: a solid ring.
// It is deliberately not a faded dot, because `unreliable` is not a weak `bound`.
//
// The dot's SIZE is this component's own addition, and it is not decoration.
// `support-travels-with-the-reading` obliges the cells that could have answered and the cells that did to
// travel with a verdict wherever it appears, and its reason is about the page rather than the artifact: a
// reading resting on three cells must not look like one resting on forty. The figure shows every punch at
// one size, which a static illustration can afford and a grid of real verdicts cannot.
//
// The cell's value carries all three facts, `state|answered|couldAnswer`, because a grid pairs a cell with
// another column's cell only by position and no import guarantees that. Anything that does not parse is
// drawn as its own mark rather than guessed at — and never as blank, which already means not bound.
const props = defineProps<{ params: { value: unknown } }>();

const VERDICT_STATES = ["bound", "not bound", "unreliable", "never asked"] as const;
type VerdictState = (typeof VERDICT_STATES)[number];

type Punch =
  | { kind: "read"; state: VerdictState; answered: number; couldAnswer: number }
  | { kind: "unparsed" };

const punch = computed<Punch>(() => {
  const raw = props.params.value;
  if (typeof raw !== "string") return { kind: "unparsed" };
  const parts = raw.split("|");
  if (parts.length !== 3) return { kind: "unparsed" };
  const [state, answered, couldAnswer] = parts;
  const known = VERDICT_STATES.find((s) => s === state);
  const a = Number(answered);
  const c = Number(couldAnswer);
  if (known === undefined || !Number.isFinite(a) || !Number.isFinite(c))
    return { kind: "unparsed" };
  return { kind: "read", state: known, answered: a, couldAnswer: c };
});

// Area rather than diameter tracks the support: doubling a diameter quadruples the ink, which reads as four
// times the evidence. The floor keeps a one-cell reading visible — a dot that shrinks to nothing is a blank
// cell, and blank already means not bound.
const diameter = computed(() => {
  const p = punch.value;
  if (p.kind !== "read" || p.state !== "bound" || p.couldAnswer <= 0) return 15;
  const fraction = Math.min(1, Math.max(0, p.answered / p.couldAnswer));
  return 8 + Math.sqrt(fraction) * 7;
});

// Which glyph, if any. `not bound` draws nothing at all, which is the figure's own reading of blank. A
// total map rather than a switch, so a fifth state would fail to compile rather than fall through to blank
// — and blank is an answer here.
type Glyph = "dot" | "ring" | "undef" | "unknown" | "none";
const GLYPH_OF: Record<VerdictState, Glyph> = {
  bound: "dot",
  "not bound": "none",
  unreliable: "ring",
  "never asked": "undef",
};
const glyph = computed<Glyph>(() =>
  punch.value.kind === "read" ? GLYPH_OF[punch.value.state] : "unknown",
);

// Every cell carries its reading in words, including the blank ones — a blank punch is the one glyph a
// reader cannot ask about by looking harder.
const tooltip = computed(() => {
  const p = punch.value;
  if (p.kind !== "read") return "No readable verdict for this clonotype at this identity";
  if (p.state === "never asked")
    return "never asked — this clonotype's cells were never offered it";
  return `${p.state} — ${p.answered} of ${p.couldAnswer} cells answered`;
});
</script>

<template>
  <div class="punch-cell" :title="tooltip">
    <span
      v-if="glyph !== 'none'"
      class="punch"
      :class="`punch--${glyph}`"
      :style="{ width: `${diameter}px`, height: `${diameter}px` }"
    />
  </div>
</template>

<style scoped>
.punch-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.punch {
  display: inline-block;
  border-radius: 50%;
  box-sizing: border-box;
}

/* The figure's own three marks, and its colours. */
.punch--dot {
  background: #d94438;
}

.punch--undef {
  border: 1.5px dashed #9aa3ae;
}

.punch--ring {
  border: 1.5px solid #9aa3ae;
}

/* Not a state — a value this component could not read. Marked rather than blanked, because blank is an
   answer here. */
.punch--unknown {
  border: 1.5px dotted #d94438;
  opacity: 0.7;
}
</style>
