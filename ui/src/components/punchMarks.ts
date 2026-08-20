import type { CSSProperties } from "vue";

/**
 * The punchcard's marks, in one place.
 *
 * The cell renderer and the legend both paint from this map, so the legend cannot describe a colour the
 * card does not draw. Two hand-maintained copies is the ordinary way a legend goes wrong, and it is
 * invisible when it does: both halves look deliberate, and only a reader comparing them closely would
 * notice that the swatch and the cell disagree.
 *
 * Styles are inline values rather than CSS classes because these are consumed inside an ag-grid cell
 * renderer, which is instantiated outside Vue's scope-id context: a scoped stylesheet emits every rule
 * with a `[data-v-…]` attribute the rendered elements do not carry, so not one rule matches and the card
 * paints blank. That is not hypothetical — it shipped, and the card was reported as empty.
 */
export type PunchGlyph = "bound" | "not-bound" | "unreliable" | "unknown";

/**
 * One diameter for every mark on the card, and for every swatch in the legend.
 *
 * The card used to size bound and not-bound by how many of a clonotype's cells answered, on the reading
 * that a verdict resting on three cells must not look like one resting on forty. The obligation behind
 * that is `support-travels-with-the-reading`, and it is a delivery obligation: it fixes that the scientist
 * is HANDED the two counts, not that a dot encode them. They are handed over twice already -- in the
 * per-cell tooltip, and as columns in the clonotype expansion -- so the card is free to be a field of
 * flat colour, which is what it is actually read as at panel density.
 *
 * Shared with the legend so a swatch cannot be a different size from the thing it explains.
 */
export const PUNCH_DIAMETER_PX = 18;

export const PUNCH_PAINT: Record<PunchGlyph, CSSProperties> = {
  bound: { background: "#1a7f37" },
  "not-bound": { background: "#d94438" },
  unreliable: { background: "#9aa3ae" },
  // Not a verdict — a value the renderer could not read. Marked rather than left blank, because blank
  // already means "never asked" on this card.
  unknown: { border: "1.5px dotted #d94438", opacity: "0.7" },
};

// ONE decoder for the punch value, shared by the grid cell and the clonotype expansion. The value is a
// single `|`-joined string because a grid pairs a cell with another column's cell only by position and
// no import guarantees that -- so everything a position needs travels together. Two readers of one
// format would be two chances to disagree about it, which is why this lives here and not in a component.
//
//   state | cellsAnswered | cellsCouldAnswer | agreement | unreliableReason | cellsBound
//
// `cellsBound` is the sixth field and was appended, so a value written before it existed has five and
// still decodes. Anything that does not decode is reported as such rather than guessed at: an
// unreadable value must never pass for an answer.
export const VERDICT_STATES = ["bound", "not bound", "unreliable", "never asked"] as const;
export type VerdictState = (typeof VERDICT_STATES)[number];

export type Punch =
  | {
      kind: "read";
      state: VerdictState;
      answered: number;
      couldAnswer: number;
      agreement?: number;
      reason?: string;
      bound?: number;
    }
  | { kind: "unparsed" };

export function parsePunch(raw: unknown): Punch {
  if (typeof raw !== "string") return { kind: "unparsed" };
  const parts = raw.split("|");
  if (parts.length !== 5 && parts.length !== 6) return { kind: "unparsed" };
  const [state, answered, couldAnswer, agreement, reason, bound] = parts;
  const known = VERDICT_STATES.find((s) => s === state);
  const a = Number(answered);
  const c = Number(couldAnswer);
  if (known === undefined || !Number.isFinite(a) || !Number.isFinite(c))
    return { kind: "unparsed" };
  // Empty is carried as absent rather than as zero. A settled verdict has no reason, a set nobody could
  // ask has no agreement, and a zero agreement would read as total disagreement.
  const ag = agreement === "" ? undefined : Number(agreement);
  const b = bound === undefined || bound === "" ? undefined : Number(bound);
  return {
    kind: "read",
    state: known,
    answered: a,
    couldAnswer: c,
    agreement: ag !== undefined && Number.isFinite(ag) ? ag : undefined,
    reason: reason === "" ? undefined : reason,
    bound: b !== undefined && Number.isFinite(b) ? b : undefined,
  };
}
