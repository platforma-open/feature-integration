import type { QcRow } from "@platforma-open/milaboratories.feature-integration.model";
import type { Color } from "@platforma-sdk/ui-vue";
import { Gradient } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "./app";
import { buildProgressMap, deriveProgress, type ProgressCell } from "./progress";

export type { ProgressCell } from "./progress";

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
  if (!app.model.outputs.started) return undefined;

  const labels = app.model.outputs.sampleLabels ?? {};
  const completed = new Set(app.model.outputs.completedSamples ?? []);
  const qcBySample = app.model.outputs.sampleQc ?? {};
  const progress = app.model.outputs.progress;
  const parseProgress = app.model.outputs.parseProgress;

  const progressMap = buildProgressMap(progress, parseProgress);

  // Roster: dataset labels ∪ completed ∪ QC'd ∪ any sample with a progress entry.
  const sampleIds = new Set<string>([
    ...Object.keys(labels),
    ...completed,
    ...Object.keys(qcBySample),
    ...progressMap.keys(),
  ]);
  // Roster not enumerated yet → keep the grid's loading overlay rather than flashing an empty table.
  if (sampleIds.size === 0) return undefined;

  return [...sampleIds]
    .map((sampleId): SampleResult => {
      const label = labels[sampleId] ?? sampleId;
      // Per-sample QC settles when the sample finishes, so Quality + Read recovery fill in at completion.
      const qc = qcBySample[sampleId];
      const qcFields = qc ? { quality: qualityStatus(qc), recovery: recoveryBar(qc) } : {};
      const progressCell = deriveProgress(sampleId, completed, progressMap);
      return { sampleId, label, progress: progressCell, ...qcFields };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
