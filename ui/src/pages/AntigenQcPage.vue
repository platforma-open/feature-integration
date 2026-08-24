<script setup lang="ts">
import type { PredefinedGraphOption } from "@milaboratories/graph-maker";
import { GraphMaker } from "@milaboratories/graph-maker";
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlSectionSeparator,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed } from "vue";
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
const qcSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.runQualityTable,
});

const mismatchSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.runQualityMismatchTable,
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

// --- the distributions ---------------------------------------------------------------------------
//
// `330-the-quality-readout` puts three distributions last, and two of them exist so a scientist can
// place a number: the spread of the run's scores is where a cutoff goes when the scores separate,
// and the fitted background is the only way to see whether a tag's counts separated at all. The
// third, the reference reading across cells, informs the gate.
//
// All three read one p-frame. The two decile sets share a `distribution` axis and are told apart by
// a filter GraphMaker applies from the default options below.
const distributionsAbsent = computed(() => app.model.outputs.runQualityDistributions === undefined);

// A run served by a population baseline produces no score, so that plot has nothing to draw and the
// section says so rather than showing an empty chart.
// `referenceChoice` is the rung that actually SERVED, not the one requested: a request the panel
// cannot honour degrades, and the plots follow what happened rather than what was asked for.
const served = computed(() => app.model.outputs.verdictRunMeta?.referenceChoice);
const noScores = computed(() => served.value !== "declared");
// A run served by a declared baseline fits no background, symmetrically.
const noBackgrounds = computed(() => served.value !== "distribution");

const DECILE_VALUE = "pl7.app/antigen/qcDecileValue";
const DECILE_AXIS = "pl7.app/antigen/qcDecile";

// Both decile plots read the same column and differ only in which distribution they filter to, so
// the options are built once and the caller names the slice.
function decileOptions(distribution: string): PredefinedGraphOption<"discrete">[] {
  return [
    {
      inputName: "y",
      selectedSource: { kind: "PColumn", name: DECILE_VALUE, valueType: "Double" },
    },
    { inputName: "x", selectedSource: { name: DECILE_AXIS, type: "Int" } },
    {
      inputName: "filters",
      selectedSource: { name: "pl7.app/antigen/qcDistribution", type: "String" },
      selectedValues: [distribution],
    },
  ] as PredefinedGraphOption<"discrete">[];
}

const scoreOptions = computed(() => decileOptions("score"));
const referenceOptions = computed(() => decileOptions("referenceReading"));

// The two means side by side, which is the whole reading: a background alone says nothing about
// whether the counts separated, and a tag that bound nothing shows as two means almost on top of
// each other rather than as a refusal.
const backgroundOptions = computed(
  () =>
    [
      {
        inputName: "x",
        selectedSource: {
          kind: "PColumn",
          name: "pl7.app/antigen/fittedBackgroundMean",
          valueType: "Double",
        },
      },
      {
        inputName: "y",
        selectedSource: {
          kind: "PColumn",
          name: "pl7.app/antigen/fittedSignalMean",
          valueType: "Double",
        },
      },
    ] as PredefinedGraphOption<"scatterplot">[],
);

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
      <PlAlert v-if="qcAbsent" type="info">
        No quality measurements have arrived from this run yet. Every measurement the verdict stage
        declares keeps a row once the report imports, including one nothing could compute — so this
        is a run still in flight or a verdict stage that did not finish, not a run that was measured
        and found clean.
      </PlAlert>
      <PlAgDataTableV2
        v-else
        v-model="app.model.data.runQualityTableState"
        :settings="qcSettings"
        no-rows-text="The report imported with no measurements in it. Every declared measurement should keep a row — a deferred one carries no status and gives its reason in place of a value — so an empty report means the measurements were lost on the way here, not that the run was clean."
        show-export-button
      />

      <PlSectionSeparator>Panel versus reads</PlSectionSeparator>

      <PlAlert v-if="mismatchAbsent" type="info">
        The panel-versus-reads check has not reported from this run yet. It is taken by the same
        verdict stage as the measurements above, so it arrives with them.
      </PlAlert>
      <PlAgDataTableV2
        v-else
        v-model="app.model.data.runQualityMismatchTableState"
        :settings="mismatchSettings"
        no-rows-text="The panel and the reads agree: every barcode the panel declared was carried by reads, and every barcode the reads carried was declared in the panel. This table is empty because the check found nothing, which is the outcome you want."
        show-export-button
      />

      <PlSectionSeparator>Distributions</PlSectionSeparator>

      <PlAlert v-if="distributionsAbsent" type="info">
        No distributions have arrived from this run yet. They are taken by the same verdict stage as
        the measurements above, so they arrive with them.
      </PlAlert>
      <template v-else>
        <PlAlert v-if="noScores" type="info">
          This run was read against a population baseline, which yields a probability rather than a
          score, so there are no scores to spread. The plot appears on a run read against a declared
          baseline tag.
        </PlAlert>
        <div v-else :class="$style.plot">
          <GraphMaker
            v-model="app.model.data.scoreDistributionGraphState"
            chartType="discrete"
            :p-frame="app.model.outputs.runQualityDistributions"
            :default-options="scoreOptions"
          />
        </div>

        <div :class="$style.plot">
          <GraphMaker
            v-model="app.model.data.referenceReadingGraphState"
            chartType="discrete"
            :p-frame="app.model.outputs.runQualityDistributions"
            :default-options="referenceOptions"
          />
        </div>

        <PlAlert v-if="noBackgrounds" type="info">
          This run was read against a declared baseline tag, so no background was fitted. The plot
          appears on a run read against each tag's own distribution.
        </PlAlert>
        <div v-else :class="$style.plot">
          <GraphMaker
            v-model="app.model.data.fittedBackgroundGraphState"
            chartType="scatterplot"
            :p-frame="app.model.outputs.runQualityDistributions"
            :default-options="backgroundOptions"
          />
        </div>
      </template>
    </template>
  </PlBlockPage>
</template>

<style module>
/* GraphMaker fills its container, and a container with no height collapses to nothing. */
.plot {
  height: 480px;
}
</style>
