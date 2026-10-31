import {
  createPlDataTableStateV2,
  dataModel,
  initialData,
} from "@platforma-open/milaboratories.feature-integration.model";
import { describe, expect, test } from "vitest";

// The migration chain, run end to end over the payload a RELEASED project holds.
//
// `DataModel.migrate` does not jump to the latest version: it looks the stored version up in the chain and
// applies every step from there to the end, in order. So a v13 project runs every step after v13, and a
// field added by one of them is absent from the result unless that step actually ran.
//
// The payload below is FROZEN on purpose. Deriving it from `initialData()` and deleting the fields we know
// are new would defeat the test: a field added to `BlockData` tomorrow would appear on both sides and the
// missing migration would pass unnoticed. That is the one bug this file exists to catch, so the starting
// value has to be written out rather than computed.
//
// v13 is the version `v3.9.1` stores, the last release before this chain was extended. When a later release
// moves that baseline, this payload stays valid -- the chain only grows, and an older version keeps
// migrating -- so it is not rewritten per release. It is rewritten when the SHAPE of v13 is found to be
// wrong, which is a different event.

/** Every field `v3.9.1`'s `BlockData` requires, plus a few optional ones to prove values carry through. */
const V13_PAYLOAD = Object.freeze({
  // Optional, and carried rather than seeded: a migration that rebuilt the object instead of spreading it
  // would drop these, and the chain would still look like it worked.
  boundCutoff: 75,
  cellWhitelist: "",
  presetId: "tenx-beam",
  runMode: "full" as const,
  minAgreement: 0.6,

  // Required at v13.
  panelReferenceMinMembers: 2,
  distributionMinCells: 300,
  countFloor: 1,
  minVotingCells: 1,

  punchcardTableState: createPlDataTableStateV2(),
  tableState: createPlDataTableStateV2(),
  qcSummaryTableState: createPlDataTableStateV2(),
  reagentTableState: createPlDataTableStateV2(),
  undeclaredBarcodesTableState: createPlDataTableStateV2(),

  // The three chart states v13 knows about. `scoreDistributionGraphState` and
  // `referenceReadingGraphState` carry the unusable "line" template a v13 project really holds: it was
  // seeded while those plots were groundwork, and a histogram drawn on it renders nothing at all. The
  // chain has to replace it, not keep it.
  scoreDistributionGraphState: { title: "Spread of the run's scores", template: "line" },
  referenceReadingGraphState: { title: "Reference reading across cells", template: "line" },
  fittedBackgroundGraphState: { title: "Fitted background per tag", template: "dots" },
});

describe("the migration chain from the last released version", () => {
  const migrated = dataModel.migrate({ version: "v13", data: structuredClone(V13_PAYLOAD) });

  test("arrives at the chain's latest version", () => {
    expect(migrated.version).toBe(dataModel.version);
  });

  // The assertion that earns this file. A field added to `BlockData` and to `initialData` without a
  // migration beside it fails HERE, rather than when someone opens a year-old project and finds a blank
  // page. TypeScript does not catch it: every version alias resolves through to `BlockData`, so a new
  // field appears in the chain's input type as well as its output and the spread satisfies both.
  //
  // Keyed off `initialData()` so it needs no list of its own to go stale. The exceptions below are fields
  // a new block seeds but an old project may legitimately lack, because they are OPTIONAL in `BlockData`
  // and every reader defaults them. Adding to this list is a claim that the same is true of the new
  // entry -- check the `?` on its declaration before doing so.
  const SEEDED_BUT_OPTIONAL = new Set(["defaultBlockLabel", "boundProbability"]);

  test("carries every required field a new block is seeded with", () => {
    const seeded = initialData() as Record<string, unknown>;
    const result = migrated.data as Record<string, unknown>;
    const missing = Object.keys(seeded).filter(
      (key) => !(key in result) && !SEEDED_BUT_OPTIONAL.has(key),
    );
    expect(missing).toEqual([]);
  });

  test("adds the two chart states that arrived after v13", () => {
    const result = migrated.data as Record<string, unknown>;
    expect(result.panelAssignmentGraphState).toBeDefined();
    expect(result.antigenCountsGraphState).toBeDefined();
  });

  // Not a spot check of one field: the whole point of the v14 step is that a STORED value is replaced,
  // which a migration that only adds missing keys would not do. The "line" template above is the
  // case that shipped broken, so it is the one asserted.
  test("replaces the unusable template a v13 project stored", () => {
    const result = migrated.data as unknown as Record<string, { template?: string }>;
    expect(result.scoreDistributionGraphState?.template).toBe("bins");
    expect(result.referenceReadingGraphState?.template).toBe("bins");
  });

  test("leaves the settings a v13 project chose alone", () => {
    const result = migrated.data as Record<string, unknown>;
    expect(result.boundCutoff).toBe(75);
    expect(result.minAgreement).toBe(0.6);
    expect(result.presetId).toBe("tenx-beam");
  });

  // The fast path, and the one case that must NOT re-run anything: a project already at the latest version
  // is returned untouched, so a reader's own chart settings survive opening the block.
  test("leaves data already at the latest version untouched", () => {
    const current = initialData();
    const again = dataModel.migrate({ version: dataModel.version, data: current });
    expect(again.data).toBe(current);
  });
});
