import { ProgressPrefix } from "@platforma-open/milaboratories.feature-integration.model";
import { computed } from "vue";
import { useApp } from "./app";
import { parseProgressString } from "./parseProgress";

// One row of the Main-page loading grid. Fields map directly onto PlProgressCell props.
export type SampleProgressRow = {
  sampleId: string;
  label: string;
  stage: "not_started" | "running" | "done";
  step: string; // left text
  progressString: string; // right text (percent / ETA)
  percent?: number; // bar fill 0-100; undefined = indeterminate (animated) bar
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

export const sampleResults = computed<SampleProgressRow[] | undefined>(() => {
  const app = useApp();
  const roster = app.model.outputs.sampleProgress;
  if (!roster) return undefined; // roster not yet enumerated (inputs still locking)

  const stepProg = app.model.outputs.stepProgress;
  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const labels = app.model.outputs.sampleLabels ?? {};

  // Full roster (every sample shows at once, "Queued" until its first step emits).
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
    .map((sampleId): SampleProgressRow => {
      const label = labels[sampleId] ?? sampleId;

      if (completed.has(sampleId)) {
        return { sampleId, label, stage: "done", step: "Done", progressString: "" };
      }

      const cur = latest.get(sampleId);
      if (!cur) {
        return { sampleId, label, stage: "not_started", step: "Queued", progressString: "" };
      }

      const name = STEP_NAMES[cur.step] ?? cur.step;

      // Step is actively streaming a progress line → show its stage + percent + ETA (determinate bar).
      if (cur.info.live && cur.info.progressLine) {
        const parsed = parseProgressString(cur.info.progressLine.replace(ProgressPrefix, ""));
        const percent = parsed.percentage ? Number(parsed.percentage) : undefined;
        const right = [parsed.percentage ? `${parsed.percentage}%` : "", parsed.etaLabel ?? ""]
          .filter(Boolean)
          .join("  ");
        return { sampleId, label, stage: "running", step: name, progressString: right, percent };
      }

      // Step finished streaming but the sample isn't done — between steps, or a Python step is running.
      // Show the last known stage as indeterminate so the row stays informative, not blank "Processing…".
      return { sampleId, label, stage: "running", step: `${name}…`, progressString: "" };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
