<script setup lang="ts">
import type { PredefinedGraphOption } from "@milaboratories/graph-maker";
import { GraphMaker } from "@milaboratories/graph-maker";
import type {
  CellListSource,
  QcMeasurementStatus,
} from "@platforma-open/milaboratories.feature-integration.model";
import type { PColumnSpec, PlDataTableSheet } from "@platforma-sdk/model";
import { getAxisId, PFrameImpl, pTableValue } from "@platforma-sdk/model";
import {
  PL_PLACEHOLDER_TEXTS,
  PlAgCellStatusTag,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlPlaceholder,
  PlTabs,
  usePlDataTableSettingsV2,
  useWatchFetch,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import QcEntityCell from "../components/QcEntityCell.vue";
import { qcStatusTag } from "../results";

const app = useApp();

// PlAgDataTableV2 hands its `cellRendererSelector` to `defaultColDef`, so one selector sees every column
// of the table and must return undefined for the ones it does not claim. `colDef.context` is the column
// spec the table was built from, which is how a column is recognised by p-column name rather than by
// header text or position.
const UNDECLARED_STATUS = "pl7.app/antigen/undeclaredBarcodeStatus";
const QC_ENTITY_AXIS = "pl7.app/antigen/qcEntity";

type RendererParams = {
  value?: unknown;
  colDef?: { context?: { type?: string; id?: unknown; spec?: { name?: string } } };
};

function specName(params: RendererParams): string | undefined {
  return params.colDef?.context?.spec?.name;
}

// The same three words the sample list uses for a status, in the same colours. `qcStatusTag` maps the
// software's vocabulary onto the tag component's, and returns undefined where no line stands behind the
// measurement -- which renders as an ordinary cell rather than a fourth colour.
const QC_STATUS_VALUES: readonly QcMeasurementStatus[] = ["OK", "warn", "alert"];

function asQcStatus(value: unknown): QcMeasurementStatus | null {
  return QC_STATUS_VALUES.includes(value as QcMeasurementStatus)
    ? (value as QcMeasurementStatus)
    : null;
}

function undeclaredCellRenderer(params: RendererParams) {
  if (specName(params) !== UNDECLARED_STATUS) return undefined;
  const tag = qcStatusTag(asQcStatus(params.value));
  return tag === undefined ? undefined : { component: PlAgCellStatusTag, params: { type: tag } };
}

function qcCellRenderer(params: RendererParams) {
  if (params.colDef?.context?.type !== "axis") return undefined;
  const axis = params.colDef?.context?.id as { name?: string } | undefined;
  if (axis?.name !== QC_ENTITY_AXIS) return undefined;
  return {
    component: QcEntityCell,
    params: { value: params.value, labels: app.model.outputs.sampleLabels ?? {} },
  };
}

// Two readings of the same run, on one page because a reader checking whether a run can be trusted asks both
// questions at once: did the measurements pass, and did the panel we declared match the barcodes the
// sequencer actually returned.
//
// This page is the RUN's quality, never the sample's. The "Per-sample QC" page above shows the mitool
// per-sample stats -- reads parsed and matched, cells and features detected -- one row per sample. What is
// below is keyed (level, panel, entity, measurement): the measurements the verdict stage takes over the whole
// run. The two pages are named apart for that reason, since "QC" alone would read as two views of one set of
// numbers.
//
// The measurements grid holds run, sample and tag rows behind one `entity` column whose meaning
// changes with the level, so `qcLevel` splits it into one sheet per level. Sheet options are the
// axis's own values in this frame, never a listed set of level names.
//
// A level with no rows here gets no sheet. `_qc_frame` builds from the rows its call sites added,
// not from `MEASUREMENTS`: the run level's one measurement is added unconditionally, and the
// tag-level rows come from the panel's tags, so every level of a run with a panel has rows.
const QC_LEVEL_AXIS_NAME = "pl7.app/antigen/qcLevel";

const qcLevelPframeHandle = computed(() => {
  const output = app.model.outputs.runQualityTable;
  return output?.ok ? output.value?.fullPframeHandle : undefined;
});

const qcLevelSheetsFetch = useWatchFetch(qcLevelPframeHandle, async (handle) => {
  if (handle === undefined) return undefined;
  const pFrame = new PFrameImpl(handle);
  const columns = await pFrame.listColumns();
  const levelColumns = columns.filter((c) =>
    c.spec.axesSpec.some((a) => a.name === QC_LEVEL_AXIS_NAME),
  );
  if (levelColumns.length === 0) return undefined;

  const axis = levelColumns[0].spec.axesSpec.find((a) => a.name === QC_LEVEL_AXIS_NAME)!;
  const axisId = getAxisId(axis);
  const seen = new Map<string | number, string | number>();
  for (const column of levelColumns) {
    const response = await pFrame.getUniqueValues({
      columnId: column.columnId,
      axis: axisId,
      filters: [],
      limit: 1000,
    });
    for (let i = 0; i < response.values.data.length; i++) {
      const value = pTableValue(response.values, i) as string | number;
      seen.set(value, value);
    }
  }
  // Sorted: `listColumns` fixes no order, and an unsorted first element makes the sheet the page
  // opens on vary run to run.
  const values = [...seen.values()].sort((a, b) => String(a).localeCompare(String(b)));

  const sheet: PlDataTableSheet = {
    axis,
    options: values.map((v) => ({ value: v, label: String(v) })),
    defaultValue: values[0],
  };
  return [sheet];
});

const qcSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.runQualityTable,
  sheets: () => qcLevelSheetsFetch.value,
});

const mismatchSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.runQualityMismatchTable,
});

const reagentSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.reagentTable,
});

const undeclaredSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.undeclaredBarcodesTable,
});

// A missing V(D)J dataset is a legitimate state rather than a half-filled form: the block runs, and the
// verdict stage alone is skipped, so neither table below has a source. Read from data rather than from an
// output, because the point is what the user has chosen, including before the next run. Same device, and the
// same reason, as the explore readout's own empty state.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// Every grid is drawn unconditionally, and the three states a frame can be in are answered inside it.
// `usePlDataTableSettingsV2` reads the `withStatus` wrapper: while the run is in flight the model is not yet
// stable, so the grid draws the processing placeholder every other loading table in the app draws. Once the
// run settles with no frame, the grid draws `notReadyText`. A frame that arrived with no rows draws
// `noRowsText`. An alert above the grid was a fourth surface saying what the grid says itself, and while the
// run was in flight it said the report had not arrived, which reads as a finished run with nothing in it.
//
// `ok === false` reaches the grid too, which renders the error it was handed. Swallowing it would report a
// failure as a choice the user made.

// --- the distributions ---------------------------------------------------------------------------
//
// `330-the-quality-readout` puts three distributions last, and two of them exist so a scientist can
// place a number: the spread of the run's scores is where a cutoff goes when the scores separate,
// and the fitted background is the only way to see whether a tag's counts separated at all. The
// third, the reference reading across cells, informs the gate. Each is drawn on the rungs that
// produce it, and says so on the rungs that do not.
//
// All three read one p-frame. The two decile sets share a `distribution` axis and are told apart by
// a filter GraphMaker applies from the default options below.
//
// Three states, and the grids' rule applies here too: while the run is in flight the plot shows the
// processing placeholder, never a sentence saying the distributions have not arrived. That sentence
// reads as a finished run that reported nothing. `PlPlaceholder` is the same component the grids draw
// through their loading overlay, in its `graph` variant.
const distributions = computed(() => app.model.outputs.runQualityDistributions);

// Not settled yet. `stable` lives on the ok branch alone, so an errored output is not pending: it falls
// through to GraphMaker, which renders the error it was handed.
const distributionsPending = computed(() => {
  const output = distributions.value;
  return output === undefined || (output.ok && !output.stable);
});

// Settled, and the verdict stage produced no frame at all.
const distributionsAbsent = computed(() => {
  const output = distributions.value;
  return output !== undefined && output.ok && output.stable && output.value === undefined;
});

// The rung that actually SERVED, not the one requested: a request the panel cannot honour degrades,
// and the plots follow what happened rather than what was asked for. Undefined until the run reports
// its own meta, and that case is NOT "some other rung served" -- reading it that way told a reader
// both that there were no scores and that there was no background, which cannot both be true of one
// run, since a run is served by exactly one rung.
const served = computed(() => app.model.outputs.verdictRunMeta?.referenceChoice);

// Which cell list every fraction of cells was computed against. Two runs whose lists came from
// different sources do not share a denominator. "none" means no list arrived, which is not the same
// as an empty one: the measurements needing a list read *not evaluated* rather than zero.
const CELL_LIST_WORDING: Record<CellListSource, string> = {
  "cell list": "Cell figures are counted against the supplied cell list.",
  "clonotype linker": "Cell figures are counted against the cells the clonotype linker returned.",
  none: "No cell list reached this run, so every figure needing one reads as not evaluated.",
};
const cellListSource = computed(() => {
  const source = app.model.outputs.verdictRunMeta?.cellListSource;
  return source === undefined ? undefined : CELL_LIST_WORDING[source];
});
const rungUnknown = computed(() => served.value === undefined);

// Only the declared rung produces a score: the others yield a probability, which is not on the same
// scale. Only a population rung fits a background. So each of these is empty on exactly the runs the
// other is not, and neither is a failure.
const noScores = computed(() => !rungUnknown.value && served.value !== "declared");
const noBackgrounds = computed(() => !rungUnknown.value && served.value !== "distribution");

// A population baseline is keyed (sample, identity), not by cell, so `reference_by_cell` refuses that
// rung and no per-cell reading exists to spread. The other two rungs read a comparator in the cell.
const noReferenceReadings = computed(() => !rungUnknown.value && served.value === "distribution");

// One tab per view. Five readings stacked put the one a reader wants below the fold, and a tab lets each
// view own the full height a chart or a grid needs. Local rather than stored: which tab is open is a
// glance, not a setting.
//
// A plot the served baseline cannot produce gets NO tab. The four grids are always there. The three plots
// each belong to one rung, so on any given run at most one of the last two pairs can be drawn, and a tab
// that opens onto a statement of why it is empty is a tab a reader learns to stop opening. While the run
// has not reported which rung served it, all three are offered and each says so when opened -- the rung is
// unknown at that point, not known to be wrong.
type ViewTab =
  | "measurements"
  | "reagents"
  | "mismatch"
  | "undeclared"
  | "score"
  | "reference"
  | "background";

const VIEW_TABS = computed(() => [
  { label: "Measurements", value: "measurements" as const },
  { label: "Reagents", value: "reagents" as const },
  { label: "Panel vs reads", value: "mismatch" as const },
  { label: "Undeclared barcodes", value: "undeclared" as const },
  ...(noScores.value ? [] : [{ label: "Scores", value: "score" as const }]),
  ...(noReferenceReadings.value
    ? []
    : [{ label: "Reference readings", value: "reference" as const }]),
  ...(noBackgrounds.value ? [] : [{ label: "Fitted background", value: "background" as const }]),
]);

const activeView = ref<ViewTab>("measurements");

// The open tab can stop existing: the run reports its rung, and the plot that tab held cannot be drawn.
// Falling back to Measurements keeps the page showing something rather than an empty body under a tab
// strip that no longer offers the tab. Watching an output and writing a LOCAL ref is not a hairpin -- see
// hairpin.md -- because nothing here reaches server-stored data.
watch(VIEW_TABS, (tabs) => {
  if (!tabs.some((t) => t.value === activeView.value)) activeView.value = "measurements";
});

const DECILE_VALUE = "pl7.app/antigen/qcDecileValue";
const DECILE_AXIS = "pl7.app/antigen/qcDecile";
const DISTRIBUTION_AXIS = "pl7.app/antigen/qcDistribution";
const BACKGROUND_MEAN = "pl7.app/antigen/fittedBackgroundMean";
const SIGNAL_MEAN = "pl7.app/antigen/fittedSignalMean";
const BACKGROUND_WEIGHT = "pl7.app/antigen/fittedBackgroundWeight";
const SAMPLE_AXIS = "pl7.app/sampleId";

// GraphMaker resolves a source by name, value type, annotations and domain only, so `axesSpec` is
// required by the type and unread. Building the source through a typed helper keeps `inputName`
// checked against the chart type's component list: a name absent from that list indexes an undefined
// component inside GraphMaker and throws.
function col(name: string, valueType: "Double" | "Int"): PColumnSpec {
  return { kind: "PColumn", name, valueType, axesSpec: [] };
}

// Both decile plots read the same column and differ only in which distribution they filter to, so
// the options are built once and the caller names the slice.
//
// A discrete chart has no `x`: its categorical input is `primaryGrouping`, and the axis reaches the
// filter only because `qcDecileValue` carries it. The filter value field is `selectedFilterValues`.
function decileOptions(distribution: string): PredefinedGraphOption<"discrete">[] {
  return [
    { inputName: "y", selectedSource: col(DECILE_VALUE, "Double") },
    { inputName: "primaryGrouping", selectedSource: { name: DECILE_AXIS, type: "Int" } },
    {
      inputName: "filters",
      selectedSource: { name: DISTRIBUTION_AXIS, type: "String" },
      filterType: "equals",
      selectedFilterValues: [distribution],
    },
  ];
}

// The frame is the whole-run graph frame and carries every verdict, clonotype and feature column
// beside the four these plots read. Narrowing the column list is what keeps the pickers usable.
const decileColumns = (spec: PColumnSpec) => spec.name === DECILE_VALUE;
const backgroundColumns = (spec: PColumnSpec) =>
  spec.name === BACKGROUND_MEAN || spec.name === SIGNAL_MEAN || spec.name === BACKGROUND_WEIGHT;

const scoreOptions = computed(() => decileOptions("score"));
const referenceOptions = computed(() => decileOptions("referenceReading"));

// The two means side by side, which is the whole reading: a background alone says nothing about
// whether the counts separated, and a tag that bound nothing shows as two means almost on top of
// each other rather than as a refusal.
//
// Faceted by sample, because the fit runs per (tag, sample): one panel pools two samples' fits and
// reads as one population. `330` asks for one plot per tag per sample. This is a panel per sample
// with a point per tag, which is not that grid — the frame carries the two fitted means, and two
// means cannot draw the two humps the atom asks a reader to judge.
const backgroundOptions = computed<PredefinedGraphOption<"scatterplot">[]>(() => [
  { inputName: "x", selectedSource: col(BACKGROUND_MEAN, "Double") },
  { inputName: "y", selectedSource: col(SIGNAL_MEAN, "Double") },
  { inputName: "facetBy", selectedSource: { name: SAMPLE_AXIS, type: "String" } },
]);

// The four exclusions this assay carries -- doublets, a false-discovery rate, the empty-droplet share and
// sequencing saturation -- are stated in `qc_measures.py`, beside the measurement each one sits next to.
// They are properties of the method rather than of any run, and no surface here repeats them.

// Status is rendered as the plain string the workflow emitted, with the discrete filter its spec declares.
// The vocabulary is now OK / warn / alert and nothing else, which IS a rank, so a status tag would fit the
// three. It stays plain text because of the fourth case a tag cannot render: a measurement with no line
// behind it leaves this column empty, and an empty cell beside three tags reads as a tag that failed to
// load. Which of the two no-status cases it is reads from the value column, not from here.
</script>

<template>
  <PlBlockPage>
    <template #title>Run quality</template>

    <PlAlert v-if="noDataset" type="warn">
      This run has no quality report: the verdict stage only runs once a single-cell V(D)J dataset
      is picked, so the run counted barcodes per cell and stopped before anything was measured. Pick
      a dataset in Settings on the Explore readout page and run again. The per-sample read
      statistics are unaffected and are on the Per-sample QC page.
    </PlAlert>

    <template v-else>
      <!-- 320 requires every figure to say which cell list it was computed against. One list serves the
           whole run, so it is stated once here rather than repeated on each measurement. -->
      <div v-if="cellListSource" class="qc-cell-list">{{ cellListSource }}</div>

      <PlTabs v-model="activeView" :options="VIEW_TABS" />

      <PlAgDataTableV2
        v-if="activeView === 'measurements'"
        v-model="app.model.data.runQualityTableState"
        :settings="qcSettings"
        :cell-renderer-selector="qcCellRenderer"
        not-ready-text="The verdict stage produced no quality report for this run."
        no-rows-text="The report imported with no measurements in it. Every declared measurement should keep a row — a deferred one carries no status and gives its reason in place of a value — so an empty report means the measurements were lost on the way here, not that the run was clean."
        show-export-button
      />

      <PlAgDataTableV2
        v-else-if="activeView === 'reagents'"
        v-model="app.model.data.reagentTableState"
        :settings="reagentSettings"
        not-ready-text="The verdict stage produced no reagent table for this run."
        no-rows-text="The table imported with no reagents in it. Every declared barcode keeps a row under every identity it carries — a dead one reads zero under Seen in — so an empty table means the panel reached this stage with nothing declared, not that the reagents were clean."
        show-export-button
      />

      <PlAgDataTableV2
        v-else-if="activeView === 'mismatch'"
        v-model="app.model.data.runQualityMismatchTableState"
        :settings="mismatchSettings"
        not-ready-text="The verdict stage produced no panel-versus-reads check for this run."
        no-rows-text="Every barcode the panel declared was carried by reads, which is the outcome you want. The opposite direction is not reported here: barcode correction snaps each feature onto the panel before counting, so a barcode the panel never declared cannot reach this check. See the Undeclared barcodes tab for that direction."
        show-export-button
      />

      <PlAgDataTableV2
        v-else-if="activeView === 'undeclared'"
        v-model="app.model.data.undeclaredBarcodesTableState"
        :settings="undeclaredSettings"
        :cell-renderer-selector="undeclaredCellRenderer"
        not-ready-text="The verdict stage produced no undeclared-barcode table for this run."
        no-rows-text="No row here for any sample: every barcode the pre-refine pass saw was on some sample's panel. That is the outcome to want, not a check that failed to run."
        show-export-button
      />

      <div v-else-if="distributionsPending" :class="$style.plot">
        <PlPlaceholder
          variant="graph"
          :title="PL_PLACEHOLDER_TEXTS.RUNNING.title"
          :subtitle="PL_PLACEHOLDER_TEXTS.RUNNING.subtitle"
        />
      </div>

      <PlAlert v-else-if="distributionsAbsent" type="info">
        The verdict stage produced no distributions for this run. They are taken by the same stage
        as the measurements, so they arrive with them.
      </PlAlert>

      <!-- No "wrong rung" alert in any of the three plot bodies. A rung that cannot produce the plot takes
           its tab away, so the body is reachable only while the rung is unknown or while it is the one that
           produces the plot. -->
      <div v-else-if="activeView === 'score'" :class="$style.plot">
        <PlAlert v-if="rungUnknown" type="info">
          This run has not reported which baseline served it yet.
        </PlAlert>
        <GraphMaker
          v-else
          v-model="app.model.data.scoreDistributionGraphState"
          chart-type="discrete"
          :p-frame="app.model.outputs.runQualityDistributions"
          :default-options="scoreOptions"
          :data-column-predicate="decileColumns"
        />
      </div>

      <div v-else-if="activeView === 'reference'" :class="$style.plot">
        <PlAlert v-if="rungUnknown" type="info">
          This run has not reported which baseline served it yet.
        </PlAlert>
        <GraphMaker
          v-else
          v-model="app.model.data.referenceReadingGraphState"
          chart-type="discrete"
          :p-frame="app.model.outputs.runQualityDistributions"
          :default-options="referenceOptions"
          :data-column-predicate="decileColumns"
        />
      </div>

      <div v-else :class="$style.plot">
        <PlAlert v-if="rungUnknown" type="info">
          This run has not reported which baseline served it yet.
        </PlAlert>
        <GraphMaker
          v-else
          v-model="app.model.data.fittedBackgroundGraphState"
          chart-type="scatterplot"
          :p-frame="app.model.outputs.runQualityDistributions"
          :default-options="backgroundOptions"
          :data-column-predicate="backgroundColumns"
        />
      </div>
    </template>
  </PlBlockPage>
</template>

<style module>
/* GraphMaker fills its container, and a container with no height collapses to nothing.
   PlBlockPage's body is a flex column in a `minmax(0, 1fr)` grid row, so `flex: 1` takes the
   height the tabs and alerts above leave. The floor holds room for a faceted grid, which needs
   more than one panel's worth. */
.plot {
  flex: 1;
  min-height: 480px;
}

.qc-cell-list {
  padding: 4px 0 12px;
  font-size: 13px;
  color: var(--color-txt-03);
}
</style>
