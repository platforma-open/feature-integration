// mitool tag-pattern (read-geometry) model for the Feature Integration block.
//
// Feature-barcode reads have a FIXED single-cell layout across every documented 10x 5' antigen-capture
// variant (verified against the 10x docs):
//   Read 1: CELL barcode + UMI          Read 2: [optional leading skip] + FEATURE barcode + remainder
// so — unlike the general peptide-amplicon builder in blocks/peptide-extraction — there are no anchors,
// no per-read insert assignment, no reverse-complement mirroring, and no single-end case. The only
// user-tunable numbers are the three barcode lengths plus a Read 2 offset (the TotalSeq-C / next-gen
// antigen-barcoding case, where the 15 nt barcode sits behind a 10 nt lead: `5PNNNNNNNNNN(BC)`).

// Tag names mitool registers for this pipeline. This is the SINGLE source of truth: they are baked into
// every assembled pattern here AND sent to the workflow in args.tags, so the downstream commands
// (`refine-tags -t CELL -t FEATURE -u UMI`, `tag-stat`, per_cell) reference exactly the names the
// pattern declares — no independent copy in the workflow to drift.
export const CELL_TAG = "CELL";
export const UMI_TAG = "UMI";
export const FEATURE_TAG = "FEATURE";

/** Structured read geometry for the UI pattern builder. UI-facing only: never projected into BlockArgs
 *  (the workflow consumes the assembled pattern string / derived lengths) and never persisted on its own
 *  (it is derived from the pattern string on demand). `featureOffset` is an anonymous `N{skip}` — it is
 *  not a named tag, so it appears here (a builder field that must round-trip) but nowhere in the
 *  workflow commands. */
export type PatternParts = {
  cellLen: number; // CELL barcode length on Read 1
  umiLen: number; // UMI length on Read 1
  r1TrailingWildcard: boolean; // trailing `*` on Read 1 — tolerate sequence past CELL+UMI (e.g. 28 nt R1)
  featureLen: number; // FEATURE barcode length on Read 2
  featureOffset: number; // leading N-skip before the feature barcode on Read 2 (0 = R2 position 0)
};

// The fixed BEAM shape. The trailing `*` on Read 1 is captured (group 3) so the builder can round-trip
// it; it tolerates R1 sequenced longer than CELL+UMI (e.g. the 28 nt R1 common in 5' runs).
const PATTERN_RE =
  /^\^\(CELL:N\{(\d+)\}\)\(UMI:N\{(\d+)\}\)(\*)?\\\^(?:N\{(\d+)\})?\(FEATURE:N\{(\d+)\}\)\(R2:\*\)$/;

/** Parse a BEAM feature-barcode pattern into its structured parts, or null if it is not the BEAM shape. */
export function parsePattern(s: string): PatternParts | null {
  const m = PATTERN_RE.exec(s.trim());
  if (!m) return null;
  return {
    cellLen: parseInt(m[1], 10),
    umiLen: parseInt(m[2], 10),
    r1TrailingWildcard: m[3] === "*",
    featureOffset: m[4] !== undefined ? parseInt(m[4], 10) : 0,
    featureLen: parseInt(m[5], 10),
  };
}

/** Loose validation for a user-supplied pattern (write mode + args). mitool does the real parsing; this
 *  only enforces what the block's downstream commands depend on: `refine-tags`/`tag-stat`/`per_cell`
 *  reference the CELL, UMI, and FEATURE tags plus the R2 capture by name, so those must be present. Any
 *  other content (constant flanks, an N-spacer, anchors) is passed to mitool verbatim. Returns null when
 *  valid, else a message naming what is missing. */
export function validatePattern(s: string): string | null {
  const p = s.trim();
  if (!p) return "Tag pattern is required";
  const required: [string, RegExp][] = [
    [CELL_TAG, new RegExp(`\\(${CELL_TAG}:`)],
    [UMI_TAG, new RegExp(`\\(${UMI_TAG}:`)],
    [FEATURE_TAG, new RegExp(`\\(${FEATURE_TAG}:`)],
    ["R2", /\(R2:/],
  ];
  const missing = required.filter(([, re]) => !re.test(p)).map(([name]) => name);
  if (missing.length > 0)
    return (
      `Pattern must define the ${missing.join(", ")} tag${missing.length > 1 ? "s" : ""}. ` +
      `Read 1 needs (CELL:…) and (UMI:…); Read 2 needs (FEATURE:…) and (R2:…).`
    );
  return null;
}

/** Assemble the mitool pattern string from structured parts (R1 trailing `*` and R2 offset optional). */
export function assemblePattern(p: PatternParts): string {
  const trailing = p.r1TrailingWildcard ? "*" : "";
  // Anonymous N-skip — bare (no parentheses). mitool reads `(...)` as a `(TAG:pattern)` group, so a
  // parenthesized `(N{n})` is rejected with "Unexpected character in tag identifier"; a bare `N{n}` is
  // matched but not captured, which is exactly what an offset should be. Verified against mitool 2.3.1.
  const skip = p.featureOffset > 0 ? `N{${p.featureOffset}}` : "";
  return (
    `^(${CELL_TAG}:N{${p.cellLen}})(${UMI_TAG}:N{${p.umiLen}})${trailing}` +
    `\\^${skip}(${FEATURE_TAG}:N{${p.featureLen}})(R2:*)`
  );
}
