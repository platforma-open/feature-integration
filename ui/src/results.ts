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
  // Populated once the sample's QC has settled, meaning the sample finished, to drive the Quality and Read
  // recovery columns. Absent while the sample is still running.
  quality?: QcStatus;
  recovery?: RecoveryBar;
  // The raw per-sample metrics behind quality/recovery, carried through so the sample report panel can show
  // the individual checks and figures without re-deriving them from a second source.
  qc?: QcRow;
};

// QC status tag shown in the Quality column (worst-case per sample). Rendered by PlAgCellStatusTag.
export type QcStatus = "OK" | "WARN" | "ALERT";

// Stacked-bar settings consumed by PlAgChartStackedBarCell for the Read recovery column.
export type RecoveryBar = {
  title: string;
  data: { label: string; value: number; color: Color; description: string }[];
};

// One QC check on a sample: what was measured, how it is judged, the value as the reader should see it, and
// prose explaining what the metric means. The sample report panel renders one row per check, so the reader
// learns *which* metric is bad, where the grid's single tag can only say that something is.
//
// status is undefined where the metric could not be evaluated at all. That is a real state rather than a
// failure: panelAssignedFraction is "" whenever no refine report was produced, and calling that "OK" would
// report a passing verdict on a measurement that never happened. PlStatusTag renders nothing for an absent
// type, and the worst-of roll-up below skips these rows, so an unevaluated check stays silent everywhere.
export type QcCheck = {
  label: string;
  status: QcStatus | undefined;
  printedValue: string;
  description: string;
};

const percent = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

// The per-sample QC checks. Each status rests on a line with a stated source, or it is undefined and the
// value speaks for itself. No number here is invented: a line comes from a figure the field published, from a
// categorical fact, or from a stated recommendation, and from nowhere else. Where none of the three applies,
// the check carries no status rather than a number with nothing behind it.
//
// The single definition of the sample's quality. The grid's one-tag Quality column is the worst of these
// statuses (qualityStatus below), so the tag and the panel's rows can never disagree about one sample, which
// they would if each carried its own copy of the thresholds.
//
// These are NOT the software layer's quality measurements. That is a larger set with its own statuses, its
// own line provenance and its own page. Bringing the two together is a separate change.

// Inherited from the field rather than calibrated here: the complement of the published 0.50
// unrecognized-barcode fraction. `qc_measures.py` holds the same number for panelAssignedFraction, and the
// two must not drift.
const PANEL_ASSIGNED_LINE = 0.5;
export function qcChecks(qc: QcRow): QcCheck[] {
  const paf = typeof qc.panelAssignedFraction === "number" ? qc.panelAssignedFraction : undefined;

  return [
    {
      label: "Cells detected",
      // Categorical, not a quantity judged against a cutoff. Zero cells means nothing downstream of this
      // sample can be computed, and that is a fact rather than a threshold somebody chose. Above zero the
      // fact does not hold, which is all OK says here -- never that the yield was good. How many cells a
      // sample *should* yield depends on the experiment, and no number for that is defensible.
      status: qc.cellsDetected === 0 ? "ALERT" : "OK",
      printedValue: qc.cellsDetected.toLocaleString(),
      description:
        "Cell barcodes that survived the whole pipeline and carry at least one counted UMI. " +
        "Zero means the read geometry or the cell-barcode pattern did not match this sample's reads, " +
        "so no per-cell result can be produced for it.",
    },
    {
      label: "Reads assigned to the panel",
      // One line, inherited, and no second tier. The field publishes 0.50 and nothing else, so an ALERT
      // level below it would be a number invented here -- a confident label on an arbitrary cut, which is
      // worse than saying less. A reader can act on WARN. They cannot act on a severity nobody calibrated.
      status: paf === undefined ? undefined : paf < PANEL_ASSIGNED_LINE ? "WARN" : "OK",
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
      // Unjudged, and shown with its value beside it. The matched share is not one of the four numbers the
      // field publishes for this assay, and nothing published says what a low one means, so no status is
      // claimed. The finding survives anyway: one sample at 40% beside its neighbours at 95% is visible in
      // the column, which is a comparison a reader makes rather than a line this code can apply.
      status: undefined,
      printedValue: `${percent(qc.matchedFraction)} (${qc.readsMatched.toLocaleString()} of ${qc.readsTotal.toLocaleString()})`,
      description:
        "The fraction of raw reads whose structure matched the read pattern, i.e. reads from which a " +
        "cell barcode, a UMI and a feature barcode could be parsed at all. A low value points at the " +
        "read pattern or the chemistry preset, not at the panel.",
    },
  ];
}

// The Quality column's single tag: the worst status across the checks above. A check that could not be
// evaluated, with status undefined, contributes nothing.
const STATUS_SEVERITY: Record<QcStatus, number> = { OK: 0, WARN: 1, ALERT: 2 };

function qualityStatus(qc: QcRow): QcStatus {
  let worst: QcStatus = "OK";
  for (const check of qcChecks(qc)) {
    if (check.status === undefined) continue;
    if (STATUS_SEVERITY[check.status] > STATUS_SEVERITY[worst]) worst = check.status;
  }
  return worst;
}

// Read-recovery funnel, splitting each sample's reads three ways: usable, meaning matched the pattern AND
// kept after the feature-barcode panel correction; off-panel, meaning matched but dropped; and no pattern
// match. The values are read counts summing to readsTotal, and PlAgChartStackedBarCell renders them
// proportionally. Where no refine report is available (panelAssignedFraction === "") the off-panel split is
// unknown, so only usable, which is then matched, and no-match are shown.
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

  // Early roster signal. The flat parseLogStream registers per sample the moment parse starts, before any
  // step report settles, so a sample appears in the grid immediately. This answers only "does this sample
  // exist yet". The bar detail comes from stepProgress below.
  const parseProgress = app.model.outputs.parseProgress;
  const earlyRosterIds = parseProgress ? parseProgress.data.map((p) => String(p.key[0])) : [];

  // Per-[sampleId, step] live progress lines (parse / refine / tag-stat). Indexed by sampleId -> step ->
  // progressLine, so deriveProgress can pull the line for whichever step the sample is on.
  const stepProgress = app.model.outputs.stepProgress;
  const lineBySampleStep = new Map<string, string | undefined>();
  if (stepProgress) {
    for (const p of stepProgress.data) {
      const v = p.value as { progressLine?: string; live: boolean } | undefined;
      lineBySampleStep.set(`${String(p.key[0])} ${String(p.key[1])}`, v?.progressLine);
    }
  }
  // Every streaming step's live line for a sample, so deriveProgress can pick the furthest one actually
  // streaming. The report-derived step advances a beat early and flashes the next step's label in the gap.
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
  // Roster not enumerated yet, so keep the grid's loading overlay rather than flashing an empty table.
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
