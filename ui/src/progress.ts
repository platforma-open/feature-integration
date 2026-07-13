import type { SampleStep } from "@platforma-open/milaboratories.feature-integration.model";

// Progress-cell config for the Main grid's Progress column. Maps onto the SDK's ColDefProgress:
// status → stage, percent → bar fill (undefined = indeterminate), text → label, suffix → right-hand text
// (set "" to suppress the SDK's default "0%" on an indeterminate bar).
export type ProgressCell = {
  status: "not_started" | "running" | "done";
  percent?: number;
  text: string;
  suffix?: string;
};

// Display label per current step. The mitool steps run fast and report no live %, so the cell shows the
// step name with an indeterminate bar; completion (Done) comes from completedSamples.
const STEP_LABEL: Record<SampleStep, string> = {
  parsing: "Parsing reads",
  refining: "Refining barcodes",
  counting: "Counting UMIs",
  metrics: "Computing metrics",
};

// The per-sample pipeline runs four stages: parse -> refine -> tag-stat -> per-cell metrics (the final
// Python). The counter shows the current stage out of this fixed total (mirrors peptide-extraction's
// hard-coded "/6"). The three mitool stages report a settled step file each; the metrics stage is the
// step reached once the tag-stat report settles, so the counter advances into it and reads "[4/4]" for
// the whole (slow) Python run — no live sub-progress is available (mitool/Python emit none, and capturing
// their stdout would reintroduce the CIDConflictError this block deliberately avoids; see
// workflow/src/fb-parse.tpl.tengo). The bar fills by completed stages so it sits in its last quarter
// during metrics, reading as "on the final stage" rather than an indeterminate hang.
const STEP_ORDINAL: Record<SampleStep, number> = {
  parsing: 1,
  refining: 2,
  counting: 3,
  metrics: 4,
};
const TOTAL_STEPS = 4;

// Progress cell for one sample: Done once its QC settles (completedSamples), else an "[N/M] <stage>"
// counter for the current step from the model's deterministic sampleStep (defaults to "parsing" before
// any stage report has settled).
export function deriveProgress(
  sampleId: string,
  completed: Set<string>,
  sampleStep: Record<string, SampleStep> | undefined,
): ProgressCell {
  if (completed.has(sampleId)) return { status: "done", percent: 100, text: "Done" };
  const step = sampleStep?.[sampleId] ?? "parsing";
  const n = STEP_ORDINAL[step];
  return {
    status: "running",
    // Fill by completed stages: parsing 0% -> refining 25% -> counting 50% -> metrics 75% -> Done 100%.
    percent: Math.round(((n - 1) / TOTAL_STEPS) * 100),
    text: `[${n}/${TOTAL_STEPS}] ${STEP_LABEL[step]}`,
    suffix: "",
  };
}
