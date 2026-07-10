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

// Progress cell for one sample: Done once its QC settles (completedSamples), else the current step from
// the model's deterministic sampleStep (defaults to "parsing" before any stage report has settled).
export function deriveProgress(
  sampleId: string,
  completed: Set<string>,
  sampleStep: Record<string, SampleStep> | undefined,
): ProgressCell {
  if (completed.has(sampleId)) return { status: "done", percent: 100, text: "Done" };
  const step = sampleStep?.[sampleId] ?? "parsing";
  return { status: "running", text: STEP_LABEL[step], suffix: "" };
}
