import type { QcRow } from "@platforma-open/milaboratories.feature-integration.model";
import type { Color } from "@platforma-sdk/ui-vue";
import { Gradient } from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "./app";
import { deriveProgress, type ProgressCell } from "./progress";

export type { ProgressCell } from "./progress";

export type SampleResult = {
  sampleId: string;
  label: string;
  progress: ProgressCell;
  // Populated once the sample's QC has settled (i.e. it has finished) — drive the Quality + Read
  // recovery columns. Absent while the sample is still running.
  quality?: QcStatus;
  recovery?: RecoveryBar;
  // The raw per-sample metrics behind quality/recovery, carried through so the sample report panel can
  // show the individual checks and figures without re-deriving them from a second source.
  qc?: QcRow;
};

// QC status tag shown in the Quality column (worst-case per sample). Rendered by PlAgCellStatusTag.
export type QcStatus = "OK" | "WARN" | "ALERT";

// Stacked-bar settings consumed by PlAgChartStackedBarCell for the Read recovery column.
export type RecoveryBar = {
  title: string;
  data: { label: string; value: number; color: Color; description: string }[];
};

// One QC check on a sample: what was measured, how it is judged, the value as the reader should see it,
// and prose explaining what the metric means. The sample report panel renders one row per check, so the
// reader learns *which* metric is bad — the grid's single tag can only say that something is.
//
// status is undefined when the metric could not be evaluated at all. That is a real state, not a
// failure: panelAssignedFraction is "" whenever no refine report was produced, and calling that "OK"
// would report a passing verdict on a measurement that never happened. PlStatusTag renders nothing for
// an absent type, and the worst-of roll-up below skips these rows, so an unevaluated check stays silent
// everywhere instead of being quietly counted as a pass.
export type QcCheck = {
  label: string;
  status: QcStatus | undefined;
  printedValue: string;
  description: string;
};

const percent = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

// The per-sample QC checks (proposed cutoffs; tune them here and nowhere else). These mirror the
// analysisLog flags: zero cells detected or a very low panel-assigned fraction → ALERT; a low
// panel-assigned or pattern-match fraction → WARN; otherwise OK.
//
// This is the single definition of the sample's quality. The grid's one-tag Quality column is the worst
// of these statuses (qualityStatus below), so the tag and the panel's rows can never disagree about the
// same sample — which they would if each carried its own copy of the thresholds.
export function qcChecks(qc: QcRow): QcCheck[] {
  const paf = typeof qc.panelAssignedFraction === "number" ? qc.panelAssignedFraction : undefined;

  return [
    {
      label: "Cells detected",
      // Zero cells means nothing downstream of this sample can be computed, so it is the hardest fail
      // the per-sample QC has. Any non-zero count is left unjudged as OK: how many cells a sample
      // *should* yield depends on the experiment, so there is no cutoff we could defend here.
      status: qc.cellsDetected === 0 ? "ALERT" : "OK",
      printedValue: qc.cellsDetected.toLocaleString(),
      description:
        "Cell barcodes that survived the whole pipeline and carry at least one counted UMI. " +
        "Zero means the read geometry or the cell-barcode pattern did not match this sample's reads, " +
        "so no per-cell result can be produced for it.",
    },
    {
      label: "Reads assigned to the panel",
      status: paf === undefined ? undefined : paf < 0.25 ? "ALERT" : paf < 0.5 ? "WARN" : "OK",
      printedValue: paf === undefined ? "not reported" : percent(paf),
      description:
        paf === undefined
          ? "The fraction of pattern-matched reads whose feature barcode was kept after panel " +
            "correction. Not reported for this sample: no refine-tags report was produced, which " +
            "happens when no reads matched the read pattern in the first place. This is a missing " +
            "measurement, not a fraction of zero."
          : "The fraction of pattern-matched reads whose feature barcode was kept after correction " +
            "against the supplied feature-barcode panel. A low value means most barcodes that were " +
            "read are not in the panel — usually the wrong panel file, or the wrong barcode column " +
            "within it.",
    },
    {
      label: "Reads matching the read pattern",
      // Below this the sample is still usable but the library is losing most of its reads before any
      // feature barcode is even read, which is worth a look — hence WARN rather than ALERT.
      status: qc.matchedFraction < 0.8 ? "WARN" : "OK",
      printedValue: `${percent(qc.matchedFraction)} (${qc.readsMatched.toLocaleString()} of ${qc.readsTotal.toLocaleString()})`,
      description:
        "The fraction of raw reads whose structure matched the read pattern, i.e. reads from which a " +
        "cell barcode, a UMI and a feature barcode could be parsed at all. A low value points at the " +
        "read pattern or the chemistry preset, not at the panel.",
    },
  ];
}

// The Quality column's single tag: the worst status across the checks above. Checks that could not be
// evaluated (status undefined) contribute nothing, exactly as an unreported panel-assigned fraction
// contributed nothing when this rule was written out by hand.
const STATUS_SEVERITY: Record<QcStatus, number> = { OK: 0, WARN: 1, ALERT: 2 };

function qualityStatus(qc: QcRow): QcStatus {
  let worst: QcStatus = "OK";
  for (const check of qcChecks(qc)) {
    if (check.status === undefined) continue;
    if (STATUS_SEVERITY[check.status] > STATUS_SEVERITY[worst]) worst = check.status;
  }
  return worst;
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
  const sampleStep = app.model.outputs.sampleStep;

  // Early roster signal: the flat parseLogStream registers per sample the moment parse starts — before
  // any step report settles — so a sample appears in the grid immediately. (The bar detail comes from
  // stepProgress below; this is just "does this sample exist yet".)
  const parseProgress = app.model.outputs.parseProgress;
  const earlyRosterIds = parseProgress ? parseProgress.data.map((p) => String(p.key[0])) : [];

  // Per-[sampleId, step] live progress lines (parse / refine / tag-stat). Index by sampleId → step →
  // progressLine so deriveProgress can pull the line for whichever step the sample is currently on.
  const stepProgress = app.model.outputs.stepProgress;
  const lineBySampleStep = new Map<string, string | undefined>();
  if (stepProgress) {
    for (const p of stepProgress.data) {
      const v = p.value as { progressLine?: string; live: boolean } | undefined;
      lineBySampleStep.set(`${String(p.key[0])} ${String(p.key[1])}`, v?.progressLine);
    }
  }
  // All streaming steps' live lines for a sample, so deriveProgress can pick the furthest one actually
  // streaming (rather than the report-derived step, which advances a beat early and flashed the next
  // step's label during the gap).
  const liveLinesFor = (sampleId: string): Record<string, string | undefined> => ({
    "1-parse": lineBySampleStep.get(`${sampleId} 1-parse`),
    "2-refine": lineBySampleStep.get(`${sampleId} 2-refine`),
    "3-tagstat": lineBySampleStep.get(`${sampleId} 3-tagstat`),
  });

  // Roster: dataset labels ∪ completed ∪ QC'd ∪ any sample with a step signal ∪ early parse signal.
  const sampleIds = new Set<string>([
    ...Object.keys(labels),
    ...completed,
    ...Object.keys(qcBySample),
    ...Object.keys(sampleStep ?? {}),
    ...earlyRosterIds,
  ]);
  // Roster not enumerated yet → keep the grid's loading overlay rather than flashing an empty table.
  if (sampleIds.size === 0) return undefined;

  return [...sampleIds]
    .map((sampleId): SampleResult => {
      const label = labels[sampleId] ?? sampleId;
      // Per-sample QC settles when the sample finishes, so Quality + Read recovery fill in at completion.
      const qc = qcBySample[sampleId];
      const qcFields = qc ? { quality: qualityStatus(qc), recovery: recoveryBar(qc), qc } : {};
      const progressCell = deriveProgress(sampleId, completed, sampleStep, liveLinesFor(sampleId));
      return { sampleId, label, progress: progressCell, ...qcFields };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});
