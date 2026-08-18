import type {
  BlockData,
  platforma,
} from "@platforma-open/milaboratories.feature-integration.model";
import { awaitStableState, blockTest } from "@platforma-sdk/test";
import { blockSpec as samplesAndDataBlockSpec } from "@platforma-open/milaboratories.samples-and-data";
import type { BlockArgs as SamplesAndDataBlockArgs } from "@platforma-open/milaboratories.samples-and-data.model";
import { uniquePlId } from "@platforma-open/milaboratories.samples-and-data.model";
import { FeatureIntegrationBlockPointer as myBlockSpec } from "this-block";
import type { InferBlockState, PTableHandle } from "@platforma-sdk/model";
import { createPlDataTableStateV2, wrapOutputs } from "@platforma-sdk/model";

// Block tests for the Feature Barcode Profiling block.
//
// WHAT THIS FILE COVERS, and what it does not. The block has two halves and this file reaches only
// one of them:
//
//   * the per-sample counting half — FASTQ in, per-cell UMI counts out — is exercised end to end by
//     the second test below, when a backend can run it.
//   * the ANTIGEN VERDICT half is not covered here at all. It needs a single-cell V(D)J dataset
//     upstream to supply the clonotype sets, and the samples-and-data chain this file uses cannot
//     produce one. The verdict logic is covered by the Python suite instead
//     (software/per-cell-metrics/test/, 259 tests) and by the Tengo suite for the p-column specs
//     (workflow/src/*.test.tengo). Neither substitutes for a live run, which is why the block is
//     verified by hand against software/test-data/fixtures/verdicts/ before release.
//
// Upstream chain follows the proven samples-and-data FASTQ pattern (blocks/mixcr-amplicon-alignment).
// The tag->feature CSV is a direct upload: set as the block arg `tagFeatureCsvHandle` via a local file
// handle; the workflow imports it with file.importFile and shares the blob across the per-sample bodies.
//
// Golden (decoded from test/assets/fb_small_R{1,2}.fastq.gz; geometry CELL 16 + UMI 10 on R1, feature
// 15 on R2; tags.csv: 15xG -> AGX, 15xC -> BGX):
//   read0: cell ACGTACGTACGTACGT, UMI AAAAAAAAAA, feature AGX
//   read1: cell ACGTACGTACGTACGT, UMI AAAAAAAAAC, feature AGX
//   read2: cell ACGTACGTACGTACGT, UMI AAAAAAAAAG, feature BGX
//   read3: cell TGCATGCATGCATGCA, UMI TTTTTTTTTT, feature AGX
// distinct-UMI counts per (cell, feature):
//   (cellA, AGX) = 2, (cellA, BGX) = 1, (cellB, AGX) = 1  -> 3 rows, umiCount column = {1, 1, 2}.

type TableOutput = {
  ok: boolean;
  value: { fullTableHandle: string; visibleTableHandle: string } | undefined;
};

blockTest("empty inputs", { timeout: 20000 }, async ({ rawPrj: project, expect }) => {
  const blockId = await project.addBlock("Block", myBlockSpec);
  const stableState = (await awaitStableState(
    project.getBlockState(blockId),
    15000,
  )) as InferBlockState<typeof platforma>;
  // With no upstream FASTQ column in the pool the option lists are empty (args() throws, disabling
  // Run, but outputs still resolve).
  expect(stableState.outputs).toMatchObject({
    fastqOptions: { ok: true, value: [] },
    datasetOptions: { ok: true, value: [] },
  });

  // Every output a page reads must RESOLVE on a freshly added block, before anything has run. An
  // output that throws here is not a failed computation — it breaks the page that reads it at the
  // moment the block is created, which is the first thing a user sees. This block gained five table
  // outputs and a run-record output on the verdict branch, each guarded by its own
  // undefined-until-computed path, so the guards are what this asserts. Values are deliberately not
  // asserted: `ok` with an undefined value is the correct empty-state answer for all of them.
  const mustResolve = [
    "perCellTable",
    "qcSummaryTable",
    "verdictTable",
    "antigenQcTable",
    "antigenPanelMismatchTable",
    "verdictRunMeta",
    "isRunning",
    "started",
  ] as const;
  const unresolved = mustResolve.filter((name) => stableState.outputs?.[name]?.ok !== true);
  expect(unresolved, "outputs that failed to resolve on an empty block").toEqual([]);
});

// Level-4 end-to-end run against the published mitool (software-mitool 2.3.1-129-main, carrying the
// FEATURE tag type #86 + tag-stat -u #84). Exercises the full per-sample chain: SND FASTQ upload,
// fastqOptions, args derivation, the tag->feature CSV upload (driven by prerun.tpl), the mitool
// parse -> refine-tags -> tag-stat -u exec chain, the per-cell-metrics Python, and the processColumn
// export emitting pl7.app/feature/umiCount.
//
// SKIPPED (2026-07-06): hangs on the CI / run-platforma FS-storage backend. The tag->feature CSV is a
// DIRECT upload consumed by file.importFile (prerun.tpl + main.tpl), and raw file.importFile of a local
// handle never finalizes on that backend — so the prerun's `csvColumns` never resolves and
// awaitStableState aborts with `field_not_resolved:csvColumns`. This is a test-backend limitation, not
// a block bug: the block runs correctly against a real backend (driven live). No block e2e-tests this
// direct-upload path — every block that consumes a file.importFile handle (immune-assay-data, blast,
// makeblastdb, antibody-sequence-liabilities) ships without block tests, and the Samples & Data upstream
// chain (the one CI-working file-input pattern) cannot supply a direct CSV upload. Re-enable if the
// backend gains a working local file.importFile, or if the CSV moves to a pool column.
blockTest.skip(
  "feature integration end-to-end emits per-cell umiCount",
  { timeout: 300000 },
  async ({ rawPrj: project, ml, helpers, expect }) => {
    const sndBlockId = await project.addBlock("Samples & Data", samplesAndDataBlockSpec);
    const fiBlockId = await project.addBlock("Feature Integration", myBlockSpec);

    const sample1Id = uniquePlId();
    const dataset1Id = uniquePlId();

    const r1Handle = await helpers.getLocalFileHandle("./assets/fb_small_R1.fastq.gz");
    const r2Handle = await helpers.getLocalFileHandle("./assets/fb_small_R2.fastq.gz");

    // Upstream: a single-sample paired-FASTQ dataset.
    await project.setBlockArgs(sndBlockId, {
      metadata: [],
      sampleIds: [sample1Id],
      sampleLabelColumnLabel: "Sample Name",
      sampleLabels: { [sample1Id]: "Sample 1" },
      datasets: [
        {
          id: dataset1Id,
          label: "Dataset 1",
          content: {
            type: "Fastq",
            readIndices: ["R1", "R2"],
            gzipped: true,
            data: {
              [sample1Id]: {
                R1: r1Handle,
                R2: r2Handle,
              },
            },
          },
        },
      ],
    } satisfies SamplesAndDataBlockArgs);
    await project.runBlock(sndBlockId);
    await helpers.awaitBlockDone(sndBlockId, 30000);

    const sndStableState = await helpers.awaitBlockDoneAndGetStableBlockState(sndBlockId, 30000);
    expect(sndStableState.outputs).toMatchObject({
      fileImports: {
        ok: true,
        value: { [r1Handle]: { done: true }, [r2Handle]: { done: true } },
      },
    });

    // The feature-integration block sees the FASTQ column once SND is done.
    const fiState1 = (await awaitStableState(
      project.getBlockState(fiBlockId),
      30000,
    )) as InferBlockState<typeof platforma>;
    expect(fiState1.outputs).toMatchObject({
      fastqOptions: { ok: true, value: [{ label: "Dataset 1" }] },
    });
    const fiOutputs1 = wrapOutputs(fiState1.outputs);

    // The tag->feature CSV is a direct upload (not a pool ref). Provision it as a local file handle.
    const csvHandle = await helpers.getLocalFileHandle("./assets/tags.csv");

    // Configure the block. update-block-data must carry EVERY BlockArgsValid field, else the backend
    // reports "currentArgs not set". controlFeature is optional (no negative-control marker here), and
    // so is datasetRef: this run has no single-cell V(D)J dataset, so it exercises the counting half
    // alone. The reading's numeric parameters are required and carry the shipped defaults, the same
    // values a freshly created block starts with.
    //
    // What a datasetless run emits BESIDES the per-cell table is deliberately not asserted. Today the
    // whole antigen stage is skipped, so nothing antigen-related is produced; the spec's qc-measurement
    // set requires the eight read-and-panel measurements and the panel mismatch report to survive a run
    // with no cell list, marking the rest not-evaluated. That gap is open (decision log O-4). Asserting
    // today's behaviour would have to be deleted to fix it, so this test asserts only what both
    // readings agree on.
    await project.mutateBlockStorage(fiBlockId, {
      operation: "update-block-data",
      value: {
        fbFastqRef: fiOutputs1.fastqOptions[0].ref,
        tagFeatureCsvHandle: csvHandle,
        presetId: "tenx-beam", // 10x 5' v2 BEAM geometry (16/10/15); pattern owned by the preset
        countFloor: 4,
        boundCutoff: 75,
        minVotingCells: 1,
        panelReferenceMinMembers: 8,
        referenceThinLine: 2,
        highReferenceLine: 100,
        tableState: createPlDataTableStateV2(),
        qcSummaryTableState: createPlDataTableStateV2(),
        verdictTableState: createPlDataTableStateV2(),
        antigenQcTableState: createPlDataTableStateV2(),
        panelMismatchTableState: createPlDataTableStateV2(),
      } satisfies BlockData,
    });

    // Let args derive before running.
    await awaitStableState(project.getBlockState(fiBlockId), 30000);

    await project.runBlock(fiBlockId);
    const fiState3 = await helpers.awaitBlockDoneAndGetStableBlockState(fiBlockId, 250000);

    const tableOutput = fiState3.outputs!["perCellTable"] as TableOutput;
    expect(tableOutput.ok).toBe(true);
    expect(tableOutput.value).toBeDefined();

    const pFrameDriver = ml.driverKit.pFrameDriver;
    const fullHandle = tableOutput.value!.fullTableHandle as PTableHandle;

    // perCellTable is COLLAPSED to one row per cell [sampleId, cellId] (DECISION 2026-07-02): the
    // per-(cell,feature) matrix — (cellA,AGX)=2, (cellA,BGX)=1, (cellB,AGX)=1 — becomes 2 rows: cellA,
    // cellB. (The per-feature matrix is still exported to the pool, just not in this table.)
    const shape = await pFrameDriver.getShape(fullHandle);
    expect(shape.rows).toBe(2);

    const indices = Array.from({ length: shape.columns }, (_, i) => i);
    const data = await pFrameDriver.getData(fullHandle, indices);

    // The only Int column is pl7.app/feature/maxUmiCount (the cell's largest per-feature UMI count):
    // cellA max(2,1)=2, cellB=1. maxFraction is a Double column, so it is not counted here. Order is
    // unspecified -> compare sorted.
    const maxUmiColumns = data.filter((c) => c.type === "Int");
    expect(maxUmiColumns).toHaveLength(1);
    const maxUmiCounts = [...maxUmiColumns[0].data].map(Number).sort((a, b) => a - b);
    expect(maxUmiCounts).toEqual([1, 2]);
    expect(maxUmiCounts.reduce((a, b) => a + b, 0)).toBe(3);

    // The cell's largest per-feature share of its own total: cellA 2/3, cellB 1/1. This is the
    // surviving per-cell magnitude — it says how concentrated a cell's counts were, not which antigen
    // it bound, so it is not a binding level and does not fall under the no-ordering prohibition.
    const fractionColumns = data.filter((c) => c.type === "Double");
    expect(fractionColumns).toHaveLength(1);
    const maxFractions = [...fractionColumns[0].data].map(Number).sort((a, b) => a - b);
    expect(maxFractions[0]).toBeCloseTo(2 / 3, 5);
    expect(maxFractions[1]).toBeCloseTo(1, 5);

    // The dominant-feature call is gone and must not come back through this table. It answered a
    // different question from the four-state verdict — one antigen per cell, chosen by a share
    // threshold — and reintroducing it beside a verdict would give a reader two disagreeing answers
    // with no rule for which wins. `guardNoScore` in column-specs.lib.tengo refuses the score
    // annotation at build time; this is the same claim checked against what actually reached a table.
    const stringColumnValues = data.filter((c) => c.type === "String").map((c) => [...c.data]);
    expect(
      stringColumnValues.some((vals) => vals.every((v) => v === "AGX")),
      "a per-cell column is calling one dominant feature — consensusFeature has returned",
    ).toBe(false);
  },
);
