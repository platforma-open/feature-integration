import { kind } from "@platforma-open/milaboratories.feature-integration.kind";
import type { BlockData } from "@platforma-open/milaboratories.feature-integration.model";
import {
  createPlDataTableStateV2,
  templateParams,
  VERDICT_DEFAULTS,
} from "@platforma-open/milaboratories.feature-integration.model";
import type { PlRef } from "@platforma-sdk/model";
import { describe, expect, test } from "vitest";

// The block kind's init-params contract, from both ends: the runtime parser that admits a hand-written
// template entry, and the projection that writes one. Nothing here needs a backend.
//
// The invariant that matters is that the two are INVERSES. A field the projection emits but the parser
// drops is configuration that survives an export and vanishes on apply -- silently, because neither side
// fails. The round-trip test below is what catches that; the field-level tests only say why it failed.

const parse = (value: unknown) => kind.parseInitializationParams(value);

const A_REF: PlRef = { __isRef: true, blockId: "block-1", name: "out" } as PlRef;

describe("the params envelope", () => {
  test("an empty object is valid params -- every field is optional", () => {
    expect(parse({})).toStrictEqual({});
  });

  // assertParamsObject exists because each of these reads as an empty object to a naive check.
  test.each([
    ["null", null],
    ["a number", 5],
    ["an array", ["a"]],
    ["a string", "params"],
    ["undefined", undefined],
  ])("%s is not params", (_label, value) => {
    expect(() => parse(value)).toThrow(/object/i);
  });

  test("a key the contract does not name is dropped, not refused", () => {
    expect(parse({ countFloor: 7, notAField: "ignored" })).toStrictEqual({ countFloor: 7 });
  });
});

describe("field checks", () => {
  test("a ref field takes a PlRef and refuses anything else", () => {
    expect(parse({ fbFastqRef: A_REF })).toStrictEqual({ fbFastqRef: A_REF });
    expect(() => parse({ fbFastqRef: "block-1/out" })).toThrow(/fbFastqRef/);
    expect(() => parse({ datasetRef: {} })).toThrow(/datasetRef/);
  });

  test("a string field refuses a non-string", () => {
    expect(parse({ barcodeSeqColumn: "barcode" })).toStrictEqual({ barcodeSeqColumn: "barcode" });
    expect(() => parse({ barcodeSeqColumn: 3 })).toThrow(/barcodeSeqColumn/);
  });

  test("a number field refuses a non-number and a non-finite number", () => {
    expect(parse({ boundCutoff: 75 })).toStrictEqual({ boundCutoff: 75 });
    expect(() => parse({ boundCutoff: "75" })).toThrow(/boundCutoff/);
    expect(() => parse({ boundCutoff: Number.NaN })).toThrow(/boundCutoff/);
    expect(() => parse({ countFloor: Number.POSITIVE_INFINITY })).toThrow(/countFloor/);
  });

  test("referenceSource takes only the three the block reads", () => {
    for (const source of ["declared", "panel", "distribution"] as const) {
      expect(parse({ referenceSource: source })).toStrictEqual({ referenceSource: source });
    }
    expect(() => parse({ referenceSource: "median" })).toThrow(/referenceSource/);
  });

  // "panel" is retired and `args()` refuses a run under it, but the PARSER still admits it: a template
  // exported from a project stored under that choice must still apply, and be refused where the refusal
  // can name the replacement.
  test("the retired 'panel' baseline still parses", () => {
    expect(parse({ referenceSource: "panel" })).toStrictEqual({ referenceSource: "panel" });
  });

  test("referenceValues must be an array of strings", () => {
    expect(parse({ referenceValues: ["ctrl"] })).toStrictEqual({ referenceValues: ["ctrl"] });
    expect(() => parse({ referenceValues: "ctrl" })).toThrow(/referenceValues/);
    expect(() => parse({ referenceValues: ["ctrl", 2] })).toThrow(/referenceValues/);
  });
});

describe("the grouping rule", () => {
  test("by tag", () => {
    expect(parse({ grouping: { by: "tag" } })).toStrictEqual({ grouping: { by: "tag" } });
  });

  test("by property, naming several columns", () => {
    const grouping = { by: "property" as const, columns: ["antigen", "lot"] };
    expect(parse({ grouping })).toStrictEqual({ grouping });
  });

  // The single-column form predates the list. A project stored under it still applies.
  test("the legacy single-column form is still read", () => {
    const grouping = { by: "property" as const, column: "antigen" };
    expect(parse({ grouping })).toStrictEqual({ grouping });
  });

  test.each([
    ["an unknown 'by'", { by: "sample" }],
    ["a property rule naming nothing", { by: "property" }],
    ["columns that are not strings", { by: "property", columns: [1] }],
    ["not an object at all", "tag"],
  ])("%s is refused", (_label, grouping) => {
    expect(() => parse({ grouping })).toThrow(/grouping|object/i);
  });
});

describe("the round trip", () => {
  test("what the projection emits, the parser accepts", () => {
    const projected = templateParams(configuredData());
    expect(() => parse(projected)).not.toThrow();
  });

  test("a configured block survives project -> parse unchanged", () => {
    const projected = templateParams(configuredData());
    // Absent fields project as undefined and the parser drops them, so the comparison is against the
    // fields that actually carry a value -- which is what an applied template restores.
    const carried = Object.fromEntries(
      Object.entries(projected).filter(([, v]) => v !== undefined),
    );
    expect(parse(projected)).toStrictEqual(carried);
  });

  test("the sample column survives the trip", () => {
    // Regression guard: `sampleColumn` was omitted from the first version of this contract, which lost a
    // manually-picked sample-aware mapping on every export/apply. The snapshots beside it are
    // project-scoped and deliberately do NOT travel -- the UI takes those again.
    const projected = templateParams(configuredData());
    expect(parse(projected)).toMatchObject({ sampleColumn: "Sample" });
  });

  test("a blank block projects params the parser still accepts", () => {
    const projected = templateParams(blankData());
    expect(() => parse(projected)).not.toThrow();
  });
});

// Internals

/** The grid and plot state every `BlockData` carries. None of it belongs to the params contract. */
function viewState() {
  return {
    tableState: createPlDataTableStateV2(),
    qcSummaryTableState: createPlDataTableStateV2(),
    punchcardTableState: createPlDataTableStateV2(),
    reagentTableState: createPlDataTableStateV2(),
    undeclaredBarcodesTableState: createPlDataTableStateV2(),
    scoreDistributionGraphState: { title: "Spread of the run's scores", template: "line" },
    referenceReadingGraphState: { title: "Reference reading across cells", template: "line" },
    fittedBackgroundGraphState: { title: "Fitted background per tag", template: "dots" },
  } satisfies Partial<BlockData>;
}

/** A block the user has finished configuring, including the optional sample-aware mapping. */
function configuredData(): BlockData {
  return {
    ...VERDICT_DEFAULTS,
    ...viewState(),
    runMode: "full",
    presetId: "tenx-beam",
    cellWhitelist: "737K-august-2016",
    defaultBlockLabel: "",
    fbFastqRef: A_REF,
    datasetRef: A_REF,
    barcodeSeqColumn: "Barcode",
    featureNameColumn: "Antigen",
    sampleColumn: "Sample",
    // Project-scoped, and deliberately outside the contract.
    sampleLabelSnapshot: { s1: "Sample 1" },
    sampleColumnValues: ["Sample 1"],
    roleColumn: "Role",
    referenceValues: ["control"],
    referenceSource: "declared",
    grouping: { by: "property", columns: ["Antigen"] },
    countFloor: 4,
    boundCutoff: 75,
  };
}

/** A block as `init` leaves it: defaults, no picks. */
function blankData(): BlockData {
  return {
    ...VERDICT_DEFAULTS,
    ...viewState(),
    runMode: "full",
    presetId: "tenx-beam",
    cellWhitelist: "",
    defaultBlockLabel: "",
  };
}
