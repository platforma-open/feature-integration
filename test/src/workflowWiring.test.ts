import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// fb-downstream runs inside a render boundary, so it receives exactly the map fb-pipeline hands it and
// reads its parameters off `inputs`. A key fb-downstream reads that fb-pipeline never passes is
// permanently undefined, and every guard written as "add the flag only where it is set" then never fires.
// Nothing else in the block notices: the value still travels in `extra`, so it is part of every
// per-sample body's identity and changing it re-runs parse, refine-tags and tag-stat for every sample --
// while reaching no command line and changing no number.
//
// That is how the three aggregate-barcode knobs came to be settings a scientist could move for no effect.
//
// Read as source text, which is the weaker half of this test: a rename that both files make together is
// caught, a restructure of how fb-pipeline builds the map is not. There is no cheaper mechanism, because
// the map is built inside a template body that only a running workflow evaluates.
const root = join(__dirname, "..", "..");
const pipeline = readFileSync(join(root, "workflow/src/fb-pipeline.tpl.tengo"), "utf8");
const downstream = readFileSync(join(root, "workflow/src/fb-downstream.tpl.tengo"), "utf8");

/** The keys of the object literal fb-pipeline renders fb-downstream with. */
function renderedKeys(): Set<string> {
  const start = pipeline.indexOf("render.create(fbDownstreamTpl, {");
  expect(start, "fb-pipeline must render fb-downstream").toBeGreaterThan(-1);
  const open = pipeline.indexOf("{", start);
  const close = pipeline.indexOf("\n\t})", open);
  expect(close, "the render call's literal must close on its own line").toBeGreaterThan(open);
  const body = pipeline.slice(open, close);
  return new Set([...body.matchAll(/^\s*([A-Za-z][A-Za-z0-9]*)\s*:/gm)].map((m) => m[1]));
}

/** Every `inputs.X` fb-downstream reads. */
function readKeys(): Set<string> {
  return new Set([...downstream.matchAll(/\binputs\.([A-Za-z][A-Za-z0-9]*)/g)].map((m) => m[1]));
}

describe("fb-pipeline hands fb-downstream everything it reads", () => {
  it("passes every input fb-downstream reads", () => {
    const passed = renderedKeys();
    const read = [...readKeys()].sort();
    expect(
      read.length,
      "fb-downstream must read something, or this test is vacuous",
    ).toBeGreaterThan(0);
    expect(read.filter((key) => !passed.has(key))).toStrictEqual([]);
  });

  it("covers the aggregate-barcode knobs specifically", () => {
    // Named rather than left to the sweep above, because these three are settings a user can move and the
    // failure was invisible: the shipped defaults happen to equal the Python's, so nothing looked wrong
    // until somebody changed one.
    const passed = renderedKeys();
    for (const key of [
      "aggregateBarcodeIqrMultiplier",
      "aggregateBarcodeMinUmiThreshold",
      "aggregateBarcodeTopN",
    ]) {
      expect(passed.has(key), `${key} must reach fb-downstream`).toBe(true);
    }
  });
});
