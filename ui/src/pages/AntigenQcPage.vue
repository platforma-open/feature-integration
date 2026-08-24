<script setup lang="ts">
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
    </template>
  </PlBlockPage>
</template>
