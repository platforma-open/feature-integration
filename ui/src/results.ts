import type { QcRow } from "@platforma-open/milaboratories.feature-integration.model";
import type { Color } from "@platforma-sdk/ui-vue";
import { Gradient } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "./app";

// Progress-cell config per sample. Maps onto the Progress column cell: status → stage, percent → bar
// fill (undefined = indeterminate), text → main label. Shape matches the SDK's ColDefProgress, so the
// column's `progress` callback passes it through unchanged.
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
  // The grid appears once the run has started. Live per-step progress bars were removed together with
  // the mitool stdout streams (to fix the CIDConflictError — see model/src/index.ts), so the roster now
  // comes from the input dataset's sample labels and each row shows "Processing…" until its QC settles
  // (completedSamples) and it flips to "Done".
  if (!app.model.outputs.started) return undefined;

  const labels = app.model.outputs.sampleLabels ?? {};
  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const qcBySample = app.model.outputs.sampleQc ?? {};

  // Roster: every sample in the input dataset (labels), unioned with any completed / QC'd sample as a
  // fallback in case the upstream label column isn't resolvable yet.
  const sampleIds = new Set<string>([
    ...Object.keys(labels),
    ...completed,
    ...Object.keys(qcBySample),
  ]);
  // Roster not enumerated yet → keep the grid's loading overlay rather than flashing an empty table.
  if (sampleIds.size === 0) return undefined;

  return [...sampleIds]
    .map((sampleId): SampleResult => {
      const label = labels[sampleId] ?? sampleId;
      // Per-sample QC settles when the sample finishes, so Quality + Read recovery fill in at completion
      // (blank while running). Same source as completedSamples.
      const qc = qcBySample[sampleId];
      const qcFields = qc ? { quality: qualityStatus(qc), recovery: recoveryBar(qc) } : {};
      // suffix:"" suppresses the SDK's default right-hand "0%" for running cells (createAgGridColDef
      // falls back to `${percent ?? 0}%` when suffix is nullish). With the live per-step stream gone
      // there is no real percentage to show — the indeterminate bar already conveys "in progress".
      const progress: ProgressCell = completed.has(sampleId)
        ? { status: "done", percent: 100, text: "Done" }
        : { status: "running", text: "Processing…", suffix: "" };
      return { sampleId, label, progress, ...qcFields };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
