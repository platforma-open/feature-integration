import { describe, expect, it } from "vitest";
import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
// deriveProgress lives in the block UI package, and is imported directly: a pure function with model-only
// deps.
import { deriveProgress } from "../../ui/src/progress";

const S = "SAMPLE1";
// Every case below is a stream that is still open, which is what these tests are about. `closed` is the
// other half: a finished step's last tick, which must not be replayed as a live reading.
const line = (stage: string) => ({ line: `${ProgressPrefix}${stage}`, live: true });
const closed = (stage: string) => ({ line: `${ProgressPrefix}${stage}`, live: false });

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

  it("does not replay a finished step's last tick as a live reading", () => {
    // mitool prints progress on a timer and the process finishes between ticks, so a completed parse ends
    // on whatever tick landed last -- 97.8% with a one-second ETA is a normal way for it to end. Shown as
    // live it says the run is nearly through a step it already finished, and offers an ETA that never
    // elapses. A closed stream sits at the TOP of its band instead.
    const cell = deriveProgress(
      S,
      new Set(),
      { [S]: "parsing" },
      {
        "1-parse": closed("Parsing sequences: 97.8%  ETA: 00:00:01"),
      },
    );
    expect(cell.percent).toBe(25);
    expect(cell.text).not.toContain("97.8");
    expect(cell.suffix).toBe("");
  });

  it("still shows a live step's own percent", () => {
    // The other side of the same rule: an open stream is a reading and its figures are current.
    const cell = deriveProgress(
      S,
      new Set(),
      { [S]: "parsing" },
      {
        "1-parse": line("Parsing sequences: 40.0%  ETA: 00:00:30"),
      },
    );
    expect(cell.text).toContain("40.0");
    expect(cell.percent).toBeLessThan(25);
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
