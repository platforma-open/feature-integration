import type {
  BlockData,
  platforma,
} from "@platforma-open/milaboratories.feature-integration.model";
import { awaitStableState, blockTest } from "@platforma-sdk/test";
import { blockSpec as samplesAndDataBlockSpec } from "@platforma-open/milaboratories.samples-and-data";
import type { BlockArgs as SamplesAndDataBlockArgs } from "@platforma-open/milaboratories.samples-and-data.model";
import { uniquePlId } from "@platforma-open/milaboratories.samples-and-data.model";
import { blockSpec as myBlockSpec } from "this-block";
import type { InferBlockState, PTableHandle } from "@platforma-sdk/model";
import { createPlDataTableStateV2, wrapOutputs } from "@platforma-sdk/model";

// Level-4 integration test (plan Task 7): a live end-to-end run emitting `pl7.app/feature/umiCount`.
//
// Upstream chain follows the proven samples-and-data FASTQ pattern (blocks/mixcr-amplicon-alignment).
// The tag->feature CSV is a direct upload (M7 resolution): set as the block arg `tagFeatureCsvHandle`
// via a local file handle; the workflow imports it with file.importFile and shares the blob across the
// per-sample bodies.
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
  // With no upstream FASTQ column in the pool the option list is empty (args() throws, disabling Run,
  // but outputs still resolve).
  expect(stableState.outputs).toMatchObject({ fastqOptions: { ok: true, value: [] } });
});

// SKIPPED pending mitool PR #84 (`tag-stat -u`) being published.
//
// The block currently consumes mitool via a DEV-ONLY local `file:` override (an unpublished build with
// `tag-stat -u`). The per-sample mitool exec is dispatched from a dev-local software package, and the
// prebuilt local backend used by `run-platforma.sh` stalls assembling the exec workdir for local-path
// dev software (the command never runs; block stays `Running` with no error). Published-software blocks
// (mixcr-clonotyping, peptide-extraction) run their mitool execs fine on the same backend, so this is
// specific to the local override — not the block or this test. Everything up to the exec is verified
// live: SND FASTQ chain, fastqOptions, args derivation, the CSV upload (driven by prerun.tpl), and the
// processColumn + export structure all build. Once #84 publishes, drop the override in the root
// package.json `pnpm.overrides`, bump the `software-mitool` catalog version, and un-skip this test.
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
    // reports "currentArgs not set". controlFeature is optional (no specificity score here).
    await project.mutateBlockStorage(fiBlockId, {
      operation: "update-block-data",
      value: {
        fbFastqRef: fiOutputs1.fastqOptions[0].ref,
        tagFeatureCsvHandle: csvHandle,
        dominanceThreshold: 0.6,
        cellLen: 16,
        umiLen: 10,
        featureLen: 15,
        tableState: createPlDataTableStateV2(),
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

    // One row per (cell, feature): (cellA,AGX), (cellA,BGX), (cellB,AGX).
    const shape = await pFrameDriver.getShape(fullHandle);
    expect(shape.rows).toBe(3);

    const indices = Array.from({ length: shape.columns }, (_, i) => i);
    const data = await pFrameDriver.getData(fullHandle, indices);

    // The only Int column is pl7.app/feature/umiCount. Order is unspecified -> compare sorted.
    const umiColumns = data.filter((c) => c.type === "Int");
    expect(umiColumns).toHaveLength(1);
    const umiCounts = [...umiColumns[0].data].map(Number).sort((a, b) => a - b);
    expect(umiCounts).toEqual([1, 1, 2]);
    expect(umiCounts.reduce((a, b) => a + b, 0)).toBe(4);

    // Consensus feature per cell: cellA dominant AGX (2 of 3 = 0.67 >= 0.6), cellB single-feature AGX.
    // Broadcast across the per-(cell,feature) rows -> every row's consensus is "AGX".
    const stringColumnValues = data.filter((c) => c.type === "String").map((c) => [...c.data]);
    expect(stringColumnValues.some((vals) => vals.every((v) => v === "AGX"))).toBe(true);
  },
);
