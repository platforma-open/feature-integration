import type {
  QcMeasurementStatus,
  QcRow,
  SampleQcReport,
} from "@platforma-open/milaboratories.feature-integration.model";
import type { Color } from "@platforma-sdk/ui-vue";
import { Gradient } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "./app";
import { deriveProgress, type ProgressCell, type StepStream } from "./progress";

export type { ProgressCell } from "./progress";

export type SampleResult = {
  sampleId: string;
  label: string;
  progress: ProgressCell;
  // Populated once the sample's QC has settled, meaning the sample finished. Absent while it is running.
  quality?: QcStatus;
  recovery?: RecoveryBar;
  // The sample's own quality report: every sample-level measurement the software declares, with its status,
  // its value and, where it has none, the reason in its place. The Quality tag above is this report's rollup,
  // so the tag and the report cannot disagree about one sample.
  qcReport?: SampleQcReport;
};

// Rendered by PlAgCellStatusTag and PlStatusTag, whose vocabulary is upper-case; the software's is not. A
// measurement with no line behind it carries no status at all, and there is no fourth word for that.
export type QcStatus = "OK" | "WARN" | "ALERT";

const STATUS_TAG: Record<QcMeasurementStatus, QcStatus> = {
  OK: "OK",
  warn: "WARN",
  alert: "ALERT",
};

export function qcStatusTag(status: QcMeasurementStatus | null): QcStatus | undefined {
  return status === null ? undefined : STATUS_TAG[status];
}

// Stacked-bar settings consumed by PlAgChartStackedBarCell for the Read recovery column.
export type RecoveryBar = {
  title: string;
  data: { label: string; value: number; color: Color; description: string }[];
};

// Read-recovery funnel, splitting each sample's reads three ways: usable, meaning matched the pattern AND
// kept after the feature-barcode panel correction; off-panel, meaning matched but dropped; and no pattern
// match. The values are read counts summing to readsTotal. Where no refine report is available
// (panelAssignedFraction === "") the off-panel split is unknown, so only usable and no-match are shown.
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
  // Written by the verdict step, so it settles later than the per-sample read QC above and only for a run
  // with a V(D)J dataset. A sample without one keeps its progress and its recovery bar and shows no tag.
  const reportBySample = app.model.outputs.sampleQcReport ?? {};
  const sampleStep = app.model.outputs.sampleStep;

  // Early roster signal. The flat parseLogStream registers per sample the moment parse starts, before any
  // step report settles. This answers only "does this sample exist yet".
  const parseProgress = app.model.outputs.parseProgress;
  const earlyRosterIds = parseProgress ? parseProgress.data.map((p) => String(p.key[0])) : [];
  // It also carries parse's live LINE, and that matters for the whole of parse: `stepLogs` is built by
  // fb-refine-tagstat, so nothing lands in the per-step map below until parse is over and the next template
  // runs.
  const parseStreamBySample = new Map<string, StepStream>();
  if (parseProgress) {
    for (const p of parseProgress.data) {
      const v = p.value as { progressLine?: string; live: boolean } | undefined;
      parseStreamBySample.set(String(p.key[0]), { line: v?.progressLine, live: v?.live });
    }
  }

  // Per-[sampleId, step] live progress lines (parse / refine / tag-stat). Indexed by sampleId -> step ->
  // progressLine, so deriveProgress can pull the line for whichever step the sample is on.
  const stepProgress = app.model.outputs.stepProgress;
  const streamBySampleStep = new Map<string, StepStream>();
  if (stepProgress) {
    for (const p of stepProgress.data) {
      const v = p.value as { progressLine?: string; live: boolean } | undefined;
      streamBySampleStep.set(`${String(p.key[0])} ${String(p.key[1])}`, {
        line: v?.progressLine,
        live: v?.live,
      });
    }
  }
  // Every streaming step's live line for a sample, so deriveProgress can pick the furthest one actually
  // streaming. The Python step streams on its own output rather than into the per-step map, because it runs
  // in a different template. Keyed by sample alone, so it is indexed here and joined under the step name the
  // bar knows it by.
  const metricsProgress = app.model.outputs.metricsProgress;
  const metricsStreamBySample = new Map<string, StepStream>();
  if (metricsProgress) {
    for (const p of metricsProgress.data) {
      const v = p.value as { progressLine?: string; live: boolean } | undefined;
      metricsStreamBySample.set(String(p.key[0]), { line: v?.progressLine, live: v?.live });
    }
  }
  const liveLinesFor = (sampleId: string): Record<string, StepStream | undefined> => ({
    // The per-step map wins once it fills, since it keeps streaming after the flat stream closes.
    "1-parse": streamBySampleStep.get(`${sampleId} 1-parse`) ?? parseStreamBySample.get(sampleId),
    "2-refine": streamBySampleStep.get(`${sampleId} 2-refine`),
    "3-tagstat": streamBySampleStep.get(`${sampleId} 3-tagstat`),
    "4-metrics": metricsStreamBySample.get(sampleId),
  });

  const sampleIds = new Set<string>([
    ...Object.keys(labels),
    ...completed,
    ...Object.keys(qcBySample),
    ...Object.keys(sampleStep ?? {}),
    ...earlyRosterIds,
  ]);
  // Roster not enumerated yet, so keep the grid's loading overlay rather than flashing an empty table.
  if (sampleIds.size === 0) return undefined;

  return [...sampleIds]
    .map((sampleId): SampleResult => {
      const label = labels[sampleId] ?? sampleId;
      const qc = qcBySample[sampleId];
      const qcReport = reportBySample[sampleId];
      const qcFields = {
        ...(qc ? { recovery: recoveryBar(qc) } : {}),
        // The tag IS the report's rollup. Nothing here recomputes it, so the grid and the sample's own report
        // state one status rather than two that can drift.
        ...(qcReport ? { quality: qcStatusTag(qcReport.status), qcReport } : {}),
      };
      const progressCell = deriveProgress(sampleId, completed, sampleStep, liveLinesFor(sampleId));
      return { sampleId, label, progress: progressCell, ...qcFields };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
