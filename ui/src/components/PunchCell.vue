<script setup lang="ts">
import { computed } from "vue";

// One punch. The glyph vocabulary is the use case's own punch-card figure
// (catalogue/discovery/antigen-barcode-binding-profiling/assets/punch-card.svg): a filled dot is bound and
// a dashed grey ring is a position never offered to this clonotype.
//
// The figure draws NOT BOUND as a blank cell, and this deviates from it: not-bound is a faint small dot.
// Blank reads as "not bound" only when hits are common. A real card is mostly negative by construction —
// a clonotype binds one antigen out of the panel, so a 74 x 13 grid is ~92% not-bound before anything goes
// wrong — and at that density a blank-for-negative card is indistinguishable from one that failed to load.
// The first reader of a live run asked why the punchcard was empty; it was not empty, it was answering.
// The faint dot says "asked, and the answer was no" while leaving bound the only thing that reads as ink.
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
// drawn as its own mark rather than guessed at, so an unreadable value cannot pass as an answer.
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
// times the evidence, and the floor keeps a one-cell reading visible rather than shrinking it to nothing.
// Only `bound` scales. A negative answer carries no magnitude worth showing, and sizing it would invite
// reading a large pale dot as a strong negative — so not-bound is one fixed, deliberately small size.
const diameter = computed(() => {
  const p = punch.value;
  if (p.kind === "read" && p.state === "not bound") return 5;
  if (p.kind !== "read" || p.state !== "bound" || p.couldAnswer <= 0) return 15;
  const fraction = Math.min(1, Math.max(0, p.answered / p.couldAnswer));
  return 8 + Math.sqrt(fraction) * 7;
});

// A total map rather than a switch, so a fifth state would fail to compile rather than silently fall
// through to whichever mark happened to be the default.
type Glyph = "dot" | "faint" | "ring" | "undef" | "unknown";
const GLYPH_OF: Record<VerdictState, Glyph> = {
  bound: "dot",
  "not bound": "faint",
  unreliable: "ring",
  "never asked": "undef",
};
const glyph = computed<Glyph>(() =>
  punch.value.kind === "read" ? GLYPH_OF[punch.value.state] : "unknown",
);

// Every cell carries its reading in words. The faint and hollow marks are the ones a reader cannot resolve
// by looking harder, so the sentence is where the counts and the state actually live.
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

/* The figure's own marks and colours, plus the faint negative. */
.punch--dot {
  background: #d94438;
}

/* Deliberately the smallest and palest mark: present enough that the grid reads as answered, quiet
   enough that scanning a column still counts only the filled dots. */
.punch--faint {
  background: #9aa3ae;
  opacity: 0.35;
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
