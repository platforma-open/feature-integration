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

// The framework resolves the FutureRef inside the sampleProgress output on serialization, so each
// entry's value arrives as this shape (or undefined before the sample's parse emits a line). The cast
// mirrors blocks/peptide-extraction results.ts.
type ProgressInfo = { progressLine?: string; live: boolean };

// Per-sample progress rows for the loading grid, derived purely from model outputs:
//   • sampleProgress   — roster (gated on getInputsLocked) + latest live parse progress line per sample
//   • completedSamples — samples whose per-sample pipeline finished (qcJson settled)
//   • sampleLabels     — sampleId -> display name
export const sampleResults = computed<SampleProgressRow[] | undefined>(() => {
  const app = useApp();
  const progress = app.model.outputs.sampleProgress;
  if (!progress) return undefined; // roster not yet enumerated (inputs still locking)

  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const labels = app.model.outputs.sampleLabels ?? {};

  const infoBySample = new Map<string, ProgressInfo>();
  const sampleIds = new Set<string>();
  for (const e of progress.data) {
    const sampleId = String(e.key[0]);
    sampleIds.add(sampleId);
    const info = e.value as ProgressInfo | undefined;
    if (info) infoBySample.set(sampleId, info);
  }

  return [...sampleIds]
    .map((sampleId): SampleProgressRow => {
      const label = labels[sampleId] ?? sampleId;

      if (completed.has(sampleId)) {
        return { sampleId, label, stage: "done", step: "Done", progressString: "" };
      }

      const info = infoBySample.get(sampleId);

      // Live parse progress — show stage + percent + ETA with a determinate bar.
      if (info?.live && info.progressLine) {
        const parsed = parseProgressString(info.progressLine.replace(ProgressPrefix, ""));
        const percent = parsed.percentage ? Number(parsed.percentage) : undefined;
        const right = [parsed.percentage ? `${parsed.percentage}%` : "", parsed.etaLabel ?? ""]
          .filter(Boolean)
          .join("  ");
        return {
          sampleId,
          label,
          stage: "running",
          step: parsed.stage?.trim() || "Parsing",
          progressString: right,
          percent,
        };
      }

      // Parse stream ended but the sample isn't done — the downstream steps (refine / tag-stat /
      // per-cell metrics) run without emitting progress lines. Show an indeterminate "Processing…".
      if (info) {
        return { sampleId, label, stage: "running", step: "Processing…", progressString: "" };
      }

      // Enumerated but its parse hasn't started emitting yet.
      return { sampleId, label, stage: "not_started", step: "Queued", progressString: "" };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
