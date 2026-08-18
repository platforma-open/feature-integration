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

export const PUNCH_PAINT: Record<PunchGlyph, CSSProperties> = {
  bound: { background: "#1a7f37" },
  "not-bound": { background: "#d94438" },
  unreliable: { background: "#9aa3ae" },
  // Not a verdict — a value the renderer could not read. Marked rather than left blank, because blank
  // already means "never asked" on this card.
  unknown: { border: "1.5px dotted #d94438", opacity: "0.7" },
};
