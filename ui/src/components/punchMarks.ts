import type { CSSProperties } from "vue";

/**
 * The punchcard's marks, in one place.
 *
 * The cell renderer and the legend both paint from this map, so the legend cannot describe a colour the card
 * does not draw.
 *
 * Styles are inline values rather than CSS classes because these are consumed inside an ag-grid cell
 * renderer, which is instantiated outside Vue's scope-id context: a scoped stylesheet emits every rule with
 * a `[data-v-...]` attribute the rendered elements do not carry, so not one rule matches and the card paints
 * blank.
 */
export type PunchGlyph = "bound" | "not-bound" | "unreliable" | "unknown";

/**
 * One diameter for every mark on the card.
 *
 * Marks are NOT sized by how many of a clonotype's cells answered. The scientist is handed the two counts
 * twice already -- in the per-cell tooltip, and as columns in the clonotype expansion -- so one diameter
 * serves every mark and the card is read as a field of them at panel density.
 */
export const PUNCH_DIAMETER_PX = 22;

/**
 * The legend's swatches, smaller than the card's marks on purpose.
 *
 * A diameter carries no meaning any more, so a swatch drawn at a different size cannot misreport anything.
 * While the card sized its dots by evidence this would have been a real hazard.
 */
export const PUNCH_LEGEND_DIAMETER_PX = 11;

// Shape carries the state, colour only reinforces it. A green disc beside a red one is the textbook
// colour-vision failure -- measured at dE 5.5 under protanopia, where 8 is the floor -- and a card whose
// only channel is hue says nothing to a reader who cannot separate the two. Filled against hollow
// survives any colour vision, and the hues below are a second confirmation rather than the message.
//
// Blue, not the block's green: the page's own chrome is green, so a green mark reads as an affordance.
export const PUNCH_PAINT: Record<PunchGlyph, CSSProperties> = {
  // The finding, and the only saturated mark on the card.
  bound: { background: "#2563c9" },
  // Hollow, and blue like `bound`: each channel carries one thing. Hue says whether the cells settled it,
  // fill says which way. So the two verdicts share a colour and the two non-verdicts share grey.
  //
  // Thinner and lighter than `bound`'s disc, because this is the answer most positions carry and a page of
  // full-strength rings reads as noise. A lighter blue rather than opacity on the same one: opacity
  // composites against the row behind it, and the row changes on hover.
  "not-bound": { background: "transparent", border: "1.5px solid #92b1e4" },
  // Grey with `unknown` and `never asked`: no verdict was reached, whatever the route. A circle, because
  // every mark on this card is one -- a bar among discs reads as a different KIND of thing rather than a
  // third state.
  unreliable: { background: "#9aa3ae" },
  // Not a verdict, but a value the renderer could not read. Marked rather than left blank, because blank
  // already means "never asked" on this card. Amber: the one mark that reports a fault rather than a result.
  unknown: { border: "1.5px dotted #b26a00", opacity: "0.7" },
};

// ONE decoder for the punch value, shared by the grid cell and the clonotype expansion. The value is a
// single `|`-joined string because a grid pairs a cell with another column's cell only by position, and no
// import guarantees that, so everything a position needs travels together.
//
//   state | cellsAnswered | cellsAsked | agreement | unreliableReason | cellsBound
//
// `cellsBound` is the sixth field and was appended, so a value written before it existed has five and still
// decodes. Anything that does not decode is reported as such rather than guessed at.
export const VERDICT_STATES = ["bound", "not bound", "unreliable", "never asked"] as const;
export type VerdictState = (typeof VERDICT_STATES)[number];

export type Punch =
  | {
      kind: "read";
      state: VerdictState;
      answered: number;
      asked: number;
      agreement?: number;
      reason?: string;
      bound?: number;
    }
  | { kind: "unparsed" };

export function parsePunch(raw: unknown): Punch {
  if (typeof raw !== "string") return { kind: "unparsed" };
  const parts = raw.split("|");
  if (parts.length !== 5 && parts.length !== 6) return { kind: "unparsed" };
  const [state, answered, asked, agreement, reason, bound] = parts;
  const known = VERDICT_STATES.find((s) => s === state);
  const a = Number(answered);
  const c = Number(asked);
  if (known === undefined || !Number.isFinite(a) || !Number.isFinite(c))
    return { kind: "unparsed" };
  // Empty is carried as absent rather than as zero. A settled verdict has no reason, a set nobody could ask
  // has no agreement, and a zero agreement would read as total disagreement.
  const ag = agreement === "" ? undefined : Number(agreement);
  const b = bound === undefined || bound === "" ? undefined : Number(bound);
  return {
    kind: "read",
    state: known,
    answered: a,
    asked: c,
    agreement: ag !== undefined && Number.isFinite(ag) ? ag : undefined,
    reason: reason === "" ? undefined : reason,
    bound: b !== undefined && Number.isFinite(b) ? b : undefined,
  };
}
