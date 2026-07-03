import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
import { computed } from "vue";
import { useApp } from "./app";
import { parseProgressString } from "./parseProgress";

// Progress-cell config per sample. Maps onto the Progress column cell: status → stage, percent → bar
// fill (undefined = indeterminate), text → main label, suffix → right-hand note. Shape matches the
// SDK's ColDefProgress, so the column's `progress` callback passes it through unchanged.
export type ProgressCell = {
  status: "not_started" | "running" | "done";
  percent?: number;
  text: string;
  suffix?: string;
};

export type SampleResult = {
  sampleId: string;
  label: string;
  progress: ProgressCell;
};

// The framework resolves the FutureRefs inside sampleProgress / stepProgress on serialization, so each
// entry's value arrives as this shape (or undefined). Cast mirrors blocks/peptide-extraction results.ts.
type ProgressInfo = { progressLine?: string; live: boolean };

// Ordinal step key (from the workflow stepLogs axis) → friendly display name. Only the mitool steps
// emit progress; qc/metrics (Python) are not tracked — completion comes from completedSamples.
const STEP_NAMES: Record<string, string> = {
  "1-parse": "Parsing reads",
  "2-refine": "Refining barcodes",
  "3-tagstat": "Counting UMIs",
};

// Steps whose mitool progress isn't monotonic — refine-tags runs several internal passes (CELL /
// FEATURE / UMI / writing), each counting 0→100%, so its live percent visibly jumps up and down.
// Show these as an indeterminate (animated) bar instead of a jumpy number.
const INDETERMINATE_STEPS = new Set(["2-refine"]);

export const sampleResults = computed<SampleResult[] | undefined>(() => {
  const app = useApp();
  const roster = app.model.outputs.sampleProgress;
  // undefined until the roster is enumerated — the grid shows its loading overlay in the meantime.
  if (!roster) return undefined;

  const stepProg = app.model.outputs.stepProgress;
  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const labels = app.model.outputs.sampleLabels ?? {};

  const sampleIds = new Set<string>();
  for (const e of roster.data) sampleIds.add(String(e.key[0]));

  // Latest step per sample: prefer a currently-live stream, else the highest-ordinal step seen. Step
  // keys sort lexicographically ("1-…" < "2-…" < "3-…"), so string compare gives step order.
  const latest = new Map<string, { step: string; info: ProgressInfo }>();
  const consider = (sampleId: string, step: string, info: ProgressInfo | undefined) => {
    if (!info) return;
    const cur = latest.get(sampleId);
    if (!cur || (info.live && !cur.info.live) || (info.live === cur.info.live && step > cur.step)) {
      latest.set(sampleId, { step, info });
    }
  };
  if (stepProg) {
    for (const e of stepProg.data) {
      consider(String(e.key[0]), String(e.key[1]), e.value as ProgressInfo | undefined);
    }
  }

  return [...sampleIds]
    .map((sampleId): SampleResult => {
      const label = labels[sampleId] ?? sampleId;

      // Whole sample finished.
      if (completed.has(sampleId)) {
        return { sampleId, label, progress: { status: "done", percent: 100, text: "Done" } };
      }

      const cur = latest.get(sampleId);
      if (!cur) {
        return { sampleId, label, progress: { status: "not_started", text: "Queued" } };
      }

      const name = STEP_NAMES[cur.step] ?? cur.step;

      // Step actively streaming → live percent + ETA in the right-hand note.
      if (cur.info.live && cur.info.progressLine) {
        // Non-monotonic steps (refine-tags): indeterminate bar so the percent doesn't jump around.
        // Blank suffix — otherwise the progress cell defaults the right-hand note to "0%".
        if (INDETERMINATE_STEPS.has(cur.step)) {
          return { sampleId, label, progress: { status: "running", text: name, suffix: "" } };
        }
        const p = parseProgressString(cur.info.progressLine.replace(ProgressPrefix, ""));
        if (p.percentage) {
          return {
            sampleId,
            label,
            progress: {
              status: "running",
              percent: Number(p.percentage),
              text: `${name}: ${p.percentage}%`,
              suffix: p.etaLabel ?? "",
            },
          };
        }
        // Streaming but no parseable percent (e.g. mitool's "∞%" line) → indeterminate bar (blank
        // suffix so the cell doesn't default the right-hand note to "0%").
        return { sampleId, label, progress: { status: "running", text: name, suffix: "" } };
      }

      // Step finished streaming but the sample isn't done yet — show that step as complete: a full bar
      // with a "Done" note, keeping the step name. The next step resets the bar when it starts emitting.
      return {
        sampleId,
        label,
        progress: { status: "running", percent: 100, text: name, suffix: "Done" },
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
