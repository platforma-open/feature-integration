import {
  ProgressPrefix,
  type QcRow,
} from "@platforma-open/milaboratories.feature-integration.model";
import type { Color } from "@platforma-sdk/ui-vue";
import { Gradient } from "@platforma-sdk/ui-vue";
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
  // Populated once the sample's QC has settled (i.e. it has finished) — drive the Quality + Read
  // recovery columns. Absent while the sample is still running.
  quality?: QcStatus;
  recovery?: RecoveryBar;
};

// QC status tag shown in the Quality column (worst-case per sample). Rendered by PlAgCellStatusTag.
export type QcStatus = "OK" | "WARN" | "ALERT";

// Stacked-bar settings consumed by PlAgChartStackedBarCell for the Read recovery column.
export type RecoveryBar = {
  title: string;
  data: { label: string; value: number; color: Color; description: string }[];
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

// refine-tags corrects the barcode tags in this fixed order (fb-pipeline.tpl.tengo passes
// `-t CELL -t FEATURE -t UMI`; mitool orders CELL < FEATURE < UMI). mitool labels each progress line
// with the tag it is currently working on ("Counting CELL", "Correcting FEATURE", "Writing UMI", …),
// so we surface WHICH tag is in progress and its position ("2 of 3"). Keep in sync with
// tag-pattern.lib.tengo if the corrected-tag set changes.
const REFINE_TAGS = ["CELL", "FEATURE", "UMI"];
const REFINE_TAG_LABELS: Record<string, string> = {
  CELL: "Cell barcodes",
  FEATURE: "Feature barcodes",
  UMI: "UMIs",
};

// Build the running-state progress cell from mitool's latest live stage label. mitool's per-step
// progress is structured, so instead of one jumpy percent we surface which sub-step is running:
//   • parse — one monotonic pass → show the live percent.
//   • refine-tags — corrects CELL → FEATURE → UMI; per-tag progress is non-monotonic (recursive
//     correction passes). Keep a stable "Refining barcodes" prefix so the label doesn't jump; vary
//     only the colon-suffix ("Refining barcodes" on init → ": Cell/Feature barcodes|UMIs" + "N of 3"
//     per tag → ": Finalizing" on the wrap-up phases), all on an indeterminate (animated) bar.
//   • tag-stat -u — a data-dependent hierarchical on-disk sort (non-monotonic → indeterminate),
//     then one monotonic "Writing result" pass → show the live percent for that final phase.
// Blank suffix on indeterminate cells, else the progress cell defaults the right-hand note to "0%".
function liveCell(
  step: string,
  stage: string,
  percentage?: string,
  etaLabel?: string,
): ProgressCell {
  if (step === "2-refine") {
    const tag = REFINE_TAGS.find((t) => stage.includes(t));
    if (tag) {
      return {
        status: "running",
        text: `Refining barcodes: ${REFINE_TAG_LABELS[tag]}`,
        suffix: `${REFINE_TAGS.indexOf(tag) + 1} of ${REFINE_TAGS.length}`,
      };
    }
    // Non-tag global phases keep the stable "Refining barcodes" prefix so the label doesn't jump.
    // mitool's lead-in stage is "Initialization" (bare label); the wrap-up stages (Filtering /
    // Final sorting / Writing result) collapse to ": Finalizing".
    if (/init/i.test(stage)) {
      return { status: "running", text: "Refining barcodes", suffix: "" };
    }
    return { status: "running", text: "Refining barcodes: Finalizing", suffix: "" };
  }

  if (step === "3-tagstat") {
    // The final "Writing result" pass is monotonic; the preceding on-disk sort is not.
    if (/writing/i.test(stage) && percentage) {
      return {
        status: "running",
        percent: Number(percentage),
        text: `Counting UMIs: writing ${percentage}%`,
        suffix: etaLabel ?? "",
      };
    }
    return { status: "running", text: "Counting UMIs: sorting", suffix: "" };
  }

  // parse (and any other monotonic step): show the live percent when present, else indeterminate.
  const name = STEP_NAMES[step] ?? step;
  if (percentage) {
    return {
      status: "running",
      percent: Number(percentage),
      text: `${name}: ${percentage}%`,
      suffix: etaLabel ?? "",
    };
  }
  return { status: "running", text: name, suffix: "" };
}

// Quality status from the per-sample QC metrics (proposed cutoffs; tune here). Mirrors the analysisLog
// flags: zero cells detected or a very low panel-assigned fraction → ALERT; a low panel-assigned or
// pattern-match fraction → WARN; otherwise OK. panelAssignedFraction is "" when no refine report ran.
function qualityStatus(qc: QcRow): QcStatus {
  const paf = typeof qc.panelAssignedFraction === "number" ? qc.panelAssignedFraction : undefined;
  if (qc.cellsDetected === 0 || (paf !== undefined && paf < 0.25)) return "ALERT";
  if ((paf !== undefined && paf < 0.5) || qc.matchedFraction < 0.8) return "WARN";
  return "OK";
}

// Read-recovery funnel: split each sample's reads into usable (matched the pattern AND kept after the
// feature-barcode panel correction) / off-panel (matched but dropped) / no pattern match. Values are
// read counts summing to readsTotal; PlAgChartStackedBarCell renders them proportionally. When no
// refine report is available (panelAssignedFraction === "") the off-panel split is unknown, so only
// usable (= matched) and no-match are shown.
const RECOVERY_COLORS = {
  usable: Gradient("viridis").getNthOf(2, 5),
  offPanel: Gradient("magma").getNthOf(4, 9),
  noMatch: Gradient("magma").getNthOf(6, 9),
};

function recoveryBar(qc: QcRow): RecoveryBar | undefined {
  const total = qc.readsTotal;
  if (!total) return undefined;
  const matched = qc.readsMatched ?? 0;
  const paf = typeof qc.panelAssignedFraction === "number" ? qc.panelAssignedFraction : undefined;
  const usable = paf !== undefined ? Math.round(matched * paf) : matched;
  const offPanel = paf !== undefined ? Math.max(0, matched - usable) : 0;
  const noMatch = Math.max(0, total - matched);

  const seg = (label: string, value: number, color: Color, desc: string) => ({
    label,
    value,
    color,
    description: [
      label,
      desc,
      `Reads: ${value.toLocaleString()} (${((value / total) * 100).toFixed(1)}%)`,
    ].join("\n"),
  });

  const data = [
    seg(
      "Usable",
      usable,
      RECOVERY_COLORS.usable,
      "Reads that matched the read pattern and were kept after feature-barcode panel correction.",
    ),
  ];
  if (paf !== undefined) {
    data.push(
      seg(
        "Off-panel",
        offPanel,
        RECOVERY_COLORS.offPanel,
        "Reads that matched the pattern but whose feature barcode was dropped as off-panel.",
      ),
    );
  }
  data.push(
    seg(
      "No pattern match",
      noMatch,
      RECOVERY_COLORS.noMatch,
      "Reads that did not match the read pattern.",
    ),
  );

  return { title: "Read recovery", data };
}

export const sampleResults = computed<SampleResult[] | undefined>(() => {
  const app = useApp();
  const roster = app.model.outputs.sampleProgress;
  // undefined until the roster is enumerated — the grid shows its loading overlay in the meantime.
  if (!roster) return undefined;

  const stepProg = app.model.outputs.stepProgress;
  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const labels = app.model.outputs.sampleLabels ?? {};
  const qcBySample = app.model.outputs.sampleQc ?? {};

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
      // Per-sample QC settles when the sample finishes, so Quality + Read recovery fill in at
      // completion (blank while running). Same source as completedSamples.
      const qc = qcBySample[sampleId];
      const qcFields = qc ? { quality: qualityStatus(qc), recovery: recoveryBar(qc) } : {};

      // Whole sample finished.
      if (completed.has(sampleId)) {
        return {
          sampleId,
          label,
          progress: { status: "done", percent: 100, text: "Done" },
          ...qcFields,
        };
      }

      const cur = latest.get(sampleId);
      if (!cur) {
        return {
          sampleId,
          label,
          progress: { status: "not_started", text: "Queued" },
          ...qcFields,
        };
      }

      const name = STEP_NAMES[cur.step] ?? cur.step;

      // Step actively streaming → a descriptive cell built from mitool's structured stage label.
      if (cur.info.live && cur.info.progressLine) {
        const p = parseProgressString(cur.info.progressLine.replace(ProgressPrefix, ""));
        return {
          sampleId,
          label,
          progress: liveCell(cur.step, p.stage ?? "", p.percentage, p.etaLabel),
          ...qcFields,
        };
      }

      // Step finished streaming but the sample isn't done yet — show that step as complete: a full bar
      // with a "Done" note, keeping the step name. The next step resets the bar when it starts emitting.
      return {
        sampleId,
        label,
        progress: { status: "running", percent: 100, text: name, suffix: "Done" },
        ...qcFields,
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
