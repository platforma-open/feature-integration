<script setup lang="ts">
import {
  PlAccordionSection,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGroup,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

const app = useApp();

// The measurement set: one row per (level, panel, measured thing, measurement), with the status, the
// coverage triple beside it and the reason for anything deferred. Every declared measurement keeps its
// row whether or not this run could compute it, so an absent row is never how a reader learns anything.
const qcSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.antigenQcTable,
});

// The panel-versus-reads check, keyed (panel, tag). Both directions travel in the one frame under the
// direction column, so one table carries them both and the discrete filter narrows to either.
const mismatchSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.antigenPanelMismatchTable,
});

// Which table is on screen. A local ref rather than block data: each table already persists its own grid
// state, and which of the two a reader is currently looking at is this window's business, not the
// project's — two clients open on the same block should not drag each other between views.
type CheckView = "measurements" | "panel";
const view = ref<CheckView>("measurements");
const viewOptions: { value: CheckView; label: string }[] = [
  { value: "measurements", label: "Quality measurements" },
  { value: "panel", label: "Panel vs reads" },
];

// The same state the verdict page reports. Without a V(D)J dataset the verdict stage never ran, so
// neither of these tables exists. Read from data rather than from an output because the point is what the
// user has chosen, including before the next run.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// How to read the measurement table. Four things a reader cannot recover from the grid itself: what a
// level is, what the rollup row is, that two of the four statuses claim nothing, and that the counts
// beside a status measure coverage rather than severity.
const measurementGuide = [
  "Level says what kind of thing a row is about: a sample, a declared tag, an antigen identity, a panel " +
    "or a capture. Sample and panel are separate levels, not one inside the other — a misbehaving tag is " +
    "usually the reagent across the whole run rather than a fault of any one sample, and reading panels " +
    "as children of samples would mark every sample alerting for one dead reagent. Capture rolls up both.",
  "A row whose measurement is \"rollup\" is that level's summary: the worst status among the level's " +
    "measurements, with the coverage of everything it gathered.",
  "Status has four values and only two of them judge the data. acceptable and alerting were computed and " +
    "a line can be defended for them. unjudged was computed and no line can be defended, so nothing is " +
    'claimed about it. "not evaluated" was never computed at all, and "Why deferred" says what was ' +
    "missing. Neither of the last two enters a rollup, and neither is a pass.",
  "Judged, Unjudged and Not evaluated are coverage — how much of the level was actually checked. They " +
    'sit beside the status rather than inside it, because "nothing here is wrong" and "almost nothing ' +
    'here was checkable" are different answers that a single column would merge.',
  "Nothing here is ordered by severity: a status is not a magnitude, and ranking one would invent one. " +
    "Filter the Status column to reach what needs attention.",
];

// How to read the mismatch table. The direction values are terse enough that a reader meeting them cold
// cannot tell which side of the comparison failed, and an empty table needs saying out loud.
const panelGuide = [
  '"undeclared-in-panel": the reads carried a barcode that no panel declared. "declared-never-seen": a ' +
    "panel declared a tag and no read ever carried it. Both are listed, because either direction alone " +
    "hides half the mismatch.",
  "Rows are keyed by panel and tag rather than by sample: a declared tag the reads never carried is a " +
    'property of the declared tag set, not of one sample. "Samples affected" keeps the samples that ' +
    "reported it, so nothing about where it was seen is lost.",
  "A table with no rows means the reads and every panel agreed — the check ran and found nothing.",
];
</script>

<template>
  <PlBlockPage>
    <template #title>Quality checks</template>

    <PlAlert v-if="noDataset" type="warn">
      The quality measurements and the panel check are produced by the verdict stage, which needs a
      single-cell V(D)J dataset. Without one this run skipped that stage entirely, so neither table
      exists — nothing was measured, rather than everything measuring clean. Select a dataset in the
      Binding verdicts settings and run again.
    </PlAlert>

    <template v-else>
      <PlBtnGroup v-model="view" :options="viewOptions" compact />

      <template v-if="view === 'measurements'">
        <PlAccordionSection label="How to read this">
          <PlAlert type="info">
            <div v-for="(line, i) in measurementGuide" :key="i">{{ line }}</div>
          </PlAlert>
        </PlAccordionSection>
        <PlAgDataTableV2
          v-if="app.model.outputs.antigenQcTable"
          v-model="app.model.data.antigenQcTableState"
          :settings="qcSettings"
          show-export-button
        />
        <PlAlert v-else type="info">
          The quality measurements appear here once the block has run with a V(D)J dataset selected.
        </PlAlert>
      </template>

      <template v-else>
        <PlAccordionSection label="How to read this">
          <PlAlert type="info">
            <div v-for="(line, i) in panelGuide" :key="i">{{ line }}</div>
          </PlAlert>
        </PlAccordionSection>
        <PlAgDataTableV2
          v-if="app.model.outputs.antigenPanelMismatchTable"
          v-model="app.model.data.panelMismatchTableState"
          :settings="mismatchSettings"
          show-export-button
        />
        <PlAlert v-else type="info">
          The panel-versus-reads check appears here once the block has run with a V(D)J dataset
          selected.
        </PlAlert>
      </template>
    </template>
  </PlBlockPage>
</template>
