<script setup lang="ts">
import type { PredefinedGraphOption } from "@milaboratories/graph-maker";
import { GraphMaker } from "@milaboratories/graph-maker";
import type { CellListSource } from "@platforma-open/milaboratories.feature-integration.model";
import type { PColumnSpec, PlDataTableSheet } from "@platforma-sdk/model";
import { getAxisId, PFrameImpl, pTableValue } from "@platforma-sdk/model";
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlTabs,
  usePlDataTableSettingsV2,
  useWatchFetch,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

const app = useApp();

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
// The measurements grid stacks run, sample and tag rows behind one `entity` column that means a
// different thing per row, so `qcLevel` splits it into one sheet per level. Sheet options are read
// live off the frame's own `pl7.app/antigen/qcLevel` axis (`QC_LEVEL_AXIS_NAME` below), never off a
// listed set of level names, so a level added to the measurement set later gets a sheet with no UI
// change. A level with zero rows in this frame gets no sheet: the verdict stage keeps a row for
// every measurement it declares, deferred ones included, so an applicable level always has rows and
// a level with none was not applicable to this run.
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
  const values = [...seen.values()];

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

// An absent frame and an empty frame are different facts and get different words. Absent means the verdict
// stage produced no report at all, so the frame is not there to read. Empty means it ran, imported its frame
// and put no rows in it, which for the mismatch check is the wanted outcome and for the measurements is a
// sign something went wrong upstream. So absence is answered here, by drawing no grid at all, and emptiness
// inside the grid through `noRowsText`. Neither ends up as a bare empty table.
//
// `ok === false` is deliberately NOT treated as absence. An errored output belongs to the grid, which renders
// the error it was handed. Swallowing it into "the stage did not run" would report a failure as a choice the
// user made.
const qcAbsent = computed(() => {
  const output = app.model.outputs.runQualityTable;
  return output === undefined || (output.ok && output.value === undefined);
});

const mismatchAbsent = computed(() => {
  const output = app.model.outputs.runQualityMismatchTable;
  return output === undefined || (output.ok && output.value === undefined);
});

const reagentAbsent = computed(() => {
  const output = app.model.outputs.reagentTable;
  return output === undefined || (output.ok && output.value === undefined);
});

const undeclaredAbsent = computed(() => {
  const output = app.model.outputs.undeclaredBarcodesTable;
  return output === undefined || (output.ok && output.value === undefined);
});

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
const distributionsAbsent = computed(() => app.model.outputs.runQualityDistributions === undefined);

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

// One tab per view. Five readings stacked put the one a reader wants below the fold, and the ones a
// given run cannot draw pushed it further. A tab also lets each view own the full height a chart or a
// grid needs. Local rather than stored: which tab is open is a glance, not a setting.
const VIEW_TABS = [
  { label: "Measurements", value: "measurements" as const },
  { label: "Reagents", value: "reagents" as const },
  { label: "Panel vs reads", value: "mismatch" as const },
  { label: "Undeclared barcodes", value: "undeclared" as const },
  { label: "Scores", value: "score" as const },
  { label: "Reference readings", value: "reference" as const },
  { label: "Fitted background", value: "background" as const },
];
const activeView = ref<
  "measurements" | "reagents" | "mismatch" | "undeclared" | "score" | "reference" | "background"
>("measurements");

const DECILE_VALUE = "pl7.app/antigen/qcDecileValue";
const DECILE_AXIS = "pl7.app/antigen/qcDecile";
const DISTRIBUTION_AXIS = "pl7.app/antigen/qcDistribution";
const BACKGROUND_MEAN = "pl7.app/antigen/fittedBackgroundMean";
const SIGNAL_MEAN = "pl7.app/antigen/fittedSignalMean";
const BACKGROUND_WEIGHT = "pl7.app/antigen/fittedBackgroundWeight";

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
const backgroundOptions = computed<PredefinedGraphOption<"scatterplot">[]>(() => [
  { inputName: "x", selectedSource: col(BACKGROUND_MEAN, "Double") },
  { inputName: "y", selectedSource: col(SIGNAL_MEAN, "Double") },
]);

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

      <template v-if="activeView === 'measurements'">
        <PlAlert v-if="qcAbsent" type="info">
          No quality measurements have arrived from this run yet. Every measurement the verdict
          stage declares keeps a row once the report imports, including one nothing could compute —
          so this is a run still in flight or a verdict stage that did not finish, not a run that
          was measured and found clean.
        </PlAlert>
        <PlAgDataTableV2
          v-else
          v-model="app.model.data.runQualityTableState"
          :settings="qcSettings"
          no-rows-text="The report imported with no measurements in it. Every declared measurement should keep a row — a deferred one carries no status and gives its reason in place of a value — so an empty report means the measurements were lost on the way here, not that the run was clean."
          show-export-button
        />
      </template>

      <template v-else-if="activeView === 'reagents'">
        <PlAlert v-if="reagentAbsent" type="info">
          No reagent table has arrived from this run yet. It is taken by the same verdict stage as
          the measurements, so it arrives with them.
        </PlAlert>
        <PlAgDataTableV2
          v-else
          v-model="app.model.data.reagentTableState"
          :settings="reagentSettings"
          no-rows-text="The table imported with no reagents in it. Every declared barcode keeps a row under every identity it carries — a dead one reads zero under Seen in — so an empty table means the panel reached this stage with nothing declared, not that the reagents were clean."
          show-export-button
        />
      </template>

      <template v-else-if="activeView === 'mismatch'">
        <PlAlert v-if="mismatchAbsent" type="info">
          The panel-versus-reads check has not reported from this run yet. It is taken by the same
          verdict stage as the measurements, so it arrives with them.
        </PlAlert>
        <PlAgDataTableV2
          v-else
          v-model="app.model.data.runQualityMismatchTableState"
          :settings="mismatchSettings"
          no-rows-text="Every barcode the panel declared was carried by reads, which is the outcome you want. The opposite direction is not reported here: barcode correction snaps each feature onto the panel before counting, so a barcode the panel never declared cannot reach this check. See the Undeclared barcodes tab for that direction."
          show-export-button
        />
      </template>

      <template v-else-if="activeView === 'undeclared'">
        <PlAlert v-if="undeclaredAbsent" type="info">
          The undeclared-barcode table has not reported from this run yet. It is taken by the same
          verdict stage as the measurements, so it arrives with them.
        </PlAlert>
        <PlAgDataTableV2
          v-else
          v-model="app.model.data.undeclaredBarcodesTableState"
          :settings="undeclaredSettings"
          no-rows-text="No row here for any sample: every barcode the pre-refine pass saw was on some sample's panel. That is the outcome to want, not a check that failed to run."
          show-export-button
        />
      </template>

      <PlAlert v-else-if="distributionsAbsent" type="info">
        No distributions have arrived from this run yet. They are taken by the same verdict stage as
        the measurements, so they arrive with them.
      </PlAlert>

      <div v-else-if="activeView === 'score'" :class="$style.plot">
        <PlAlert v-if="rungUnknown" type="info">
          This run has not reported which baseline served it yet.
        </PlAlert>
        <PlAlert v-else-if="noScores" type="info">
          This run was read against each tag's own distribution, which yields a probability rather
          than a score, so there are no scores to spread. This plot is drawn for a run read against
          a declared baseline tag.
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
        <PlAlert v-else-if="noReferenceReadings" type="info">
          This run was read against each tag's own distribution. That baseline belongs to a tag in a
          sample rather than to a cell, so no cell carries a reference reading and there is nothing
          to spread. This plot is drawn for a run read against a declared baseline tag.
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
        <PlAlert v-else-if="noBackgrounds" type="info">
          This run was read against a declared baseline tag, so no background was fitted and there
          is nothing to draw here. The other two plots are unaffected. This plot is drawn for a run
          read against each tag's own distribution.
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
/* GraphMaker fills its container, and a container with no height collapses to nothing. */
.plot {
  height: 480px;
}

.qc-cell-list {
  padding: 4px 0 12px;
  font-size: 13px;
  color: var(--color-txt-03);
}
</style>
