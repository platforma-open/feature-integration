import type { SampleStep } from "@platforma-open/milaboratories.feature-integration.model";
import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
import { parseProgressString } from "./parseProgress";

// Progress-cell config for the Main grid's Progress column. Maps onto the SDK's ColDefProgress:
// status → stage, percent → bar fill (undefined = indeterminate), text → label, suffix → right-hand text
// (set "" to suppress the SDK's default "0%" on an indeterminate bar).
export type ProgressCell = {
  status: "not_started" | "running" | "done";
  percent?: number;
  text: string;
  suffix?: string;
};

// The per-sample pipeline runs four stages: parse → refine → tag-stat → per-cell metrics (Python). Each
// stage owns a quarter-band of the overall bar (parse 0–25, refine 25–50, tag-stat 50–75, metrics
// 75–100). A stage's own live % fills WITHIN its band; indeterminate phases hold at the band floor. The
// band floor comes from the deterministic sampleStep (report presence, which only advances), so the bar
// is MONOTONIC — it never resets to zero when a new step starts (the pre-scrap version drove the full bar
// per step, which caused that reset). The rich per-step text still comes from the live mitool stdout.
const STEP_ORDINAL: Record<SampleStep, number> = {
  parsing: 0,
  refining: 1,
  counting: 2,
  metrics: 3,
};
const TOTAL_STEPS = 4;
const BAND = 100 / TOTAL_STEPS;

// sampleStep (the stage a sample is currently on) → its workflow stepLogs key, so we can pull that step's
// live progress line. metrics is the Python step and emits no live stream, so it has no key.
const STEP_TO_WF: Record<SampleStep, string | undefined> = {
  parsing: "1-parse",
  refining: "2-refine",
  counting: "3-tagstat",
  metrics: undefined,
};

// Fallback step label (used when no live line is available for the current step yet).
const STEP_LABEL: Record<SampleStep, string> = {
  parsing: "Parsing reads",
  refining: "Refining barcodes",
  counting: "Counting UMIs",
  metrics: "Computing metrics",
};

// mitool progress prose per step. Returns the display text + suffix, plus a localPercent (0–100 WITHIN
// the step) ONLY for monotonic phases — the caller maps that into the step's band. Indeterminate phases
// (refine correction passes, tag-stat on-disk sort) return no localPercent so the bar holds at the band
// floor rather than bouncing.
type StepDisplay = { text: string; suffix: string; localPercent?: number };

// refine-tags corrects CELL → FEATURE → UMI (fb-pipeline passes -t CELL -t FEATURE -t UMI; mitool orders
// them CELL < FEATURE < UMI). Each tag's correction is recursive/non-monotonic, so we surface WHICH tag
// is in progress ("2 of 3") on an indeterminate bar rather than a jumpy %.
const REFINE_TAGS = ["CELL", "FEATURE", "UMI"];
const REFINE_TAG_LABELS: Record<string, string> = {
  CELL: "Cell barcodes",
  FEATURE: "Feature barcodes",
  UMI: "UMIs",
};
const REFINE_INIT_LABEL = /init/i;
const TAGSTAT_WRITING_LABEL = /writing/i;
// tag-stat runs a hierarchical on-disk sort ("Sorting records, step N of M: X%") followed by one final
// "Writing result: X%" pass. Each sub-phase is monotonic 0→100% on its own; naively showing that % makes
// the bar bounce M+1 times. Instead we compose them into one monotonic fill: the sort passes share a
// fixed leading portion of the step (0 → SORT_PORTION%), distributed dynamically across whatever M mitool
// reports, and the write pass owns the remainder (SORT_PORTION → 100%). This is M-independent — sort step
// M always ends at SORT_PORTION and the write starts there, so the bar never jumps or steps back no
// matter how many sort passes run (the "Writing result" line carries no M, so we cannot derive it there).
const TAGSTAT_SORT_LABEL = /step\s+(\d+)\s+of\s+(\d+)/i;
const TAGSTAT_SORT_PORTION = 80;

function stepDisplay(
  step: string,
  stage: string,
  percentage?: string,
  etaLabel?: string,
): StepDisplay {
  if (step === "2-refine") {
    const tag = REFINE_TAGS.find((t) => stage.includes(t));
    if (tag) {
      return {
        text: `Refining barcodes: ${REFINE_TAG_LABELS[tag]}`,
        suffix: `${REFINE_TAGS.indexOf(tag) + 1} of ${REFINE_TAGS.length}`,
      };
    }
    // Global phases keep the stable "Refining barcodes" prefix so the label doesn't jump. mitool's
    // lead-in is "Initialization"; the wrap-up stages collapse to ": Finalizing".
    if (REFINE_INIT_LABEL.test(stage)) return { text: "Refining barcodes", suffix: "" };
    return { text: "Refining barcodes: Finalizing", suffix: "" };
  }

  if (step === "3-tagstat") {
    const pct = percentage ? Number(percentage) : 0;
    // Sort pass "step N of M": sub-phase N of (M + 1). Fill within its slice of the tag-stat band.
    const sort = stage.match(TAGSTAT_SORT_LABEL);
    if (sort) {
      const n = Number(sort[1]);
      const m = Number(sort[2]);
      // Sort passes share the leading SORT_PORTION; pass M at 100% lands exactly at SORT_PORTION.
      return {
        text: `Counting UMIs: sorting ${n}/${m}${percentage ? ` — ${Math.round(pct)}%` : ""}`,
        suffix: etaLabel ?? "",
        localPercent: ((n - 1 + pct / 100) / m) * TAGSTAT_SORT_PORTION,
      };
    }
    // Final "Writing result" pass owns the remainder (SORT_PORTION → 100%), so it always continues from
    // where the sort ended, independent of how many sort passes ran.
    if (TAGSTAT_WRITING_LABEL.test(stage)) {
      return {
        text: `Counting UMIs: writing${percentage ? ` — ${Math.round(pct)}%` : ""}`,
        suffix: etaLabel ?? "",
        localPercent: TAGSTAT_SORT_PORTION + (pct / 100) * (100 - TAGSTAT_SORT_PORTION),
      };
    }
    return { text: "Counting UMIs", suffix: "" };
  }

  // parse (and any other monotonic step): show the live percent when present, else the bare stage name.
  if (percentage) {
    return {
      text: `Parsing reads: ${percentage}%`,
      suffix: etaLabel ?? "",
      localPercent: Number(percentage),
    };
  }
  return { text: "Parsing reads", suffix: "" };
}

// Progress cell for one sample. Done once its QC settles (completedSamples). Otherwise the band floor
// comes from the deterministic sampleStep (monotonic), and — if the current step is streaming a live
// line — the rich mitool prose fills the text and (for monotonic phases) the within-band bar.
export function deriveProgress(
  sampleId: string,
  completed: Set<string>,
  sampleStep: Record<string, SampleStep> | undefined,
  liveLineForCurrentStep?: string,
): ProgressCell {
  if (completed.has(sampleId)) return { status: "done", percent: 100, text: "Done" };

  const step = sampleStep?.[sampleId] ?? "parsing";
  const bandFloor = STEP_ORDINAL[step] * BAND;
  const wfStep = STEP_TO_WF[step];

  if (wfStep && liveLineForCurrentStep) {
    const parsed = parseProgressString(liveLineForCurrentStep.replace(ProgressPrefix, ""));
    const d = stepDisplay(wfStep, parsed.stage ?? "", parsed.percentage, parsed.etaLabel);
    // Within-band fill from the step's own monotonic %; indeterminate phases hold at the floor.
    const frac =
      d.localPercent !== undefined && !Number.isNaN(d.localPercent) ? d.localPercent / 100 : 0;
    return {
      status: "running",
      percent: Math.round(bandFloor + frac * BAND),
      text: d.text,
      suffix: d.suffix,
    };
  }

  // No live line for the current step yet (or the metrics/Python step, which emits none) — hold the bar
  // at the band floor with the step name. metrics sits at 75% through the whole (slow) Python run.
  return { status: "running", percent: Math.round(bandFloor), text: STEP_LABEL[step], suffix: "" };
}
