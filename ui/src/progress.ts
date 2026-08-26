import type { SampleStep } from "@platforma-open/milaboratories.feature-integration.model";
import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
import { parseProgressString } from "./parseProgress";

// Progress-cell config for the Main grid's Progress column, mapping onto the SDK's ColDefProgress: status
// -> stage, percent -> bar fill where undefined is indeterminate, text -> label, suffix -> right-hand text.
// Set suffix to "" to suppress the SDK's default "0%" on an indeterminate bar.
export type ProgressCell = {
  status: "not_started" | "running" | "done";
  percent?: number;
  text: string;
  suffix?: string;
};

// The per-sample pipeline runs four stages, each owning a quarter-band of the overall bar: parse 0-25,
// refine 25-50, tag-stat 50-75, metrics 75-100. A stage's own live % fills WITHIN its band, and an
// indeterminate phase holds at the band floor. That floor comes from the deterministic sampleStep, which is
// report presence and only advances, so the bar is MONOTONIC. Never drive the full bar per step, which is
// what causes a reset to zero.
const STEP_ORDINAL: Record<SampleStep, number> = {
  parsing: 0,
  refining: 1,
  counting: 2,
  metrics: 3,
};
const TOTAL_STEPS = 4;
const BAND = 100 / TOTAL_STEPS;

// The streaming mitool steps in order, with their bar ordinal. The label follows the FURTHEST of these that
// currently has a live line, NOT the report-derived sampleStep, which advances a beat early: a step's report
// settles before the next step's live stream starts, so the bar flashed the next step's name during that
// gap.
const WF_STEPS = ["1-parse", "2-refine", "3-tagstat", "4-metrics"] as const;
const WF_ORDINAL: Record<string, number> = {
  "1-parse": 0,
  "2-refine": 1,
  "3-tagstat": 2,
  "4-metrics": 3,
};

// Fallback step label, used when no live line is available for the current step yet.
const STEP_LABEL: Record<SampleStep, string> = {
  parsing: "Parsing reads",
  refining: "Refining barcodes",
  counting: "Counting UMIs",
  metrics: "Computing metrics",
};

// mitool progress prose per step. Returns the display text and suffix, plus a localPercent (0-100 WITHIN the
// step) ONLY for monotonic phases -- the caller maps that into the step's band. Indeterminate phases (refine
// correction passes, tag-stat on-disk sort) return no localPercent so the bar holds at the band floor.
type StepDisplay = { text: string; suffix: string; localPercent?: number };

// refine-tags corrects CELL -> FEATURE -> UMI (fb-pipeline passes -t CELL -t FEATURE -t UMI, and mitool
// orders them CELL < FEATURE < UMI). Each tag's correction is recursive and non-monotonic, so surface WHICH
// tag is in progress ("2 of 3") on an indeterminate bar rather than a jumpy %.
const REFINE_TAGS = ["CELL", "FEATURE", "UMI"];
const REFINE_TAG_LABELS: Record<string, string> = {
  CELL: "Cell barcodes",
  FEATURE: "Feature barcodes",
  UMI: "UMIs",
};
const REFINE_INIT_LABEL = /init/i;
const TAGSTAT_WRITING_LABEL = /writing/i;
// tag-stat runs a hierarchical on-disk sort ("Sorting records, step N of M: X%") followed by one final
// "Writing result: X%" pass. Each sub-phase is monotonic 0->100% on its own, and showing that % directly
// makes the bar bounce M+1 times. The sort passes share a fixed leading portion of the step
// (0 -> SORT_PORTION%), distributed across whatever M mitool reports, and the write pass owns the remainder.
// This is M-independent. The "Writing result" line carries no M, so it cannot be derived there.
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
    // Global phases keep the stable "Refining barcodes" prefix so the label does not jump. mitool's lead-in
    // is "Initialization".
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
      // Sort passes share the leading SORT_PORTION, so pass M at 100% lands exactly at SORT_PORTION.
      return {
        text: `Counting UMIs: sorting ${n}/${m}${percentage ? ` — ${Math.round(pct)}%` : ""}`,
        suffix: etaLabel ?? "",
        localPercent: ((n - 1 + pct / 100) / m) * TAGSTAT_SORT_PORTION,
      };
    }
    // The final "Writing result" pass owns the remainder, so it always continues from where the sort ended,
    // independent of how many sort passes ran.
    if (TAGSTAT_WRITING_LABEL.test(stage)) {
      return {
        text: `Counting UMIs: writing${percentage ? ` — ${Math.round(pct)}%` : ""}`,
        suffix: etaLabel ?? "",
        localPercent: TAGSTAT_SORT_PORTION + (pct / 100) * (100 - TAGSTAT_SORT_PORTION),
      };
    }
    return { text: "Counting UMIs", suffix: "" };
  }

  // The Python step names its own phase and carries no ETA: these are whole-frame operations with no
  // iteration count to extrapolate from, and an invented ETA is worse than none.
  if (step === "4-metrics") {
    const pct = percentage ? Number(percentage) : undefined;
    return {
      text: stage ? `Computing metrics: ${stage}` : "Computing metrics",
      suffix: "",
      localPercent: pct,
    };
  }

  // parse, and any other monotonic step: show the live percent when present, else the bare stage name.
  if (percentage) {
    return {
      text: `Parsing reads: ${percentage}%`,
      suffix: etaLabel ?? "",
      localPercent: Number(percentage),
    };
  }
  return { text: "Parsing reads", suffix: "" };
}

// Progress cell for one sample. Done once its QC settles. Otherwise the band floor comes from the
// deterministic sampleStep, and where the current step is streaming a live line the mitool prose fills the
// text and, for monotonic phases, the within-band bar.
//
// `live` matters because a closed stream's last line is HISTORY, not a reading. mitool prints progress on a
// timer and the process usually finishes between ticks, so 97.8% with an ETA of one second is a normal way
// for a finished parse to end. Replayed as if current it reads as a stall.
export type StepStream = { line?: string; live?: boolean };

export function deriveProgress(
  sampleId: string,
  completed: Set<string>,
  sampleStep: Record<string, SampleStep> | undefined,
  liveLines?: Partial<Record<string, StepStream>>,
): ProgressCell {
  if (completed.has(sampleId)) return { status: "done", percent: 100, text: "Done" };

  const step = sampleStep?.[sampleId] ?? "parsing";
  const reportFloor = STEP_ORDINAL[step] * BAND;

  // The label follows the FURTHEST streaming step that actually has a live line, not the report-derived
  // sampleStep, which settles a beat before the next step's live stream starts.
  let liveWf: string | undefined;
  let liveLine: string | undefined;
  let streamOpen = true;
  if (liveLines) {
    for (const wf of WF_STEPS) {
      const stream = liveLines[wf];
      if (stream?.line) {
        liveWf = wf;
        liveLine = stream.line;
        streamOpen = stream.live !== false;
      }
    }
  }

  // Nothing streaming yet: hold at the report floor with the step name. The metrics step used to land here
  // unconditionally and sat at 75% through the whole slow Python run; it now streams like the others, so only
  // the gap before a step's first line reaches this.
  if (liveWf === undefined || liveLine === undefined) {
    return {
      status: "running",
      percent: Math.round(reportFloor),
      text: STEP_LABEL[step],
      suffix: "",
    };
  }

  // A closed stream means that step finished, whatever percentage its last tick happened to carry. Hold at
  // the TOP of its band and drop the label's stale figures: an ETA of one second that never elapses invites a
  // reader to wait for something that already happened.
  if (!streamOpen) {
    const finishedFloor = (WF_ORDINAL[liveWf] + 1) * BAND;
    return {
      status: "running",
      percent: Math.round(Math.max(reportFloor, finishedFloor)),
      text: STEP_LABEL[step],
      suffix: "",
    };
  }

  const parsed = parseProgressString(liveLine.replace(ProgressPrefix, ""));
  const d = stepDisplay(liveWf, parsed.stage ?? "", parsed.percentage, parsed.etaLabel);
  const liveFloor = WF_ORDINAL[liveWf] * BAND;
  const frac =
    d.localPercent !== undefined && !Number.isNaN(d.localPercent) ? d.localPercent / 100 : 0;
  // Bar stays monotonic: never below the report floor, and it tracks the live step's within-band fill.
  const percent = Math.max(reportFloor, liveFloor + frac * BAND);
  return { status: "running", percent: Math.round(percent), text: d.text, suffix: d.suffix };
}
