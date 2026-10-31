<script setup lang="ts">
import type { QcMeasurementStatus } from "@platforma-open/milaboratories.feature-integration.model";
import {
  PL_PLACEHOLDER_TEXTS,
  PlAgCellStatusTag,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlDropdown,
  PlPlaceholder,
  PlRow,
  PlTabs,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { GraphMaker } from "@milaboratories/graph-maker";
import type { PredefinedGraphOption } from "@milaboratories/graph-maker";
import strings from "@milaboratories/strings";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import FittedBackgroundGrid from "../components/FittedBackgroundGrid.vue";
import { qcStatusTag } from "../results";

const app = useApp();

// PlAgDataTableV2 hands its `cellRendererSelector` to `defaultColDef`, so one selector sees every column
// of the table and must return undefined for the ones it does not claim. `colDef.context` is the column
// spec the table was built from, which is how a column is recognised by p-column name rather than by
// header text or position.
const UNDECLARED_STATUS = "pl7.app/antigen/undeclaredBarcodeStatus";

type RendererParams = {
  value?: unknown;
  colDef?: { context?: { type?: string; id?: unknown; spec?: { name?: string } } };
};

function specName(params: RendererParams): string | undefined {
  return params.colDef?.context?.spec?.name;
}

// `qcStatusTag` maps the software's vocabulary onto the tag component's, and returns undefined where no
// line stands behind the measurement -- which renders as an ordinary cell rather than a fourth colour.
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

// Two readings of the same run, on one page: how each reagent behaved, and whether the barcodes the
// sequencer returned were the ones we declared.
//
// This page is the RUN's quality, never the sample's. "Per-sample QC" shows the mitool per-sample stats,
// one row per sample, and each sample's own Quality Checks tab carries its declared measurement list. The
// pages are named apart for that reason, since "QC" alone would read as two views of one set of numbers.
const reagentSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.reagentTable,
});

// The plot opens on the panel-assigned fraction: it is the measure whose line the block warns on, so a
// reader lands on the question the Quality column already raised. Read from the emitted specs rather than
// written out here, so a change in the workflow cannot leave this naming a column that no longer exists.
const PANEL_ASSIGNED = "pl7.app/qc/panelAssignedFraction";

const ANTIGEN_UMI = "pl7.app/antigen/rawUmiCount";
const BARCODE_AXIS = "pl7.app/antigen/tagId";

// A violin per barcode: the shape behind the median the Antigens table reports. The barcode axis is
// grouped by DEFAULT rather than left to the reader, because a distribution pooled over every barcode
// answers nothing -- the question is whether EACH reagent separated from zero. Faceting by sample is
// left to the reader: it is a second question, and one violin per barcode already answers the first.
const countsDefaults = computed((): PredefinedGraphOption<"discrete">[] => {
  const spec = app.model.outputs.cellTagCountsPcols?.find((s) => s.name === ANTIGEN_UMI);
  if (spec === undefined) return [];
  const options: PredefinedGraphOption<"discrete">[] = [{ inputName: "y", selectedSource: spec }];
  const barcode = spec.axesSpec.find((a) => a.name === BARCODE_AXIS);
  if (barcode !== undefined)
    options.push({ inputName: "primaryGrouping", selectedSource: barcode });
  return options;
});

const CELL_SCORE = "pl7.app/antigen/cellScore";

const scoreDefaults = computed((): PredefinedGraphOption<"histogram">[] => {
  const spec = app.model.outputs.cellScorePcols?.find((s) => s.name === CELL_SCORE);
  return spec === undefined ? [] : [{ inputName: "value", selectedSource: spec }];
});

const CELL_BACKGROUND = "pl7.app/antigen/cellBackground";

const referenceDefaults = computed((): PredefinedGraphOption<"histogram">[] => {
  const spec = app.model.outputs.cellBackgroundPcols?.find((s) => s.name === CELL_BACKGROUND);
  return spec === undefined ? [] : [{ inputName: "value", selectedSource: spec }];
});

const assignmentDefaults = computed((): PredefinedGraphOption<"discrete">[] => {
  const spec = app.model.outputs.panelQcPcols?.find((s) => s.name === PANEL_ASSIGNED);
  return spec === undefined ? [] : [{ inputName: "y", selectedSource: spec }];
});

const undeclaredSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.undeclaredBarcodesTable,
});

// A missing V(D)J dataset is an UNCONFIGURED block, never a finished run. `args()` throws without one, so
// no run reaches this page in that state and nothing here can be describing a run's outcome. Read from data
// rather than from an output, because the point is what the user has chosen, including before the next run.
//
// The workflow does gate the verdict stage on the same ref, but that guard is for a hand-driven render --
// main.tpl says the settings-knowable refusal belongs in the model, which is where it is.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// Every grid is drawn unconditionally, and the three states a frame can be in are answered inside it.
// `usePlDataTableSettingsV2` reads the `withStatus` wrapper: while the run is in flight the grid draws the
// processing placeholder, once it settles with no frame the grid draws `notReadyText`, and a frame that
// arrived with no rows draws `noRowsText`. An alert above the grid was a fourth surface saying what the
// grid says itself, and while the run was in flight it said the report had not arrived, which reads as a
// finished run with nothing in it.
//
// `ok === false` reaches the grid too, which renders the error it was handed. Swallowing it would report a
// failure as a choice the user made.

// --- the distributions ---------------------------------------------------------------------------
//
// Three distributions, two of which exist so a scientist can place a number: the spread of the run's scores
// is where a cutoff goes when the scores separate, and the fitted background is the only way to see whether
// a tag's counts separated at all. The third, the reference reading across cells, informs the gate. Each is
// drawn on the rungs that produce it, and says so on the rungs that do not.
//
// The score spread and the reference reading are GraphMaker histograms over per-cell p-frames
// (`cellScorePf`, `cellBackgroundPf`). Only the fitted background is drawn from `tagCountBins`, one JSON
// output: values rather than a p-frame handle, because its grid takes its bins as values.
//
// While the run is in flight the plot shows the processing placeholder, never a sentence saying the
// distributions have not arrived: that sentence reads as a finished run that reported nothing.
//
// Readiness is read from `isRunning` first, then from each plot's own source -- a GraphMaker from its own
// p-frame, the fitted-background grid from the JSON itself -- never from another output's status wrapper.
// The tab strip above already gates on `isRunning`, so this is one signal rather than two that could
// disagree.

// The rung that actually SERVED, not the one requested. Undefined until the run reports its own meta, and
// that case is NOT "some other rung served" -- reading it that way told a reader both that there were no
// scores and that there was no background, which cannot both be true of one run.
const served = computed(() => app.model.outputs.verdictRunMeta?.referenceChoice);

const rungUnknown = computed(() => served.value === undefined);

// Only the declared rung produces a score: the others yield a probability, which is not on the same scale.
// Only a population rung fits a background. So each of these is empty on exactly the runs the other is not,
// and neither is a failure.
const noScores = computed(() => !rungUnknown.value && served.value !== "declared");
const noBackgrounds = computed(() => !rungUnknown.value && served.value !== "distribution");

// A population baseline is keyed (sample, identity), not by cell, so `reference_by_cell` refuses that rung
// and no per-cell reading exists to spread.
const noReferenceReadings = computed(() => !rungUnknown.value && served.value === "distribution");

// One tab per view. Five readings stacked put the one a reader wants below the fold, and a tab lets each
// view own the full height a chart or a grid needs. Local rather than stored.
//
// A plot the served baseline cannot produce gets NO tab. The four grids are always there. While the run has
// not reported which rung served it, all three plots are offered and each says so when opened -- the rung
// is unknown at that point, not known to be wrong.
type ViewTab =
  | "reagents"
  | "counts"
  | "undeclared"
  | "assignment"
  | "score"
  | "reference"
  | "background";

// Named for what a biologist is looking at, and ordered by grain: barcode, then sample, then cell. The
// two barcode tables sit together because they are one question in two halves -- what the panel
// declared, and what turned up that it did not.
//
// SHORT, because these share the title line with the grid's own Filters / Columns / Export and the full
// names clipped the last tab off the row. "Antigens" and "Off-panel" carry the pairing between them by
// sitting adjacent rather than by repeating the word "barcodes" twice.
//
// `score` and `background` carry the SAME label. They are the two routes to one answer -- where the
// line between signal and background sits -- and a run takes exactly one of them, so a reader never
// meets both. `rungUnknown` is the case where neither is ruled out yet, and there the background
// tab is withheld so the duplicate label cannot surface.
const VIEW_TABS = computed(() => [
  { label: "Antigens", value: "reagents" as const },
  { label: "Antigen counts", value: "counts" as const },
  { label: "Off-panel", value: "undeclared" as const },
  { label: "Assignment", value: "assignment" as const },
  ...(noReferenceReadings.value ? [] : [{ label: "Background", value: "reference" as const }]),
  ...(noScores.value ? [] : [{ label: "Signal vs background", value: "score" as const }]),
  ...(noBackgrounds.value || rungUnknown.value
    ? []
    : [{ label: "Signal vs background", value: "background" as const }]),
]);

const activeView = ref<ViewTab>("reagents");

// VIEW_TABS is derived from the rung, and the rung is unreported until the run settles, so a strip drawn
// mid-run offers every plot and then drops the ones that rung cannot draw. It stays hidden until then. The
// open view's body keeps rendering and draws its own processing placeholder.
const isRunning = computed(() => app.model.outputs.isRunning === true);

// The open tab can stop existing: the run reports its rung, and the plot that tab held cannot be drawn.
// Falling back keeps the page showing something rather than an empty body under a tab strip that no longer
// offers the tab. Watching an output and writing a LOCAL ref is not a hairpin: nothing here reaches
// server-stored data.
watch(VIEW_TABS, (tabs) => {
  if (!tabs.some((t) => t.value === activeView.value)) activeView.value = "reagents";
});

// The fitted background is a GRID of small multiples, one panel per (sample, tag), drawn from the binned
// counts rather than from the frame's two fitted means. Two means cannot draw the two humps a reader is
// asked to judge; the bins can, and they carry the means alongside for the reading. The two means side by
// side are the whole reading: a background alone says nothing about whether the counts separated, and a tag
// that bound nothing shows as two means almost on top of each other. The grid holds no chart configuration,
// because it asks one question and offers no axes to pick.
const tagBins = computed(() => app.model.outputs.tagCountBins);

const backgroundSample = ref<string | undefined>(undefined);

const backgroundSampleOptions = computed(() => {
  const labels = app.model.outputs.sampleLabels ?? {};
  return Object.keys(tagBins.value?.bySample ?? {})
    .map((id) => ({ value: id, label: labels[id] ?? id }))
    .sort((a, b) => a.label.localeCompare(b.label));
});

// Keep the current selection if it still exists, otherwise fall back to the first sample. A re-run can
// drop the sample that was on screen, and an empty selector next to a full grid looks broken.
//
// The selector shows even when there is only one sample, because the panel titles no longer name it.
watch(
  backgroundSampleOptions,
  (options) => {
    if (!options.some((o) => o.value === backgroundSample.value)) {
      backgroundSample.value = options[0]?.value;
    }
  },
  { immediate: true },
);

// Status is rendered as the plain string the workflow emitted, with the discrete filter its spec declares.
// It stays plain text because of the fourth case a tag cannot render: a measurement with no line behind it
// leaves this column empty, and an empty cell beside three tags reads as a tag that failed to load. Which
// of the two no-status cases it is reads from the value column, not from here.
</script>

<template>
  <PlBlockPage>
    <template #title>Panel QC</template>

    <PlAlert v-if="noDataset" type="warn">
      {{ strings.callToActions.configureSettingsAndRun }}
    </PlAlert>

    <template v-else>
      <!-- Every figure says which cell list it was computed against. One list serves the whole run, so it is
           stated once here rather than repeated on each measurement. -->

      <!-- A row of their own, below the title. Not the title line: the grid pins its Filters / Columns /
           Export there and exposes no prop to move them, so tabs placed beside them either crowd off the
           right edge or displace the heading. -->
      <PlTabs v-if="!isRunning" v-model="activeView" :options="VIEW_TABS" />

      <PlAgDataTableV2
        v-if="activeView === 'reagents'"
        v-model="app.model.data.reagentTableState"
        :settings="reagentSettings"
        :not-ready-text="strings.callToActions.configureSettingsAndRun"
        no-rows-text="No barcodes in the table. Every declared barcode keeps a row even when it was never detected, reading 0 under Detected in samples, so an empty table means your panel declared nothing."
        show-export-button
      />

      <GraphMaker
        v-else-if="activeView === 'counts'"
        v-model="app.model.data.antigenCountsGraphState"
        chart-type="discrete"
        default-palette="bright"
        :p-frame="app.model.outputs.cellTagCountsPf"
        :data-state-key="app.model.outputs.cellTagCountsPf"
        :default-options="countsDefaults"
        :status-text="{ noPframe: { title: strings.callToActions.configureSettingsAndRun } }"
      />

      <PlAgDataTableV2
        v-else-if="activeView === 'undeclared'"
        v-model="app.model.data.undeclaredBarcodesTableState"
        :settings="undeclaredSettings"
        :cell-renderer-selector="undeclaredCellRenderer"
        :not-ready-text="strings.callToActions.configureSettingsAndRun"
        no-rows-text="No rows: every barcode found in the reads matched an antigen in your panel. That is the outcome to want, not a check that failed."
        show-export-button
      />

      <GraphMaker
        v-else-if="activeView === 'assignment'"
        v-model="app.model.data.panelAssignmentGraphState"
        chart-type="discrete"
        default-palette="bright"
        :p-frame="app.model.outputs.panelQcPf"
        :data-state-key="app.model.outputs.panelQcPf"
        :default-options="assignmentDefaults"
        :status-text="{ noPframe: { title: strings.callToActions.configureSettingsAndRun } }"
      />

      <div v-else-if="isRunning" :class="$style.plot">
        <PlPlaceholder
          variant="graph"
          :title="PL_PLACEHOLDER_TEXTS.RUNNING.title"
          :subtitle="PL_PLACEHOLDER_TEXTS.RUNNING.subtitle"
        />
      </div>

      <!-- No "wrong rung" alert in any of the three plot bodies. A rung that cannot produce the plot takes
           its tab away, so the body is reachable only while the rung is unknown or while it is the one that
           produces the plot. -->
      <!-- The score column rather than the binned JSON: narrowed by the software to the same population
           the spread was taken over, with the bound cutoff reaching the plot as the column's own
           `pl7.app/graph/thresholds`. -->
      <GraphMaker
        v-else-if="activeView === 'score'"
        v-model="app.model.data.scoreDistributionGraphState"
        chart-type="histogram"
        default-palette="bright"
        :p-frame="app.model.outputs.cellScorePf"
        :data-state-key="app.model.outputs.cellScorePf"
        :default-options="scoreDefaults"
        :status-text="{ noPframe: { title: strings.callToActions.configureSettingsAndRun } }"
      />

      <!-- The per-cell column rather than the binned JSON beside it: the frame the software writes is
           already narrowed to the cell list, so this is the same population the histogram showed, with the
           bin count now the reader's to move. The sticky-cell gate reaches the plot as the column's own
           `pl7.app/graph/thresholds`, and is absent where no gate is declared. -->
      <GraphMaker
        v-else-if="activeView === 'reference'"
        v-model="app.model.data.referenceReadingGraphState"
        chart-type="histogram"
        default-palette="bright"
        :p-frame="app.model.outputs.cellBackgroundPf"
        :data-state-key="app.model.outputs.cellBackgroundPf"
        :default-options="referenceDefaults"
        :status-text="{ noPframe: { title: strings.callToActions.configureSettingsAndRun } }"
      />

      <div v-else :class="$style.plot">
        <PlAlert v-if="rungUnknown" type="info">
          {{ strings.callToActions.configureSettingsAndRun }}
        </PlAlert>
        <PlAlert v-else-if="tagBins === undefined" type="info">
          {{ strings.callToActions.configureSettingsAndRun }}
        </PlAlert>
        <template v-else>
          <PlRow>
            <PlDropdown
              v-model="backgroundSample"
              :options="backgroundSampleOptions"
              label="Sample"
            />
          </PlRow>

          <!-- Barcodes read in the panel's DECLARED order once the grid is scoped to one sample, so a
                     barcode holds the same slot whichever sample is shown. Across every sample the grid reads
                     down one reagent instead, where alphabetical-by-label is the order that column needs. -->
          <FittedBackgroundGrid
            :bins="tagBins"
            :sample-labels="app.model.outputs.sampleLabels ?? {}"
            :only-sample="backgroundSample"
            :tag-order="tagBins.tagOrder"
          />
        </template>
      </div>
    </template>
  </PlBlockPage>
</template>

<style module>
/* A plot fills its container, and a container with no height collapses to nothing.
   PlBlockPage's body is a flex column in a `minmax(0, 1fr)` grid row, so `flex: 1` takes the
   height the tabs and alerts above leave. The floor holds room for a faceted grid. */
.plot {
  flex: 1;
  min-height: 480px;
}
</style>

<!-- Every chart here titles itself inside the plot, so GraphMaker's heading is seeded empty -- and it
     renders that row anyway, leaving 68px of blank above each plot. No prop reaches it; same workaround
     `titeseq-analysis` carries. -->
<style scoped>
:deep(.graph-maker .chart_title),
:deep(.graph-maker .chart_titleEdit) {
  display: none;
}

:deep(.graph-maker .chart_header) {
  margin-bottom: 0;
}

:deep(.graph-maker .chart_container) {
  padding-top: 0;
}
</style>
