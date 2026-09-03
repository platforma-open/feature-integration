import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AGGREGATE_DETECTION_DEFAULTS,
  QC_LINE_DEFAULTS,
  VERDICT_DEFAULTS,
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

// The verdict-side numbers sit in three Python modules rather than one, and two of them are written
// without a type annotation, so this takes the file and accepts both forms.
function pythonConstIn(file: string, name: string): number {
  const source = readFileSync(join(root, "software/per-cell-metrics/src", file), "utf8");
  const m = source.match(new RegExp(`^${name}\\s*(?::\\s*\\w+\\s*)?=\\s*([0-9.]+)\\s*$`, "m"));
  if (!m) throw new Error(`${file} declares no ${name}`);
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

// The lines above are the numbers nobody tunes. These five decide verdicts, and they are the ones that
// actually ship: verdict-args.lib.tengo adds --floor, --cutoff, --min-voters, --panel-min-members and
// --distribution-min-cells UNCONDITIONALLY, substituting the model's value wherever the stored one is
// undefined. So the Python argparse defaults never govern a workflow-driven run, and a drift in the
// model's copy would score every run at a number no test had seen.
describe("VERDICT_DEFAULTS matches verdict-args.lib.tengo and the Python that owns each number", () => {
  const pairs: [keyof typeof VERDICT_DEFAULTS, string, string, string][] = [
    ["countFloor", "DEFAULT_COUNT_FLOOR", "verdict.py", "DEFAULT_FLOOR"],
    ["boundCutoff", "DEFAULT_BOUND_CUTOFF", "verdict.py", "BOUND_CUTOFF"],
    [
      "boundProbability",
      "DEFAULT_BOUND_PROBABILITY",
      "verdict.py",
      "DISTRIBUTION_BOUND_PROBABILITY",
    ],
    [
      "expectedBinderFraction",
      "DEFAULT_EXPECTED_BINDER_FRACTION",
      "tag_distribution.py",
      "DEFAULT_INITIAL_SIGNAL_WEIGHT",
    ],
    ["minVotingCells", "DEFAULT_MIN_VOTING_CELLS", "combine.py", "DEFAULT_MIN_VOTERS"],
    [
      "panelReferenceMinMembers",
      "DEFAULT_PANEL_MIN_MEMBERS",
      "verdict.py",
      "DEFAULT_PANEL_MIN_MEMBERS",
    ],
    [
      "distributionMinCells",
      "DEFAULT_DISTRIBUTION_MIN_CELLS",
      "tag_distribution.py",
      "DEFAULT_DISTRIBUTION_MIN_CELLS",
    ],
  ];

  it.each(pairs)("%s", (key, tengoName, pythonFile, pythonName) => {
    expect(VERDICT_DEFAULTS[key]).toBe(tengoConst(tengoName));
    expect(VERDICT_DEFAULTS[key]).toBe(pythonConstIn(pythonFile, pythonName));
  });

  it("covers every shaping default the tengo file declares", () => {
    const declared = [
      ...tengo.matchAll(
        /^(DEFAULT_(?:COUNT_FLOOR|BOUND_CUTOFF|BOUND_PROBABILITY|EXPECTED_BINDER_FRACTION|MIN_VOTING_CELLS|PANEL_MIN_MEMBERS|DISTRIBUTION_MIN_CELLS))\s*:=/gm,
      ),
    ].map((m) => m[1]);
    expect(new Set(declared)).toStrictEqual(new Set(pairs.map(([, name]) => name)));
  });
});
