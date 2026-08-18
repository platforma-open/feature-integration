<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed } from "vue";
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
const props = defineProps<{ params: { value: unknown } }>();

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

const cellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
};

// Why this mark is this colour, in the order a reader asks it: what the verdict is, what it rests on, and
// - where the verdict is unsettled - which of the seven ways it failed to settle. The reason tokens are
// machine values (`thin-comparator`, `tie`, ...), so each is expanded here rather than shown raw; a token
// is a key, not a sentence.
const WHY_UNSETTLED: Record<string, string> = {
  "never-offered": "no sample holding these cells declared this antigen",
  "no-comparator": "no comparator reading existed for these cells",
  "thin-comparator": "the comparator rested on too little to compare against",
  "all-cells-gated": "every cell was set aside by the admissibility gate",
  tie: "the cells split evenly, so no majority settled it",
  "below-agreement-floor": "the cells agreed less than the run required",
  "too-few-voters": "fewer cells answered than the run required",
};

const EXPLANATION: Record<VerdictState, string> = {
  bound:
    "green: a majority of the cells that answered read this antigen as bound against the comparator that served",
  "not bound":
    "red: the cells that answered read this antigen as not bound against the comparator that served",
  unreliable: "grey: the experiment asked, and the readings could not settle it",
  "never asked":
    "no mark: this clonotype's cells were never offered this antigen, so there is nothing to answer",
};

const tooltip = computed(() => {
  const p = punch.value;
  if (p.kind !== "read") return "No readable verdict for this clonotype at this identity";

  const lines = [p.state.toUpperCase(), EXPLANATION[p.state]];
  if (p.state !== "never asked") {
    lines.push(`${p.answered} of ${p.couldAnswer} cells answered`);
    if (p.agreement !== undefined) {
      lines.push(`${Math.round(p.agreement * 100)}% of them agreed`);
    }
  }
  if (p.reason !== undefined) {
    lines.push(WHY_UNSETTLED[p.reason] ?? p.reason);
  }
  return lines.join("\n");
});
</script>

<template>
  <div :style="cellStyle" :title="tooltip">
    <span v-if="glyph !== 'none'" :style="punchStyle" />
  </div>
</template>
