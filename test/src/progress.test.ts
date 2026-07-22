import { describe, expect, it } from "vitest";
import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
// deriveProgress lives in the block UI package; imported directly (pure function, model-only deps).
import { deriveProgress } from "../../ui/src/progress";

const S = "SAMPLE1";
const line = (stage: string) => `${ProgressPrefix}${stage}`;

describe("deriveProgress — label follows the live stream, not the report step", () => {
  it("does not flash 'Counting UMIs' while refine is still the furthest live step", () => {
    // Report step already advanced to "counting", but only refine has a live line → label must stay
    // on refine (the pre-fix bug flashed "Counting UMIs" here).
    const cell = deriveProgress(
      S,
      new Set(),
      { [S]: "counting" },
      {
        "2-refine": line("Refining tag UMI"),
        "3-tagstat": undefined,
      },
    );
    expect(cell.text).toContain("Refining barcodes");
    expect(cell.text).not.toContain("Counting UMIs");
  });

  it("shows a Counting UMIs variant once tag-stat streams", () => {
    // Now tag-stat is the furthest step with a live line → label follows it.
    const cell = deriveProgress(
      S,
      new Set(),
      { [S]: "counting" },
      {
        "2-refine": line("Refining tag UMI"),
        "3-tagstat": line("Sorting records, step 1 of 2: 50%"),
      },
    );
    expect(cell.text).toContain("Counting UMIs");
  });

  it("keeps the bar monotonic — never below the reported step floor", () => {
    // Report floor for "counting" is 50; refine's within-band fill must not drag the bar below it.
    const cell = deriveProgress(
      S,
      new Set(),
      { [S]: "counting" },
      {
        "2-refine": line("Refining tag UMI"),
      },
    );
    expect(cell.percent).toBeGreaterThanOrEqual(50);
  });
});
