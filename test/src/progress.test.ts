import { describe, expect, it } from "vitest";
import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
// deriveProgress lives in the block UI package, and is imported directly: a pure function with model-only
// deps.
import { deriveProgress } from "../../ui/src/progress";

const S = "SAMPLE1";
const line = (stage: string) => `${ProgressPrefix}${stage}`;

describe("deriveProgress — label follows the live stream, not the report step", () => {
  it("does not flash 'Counting UMIs' while refine is still the furthest live step", () => {
    // The report step has advanced to "counting" while only refine has a live line, so the label must stay on
    // refine. Following the report step flashes "Counting UMIs" here.
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
    // tag-stat is now the furthest step with a live line, so the label follows it.
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
    // The report floor for "counting" is 50, and refine's within-band fill must not drag the bar below it.
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
