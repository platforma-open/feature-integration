<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed } from "vue";

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

// The colours are INLINE, and that is not a style preference.
//
// ag-grid instantiates a cell renderer outside the scope-id context, so the elements this component
// renders carry no `data-v-...` attribute - while a scoped stylesheet emits every rule WITH one. A scoped
// block therefore matched nothing here: the classes were on the elements, the rules were in the
// stylesheet, and not one of them applied. The card rendered 314 not-bound punches on a transparent
// background and looked blank, which is exactly what a reader reported. Inline styles cannot be defeated
// that way.
//
// `unknown` is not a state - it is a value this component could not read - so it is marked rather than
// left blank, because blank means never-asked here.
const PAINT: Record<Exclude<Glyph, "none">, CSSProperties> = {
  bound: { background: "#1a7f37" },
  "not-bound": { background: "#d94438" },
  unreliable: { background: "#9aa3ae" },
  unknown: { border: "1.5px dotted #d94438", opacity: "0.7" },
};

const punchStyle = computed<CSSProperties>(() => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: `${diameter.value}px`,
  height: `${diameter.value}px`,
  ...(glyph.value === "none" ? {} : PAINT[glyph.value]),
}));

const cellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
};

// Every cell carries its reading in words. Colour separates the three answers and size carries their
// support, but neither says WHICH counts, and an empty cell says nothing by design - so the sentence is
// where the state and the two numbers actually live.
const tooltip = computed(() => {
  const p = punch.value;
  if (p.kind !== "read") return "No readable verdict for this clonotype at this identity";
  if (p.state === "never asked")
    return "never asked — this clonotype's cells were never offered it";
  return `${p.state} — ${p.answered} of ${p.couldAnswer} cells answered`;
});
</script>

<template>
  <div :style="cellStyle" :title="tooltip">
    <span v-if="glyph !== 'none'" :style="punchStyle" />
  </div>
</template>
