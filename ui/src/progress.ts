import {
  ProgressPattern,
  ProgressPrefix,
} from "@platforma-open/milaboratories.feature-integration.model";

// The unwrapped shape of the model's `progress` / `parseProgress` outputs as read from
// app.model.outputs (a resource map, or undefined until it resolves). Value is left `unknown` and
// narrowed at the call site — only progressLine / live are needed.
type ProgressResource = { data: { key: (string | number)[]; value?: unknown }[] } | undefined;

// Progress-cell config for the Main grid's Progress column. Maps onto the SDK's ColDefProgress:
// status → stage, percent → bar fill (undefined = indeterminate), text → label, suffix → right-hand text
// (set "" to suppress the SDK's default "0%" on an indeterminate bar).
export type ProgressCell = {
  status: "not_started" | "running" | "done";
  percent?: number;
  text: string;
  suffix?: string;
};

// mitool step key -> friendly progress label. qc / per-cell-metrics are Python steps with no progress
// stream, so their completion comes from completedSamples (qcJson), not a step log.
export const STEP_LABEL: Record<string, string> = {
  "1-parse": "Parsing reads",
  "2-refine": "Refining barcodes",
  "3-tagstat": "Counting UMIs",
};

// The last step that emits a progress stream; once it finishes, the Python metrics step runs (no stream).
const LAST_LOGGED_STEP = "3-tagstat";

// The latest active step for a sample, unified across the per-step (progress) and flat-parse
// (parseProgress) sources.
type StepInfo = { step: string; progressLine?: string; live: boolean };

// Percent from a mitool progress line (e.g. "Parsing: 42%"), or undefined for an indeterminate stage.
function progressPercent(line: string): number | undefined {
  const m = line.replace(ProgressPrefix, "").match(ProgressPattern);
  const n = m?.groups?.progress === undefined ? NaN : Number(m.groups.progress);
  return Number.isFinite(n) ? n : undefined;
}

// Unify the two progress sources into sampleId -> latest active step. parseProgress is the flat 1-parse
// stream (fires early); progress is the per-[sampleId, step] map (parse / refine / tag-stat). Prefer a
// live step, then the higher step number, so a sample shows its furthest active stage.
export function buildProgressMap(
  progress: ProgressResource,
  parseProgress: ProgressResource,
): Map<string, StepInfo> {
  const progressMap = new Map<string, StepInfo>();
  const consider = (
    sampleId: string,
    step: string,
    value?: { progressLine?: string; live: boolean },
  ) => {
    if (!value) return;
    const cur = progressMap.get(sampleId);
    const better =
      !cur || (value.live && !cur.live) || (value.live === cur.live && step > cur.step);
    if (better)
      progressMap.set(sampleId, { step, progressLine: value.progressLine, live: value.live });
  };
  type ProgressValue = { progressLine?: string; live: boolean };
  if (parseProgress)
    for (const p of parseProgress.data)
      consider(String(p.key[0]), "1-parse", p.value as ProgressValue | undefined);
  if (progress)
    for (const p of progress.data)
      consider(String(p.key[0]), String(p.key[1]), p.value as ProgressValue | undefined);
  return progressMap;
}

// Progress cell for one sample: Done once its QC settles, else the current mitool step (live % when the
// step reports one, "Computing metrics" once tag-stat is done, "Processing…" before any step logs).
export function deriveProgress(
  sampleId: string,
  completed: Set<string>,
  progressMap: Map<string, StepInfo>,
): ProgressCell {
  if (completed.has(sampleId)) return { status: "done", percent: 100, text: "Done" };
  const info = progressMap.get(sampleId);
  if (!info) return { status: "running", text: "Processing…", suffix: "" };
  if (info.live && info.progressLine) {
    const text = STEP_LABEL[info.step] ?? "Processing";
    const percent = progressPercent(info.progressLine);
    return percent === undefined
      ? { status: "running", text, suffix: "" }
      : { status: "running", text, percent };
  }
  if (!info.live && info.step >= LAST_LOGGED_STEP)
    return { status: "running", text: "Computing metrics", suffix: "" };
  return { status: "running", text: STEP_LABEL[info.step] ?? "Processing…", suffix: "" };
}
