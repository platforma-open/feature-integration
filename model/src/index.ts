import type { InferOutputsType } from "@platforma-sdk/model";
import {
  ArrayColumnProvider,
  BlockModelV3,
  createPlDataTableStateV2,
  createPlDataTableV3,
  DataModelBuilder,
  isPColumnSpec,
} from "@platforma-sdk/model";
import type { BlockArgs, BlockData } from "./types";

const DOMINANCE_FLOOR = 0.5; // spec A-0012: threshold is user-adjustable down to 0.5, never lower

const dataModel = new DataModelBuilder()
  .from<BlockData>("v1")
  .init(() => ({ dominanceThreshold: 0.6, tableState: createPlDataTableStateV2() }));

export const platforma = BlockModelV3.create(dataModel)
  .args((data): BlockArgs => {
    if (!data.fbFastqRef) throw new Error("Select the feature-barcode FASTQ");
    if (!data.tagFeatureCsvRef) throw new Error("Select the tag→feature CSV");
    return {
      fbFastqRef: data.fbFastqRef,
      tagFeatureCsvRef: data.tagFeatureCsvRef,
      controlFeature: data.controlFeature,
      // canonicalize + clamp to the 0.5 floor
      dominanceThreshold: Math.max(DOMINANCE_FLOOR, data.dominanceThreshold ?? 0.6),
    };
  })
  .prerunArgs((data) => ({
    fbFastqRef: data.fbFastqRef,
    tagFeatureCsvRef: data.tagFeatureCsvRef,
  }))
  // feature-barcode FASTQ options (file-valued sequencing columns, fastq / fastq.gz)
  .output("fastqOptions", (ctx) =>
    ctx.resultPool.getOptions((spec) => {
      if (!isPColumnSpec(spec)) return false;
      const ext = spec.domain?.["pl7.app/fileExtension"];
      return (
        spec.name === "pl7.app/sequencing/data" &&
        (spec.valueType as string) === "File" &&
        (ext === "fastq" || ext === "fastq.gz")
      );
    }),
  )
  // tag->feature CSV options
  .output("csvOptions", (ctx) =>
    ctx.resultPool.getOptions(
      (spec) => isPColumnSpec(spec) && spec.domain?.["pl7.app/fileExtension"] === "csv",
    ),
  )
  // Negative-control dropdown: the antigens defined in the chosen CSV (spec A-0014). Populated once
  // the workflow emits the feature names (plan Task 4); empty until then.
  .output("controlOptions", (_ctx): { value: string; label: string }[] => [])
  // Per-cell results table (latest builder: createPlDataTableV3). Resolves the workflow's exported
  // perCellTable PFrame; undefined until the workflow (plan Tasks 3-4) emits it, so the UI guards it.
  .outputWithStatus("perCellTable", (ctx) => {
    const cols = ctx.outputs?.resolve("perCellTable")?.getPColumns();
    if (!cols) return undefined;
    return createPlDataTableV3(ctx, {
      columns: new ArrayColumnProvider(cols)
        .getAllColumns()
        .map((column) => ({ column, isPrimary: true })),
      tableState: ctx.data.tableState,
    });
  })
  .title(() => "Feature Integration")
  .sections(() => [{ type: "link" as const, href: "/" as const, label: "Main" }])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
