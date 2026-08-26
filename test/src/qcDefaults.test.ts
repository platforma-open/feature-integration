import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AGGREGATE_DETECTION_DEFAULTS,
  QC_LINE_DEFAULTS,
} from "@platforma-open/milaboratories.feature-integration.model";

// The settings fields display these numbers where the stored value is undefined, and the workflow
// substitutes its own copy on the command line. Two copies that disagree put a number on screen that no run
// was scored against, and nothing else in the block compares them.
//
// Read as text rather than imported: one source is Tengo and the other Python.
const root = join(__dirname, "..", "..");
const tengo = readFileSync(join(root, "workflow/src/verdict-args.lib.tengo"), "utf8");
const python = readFileSync(join(root, "software/per-cell-metrics/src/qc_measures.py"), "utf8");

function tengoConst(name: string): number {
  const m = tengo.match(new RegExp(`^${name}\\s*:=\\s*([0-9.]+)\\s*$`, "m"));
  if (!m) throw new Error(`verdict-args.lib.tengo declares no ${name}`);
  return Number(m[1]);
}

function pythonConst(name: string): number {
  const m = python.match(new RegExp(`^${name}\\s*:\\s*\\w+\\s*=\\s*([0-9.]+)\\s*$`, "m"));
  if (!m) throw new Error(`qc_measures.py declares no ${name}`);
  return Number(m[1]);
}

describe("QC_LINE_DEFAULTS matches verdict-args.lib.tengo", () => {
  const pairs: [keyof typeof QC_LINE_DEFAULTS, string][] = [
    ["cellBarcodeValidWarn", "DEFAULT_CELL_BARCODE_VALID_WARN"],
    ["cellBarcodeValidError", "DEFAULT_CELL_BARCODE_VALID_ERROR"],
    ["readsPerCellWarn", "DEFAULT_READS_PER_CELL_WARN"],
    ["aggregateBarcodeWarn", "DEFAULT_AGGREGATE_BARCODE_WARN"],
    ["aggregateBarcodeError", "DEFAULT_AGGREGATE_BARCODE_ERROR"],
    ["undeclaredBarcodeWarn", "DEFAULT_UNDECLARED_BARCODE_WARN"],
    ["undeclaredBarcodeError", "DEFAULT_UNDECLARED_BARCODE_ERROR"],
    ["usableReadWarn", "DEFAULT_USABLE_READ_WARN"],
    ["usableReadError", "DEFAULT_USABLE_READ_ERROR"],
  ];

  it.each(pairs)("%s", (key, tengoName) => {
    expect(QC_LINE_DEFAULTS[key]).toBe(tengoConst(tengoName));
  });

  it("covers every line the tengo file declares", () => {
    const declared = [
      ...tengo.matchAll(
        /^(DEFAULT_(?:CELL_BARCODE_VALID|READS_PER_CELL|AGGREGATE_BARCODE|UNDECLARED_BARCODE|USABLE_READ)_[A-Z]+)\s*:=/gm,
      ),
    ].map((m) => m[1]);
    expect(new Set(declared)).toStrictEqual(new Set(pairs.map(([, name]) => name)));
  });
});

describe("AGGREGATE_DETECTION_DEFAULTS matches qc_measures.py", () => {
  const pairs: [keyof typeof AGGREGATE_DETECTION_DEFAULTS, string][] = [
    ["aggregateBarcodeIqrMultiplier", "AGGREGATE_BARCODE_IQR_MULTIPLIER"],
    ["aggregateBarcodeMinUmiThreshold", "AGGREGATE_BARCODE_MIN_THRESHOLD"],
    ["aggregateBarcodeTopN", "AGGREGATE_BARCODE_TOP_N"],
  ];

  it.each(pairs)("%s", (key, pythonName) => {
    expect(AGGREGATE_DETECTION_DEFAULTS[key]).toBe(pythonConst(pythonName));
  });
});
