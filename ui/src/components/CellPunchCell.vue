<script setup lang="ts">
import type { CSSProperties } from "vue";
import { computed, ref } from "vue";
import { PUNCH_DIAMETER_PX, PUNCH_PAINT, VERDICT_STATES, type VerdictState } from "./punchMarks";

// One cell's own reading at one identity, in the same four marks the card above uses. A separate component
// from `PunchCell` rather than a mode of it: PunchCell's explanations are all about a MAJORITY, and none of
// that is true of a single cell.
//
// What is shared is what must not drift: the paint map and the diameter, both from `punchMarks.ts`. A cell
// reading bound has to be the same green in both faces.
//
// The value is `state|reason`, two fields. A set's punch carries six because a verdict rests on counts a
// reader needs beside it. A cell IS the evidence.
const props = defineProps<{ params: { value: unknown; antigen?: string } }>();

type Reading = { state: VerdictState; reason?: string } | { state: "unparsed" };

// An EMPTY position is never-asked, and that is the difference from the set-level card. There, every
// position carries an explicit state, "never asked" among them. Here the export writes a row only where the
// cell's sample was stained for the identity, because writing the string instead would make the file cells
// x identities dense on a panel that can run to hundreds.
//
// So null is a READING rather than a missing value, and it must not fall through to the unreadable-value
// mark. That mark means "a value arrived and did not decode".
const reading = computed<Reading>(() => {
  const raw = props.params.value;
  if (raw === null || raw === undefined || raw === "") return { state: "never asked" };
  if (typeof raw !== "string") return { state: "unparsed" };
  const [state, reason] = raw.split("|");
  const known = VERDICT_STATES.find((s) => s === state);
  if (known === undefined) return { state: "unparsed" };
  return { state: known, reason: reason === "" || reason === undefined ? undefined : reason };
});

type Glyph = "bound" | "not-bound" | "unreliable" | "none" | "unknown";
const GLYPH_OF: Record<VerdictState, Glyph> = {
  bound: "bound",
  "not bound": "not-bound",
  unreliable: "unreliable",
  "never asked": "none",
};
const glyph = computed<Glyph>(() =>
  reading.value.state === "unparsed" ? "unknown" : GLYPH_OF[reading.value.state],
);

const punchStyle = computed<CSSProperties>(() => ({
  display: "inline-block",
  boxSizing: "border-box",
  borderRadius: "50%",
  width: `${PUNCH_DIAMETER_PX}px`,
  height: `${PUNCH_DIAMETER_PX}px`,
  ...(glyph.value === "none" ? {} : PUNCH_PAINT[glyph.value]),
}));

const cellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  height: "100%",
};

// No token -> sentence map here, deliberately. `UnreliableReason`'s VALUES are already the prose meant for a
// reader ("no comparator for this cell"), and the enum member is what code compares against. The set-level
// card expands tokens because its reasons come from `SetUnreliableReason`, a different vocabulary that
// really is machine values.
//
// Prefixed rather than capitalised, for the reason PunchCell gives: a blanket transform would also
// capitalise the antigen name, which is panel data.

const EXPLANATION: Record<VerdictState, string> = {
  bound: "Green: this cell read the antigen as bound against the baseline that served",
  "not bound":
    "Red: this cell read the antigen as not bound. A cell that was asked and returned no count reads here too — a zero count is a reading",
  unreliable: "Grey: this cell could not be compared at all, so it cast no vote",
  "never asked":
    "No mark: no sample holding this cell declared this antigen, so there was nothing to answer",
};

const lines = computed<string[]>(() => {
  const r = reading.value;
  if (r.state === "unparsed") return ["No readable reading for this cell at this identity"];
  const out = props.params.antigen === undefined ? [] : [props.params.antigen];
  out.push(r.state.toUpperCase(), EXPLANATION[r.state]);
  if (r.reason !== undefined) out.push(`Why: ${r.reason}`);
  return out;
});

// Teleported and hand-positioned for the same three reasons PunchCell's panel is: a native `title` inside a
// virtualised grid cell does not fire at all, cannot be laid out, and waits for the OS hover delay.
const hover = ref<{ x: number; y: number } | null>(null);

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
