<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdownMulti,
  PlMaskIcon24,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import {
  PUNCH_COLUMN_ID_PREFIX,
  PUNCH_COLUMN_KEY_PREFIX,
  REFERENCE_SOURCE_LABELS,
} from "@platforma-open/milaboratories.feature-integration.model";
import { computed, ref } from "vue";
import { useApp } from "../app";
import PunchCell from "../components/PunchCell.vue";
import PunchLegend from "../components/PunchLegend.vue";
import VerdictSettings from "../components/VerdictSettings.vue";

const app = useApp();

// Every clonotype set against every picked identity: rows are the sets, columns are the identities, and a
// cell is one punch. This is the reading `block-set` calls this block's own view — every clonotype against
// every identity, each position in one of the four states with what it rests on beside it.
const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.punchcardTable,
});

// Only the antigen columns are punches. The grid applies a renderer through `defaultColDef`, so a selector
// that answered unconditionally replaced EVERY cell — the row number and the clonotype label rendered as
// "unreadable value" marks, because a clone id is not a verdict and never parses as one. The columns this
// table carries are the punch family plus the clonotype axis and whatever label columns the pool supplies
// for it, and only the first should be drawn.
//
// Matched on the column id, because the grid layer sees ids rather than specs. The prefix is exported by
// the model rather than written here, so the coupling to the workflow's p-frame key has one home.
//
// A merged identity also gets a note explaining ITSELF. Its column header reads as a joined label
// (`SpikeWT / SpikeWT__alt`) where every other header is a single antigen, and nothing on the header
// says why. The banner above the card says two barcodes lost their grouping value, but it sits far from
// the column it is about and names barcodes rather than the label now shown. Attaching the note to the
// column's cells puts the explanation where the reader's cursor already is.
const mergedNote = (colId: string): string | undefined => {
  const merged = ungroupedTags.value.find((tag) =>
    colId.includes(`${PUNCH_COLUMN_ID_PREFIX}${tag}`),
  );
  if (merged === undefined) return undefined;
  return (
    `merged: the panel gives barcode ${merged} a different name in different samples, ` +
    `so there is no single value to group it under — it stands alone, labelled with both names. ` +
    `Every other column groups on one agreed name.`
  );
};

const cellRendererSelector = (params: { colDef?: { colId?: string } }) => {
  const colId = String(params.colDef?.colId ?? "");
  if (!colId.includes(PUNCH_COLUMN_KEY_PREFIX)) return undefined;
  return { component: PunchCell, params: { mergedNote: mergedNote(colId) } };
};

// The reading's own settings, reachable from the page they explain.
const settingsOpen = ref(false);

// A missing V(D)J dataset is a legitimate state rather than a half-filled form: the block runs, and the
// verdict stage alone is skipped. Read from data rather than from an output, because the point is what the
// user has chosen — including before the next run.
//
// This is a limit of how the stage is currently WIRED, not of the view. A row here is a clonotype or
// whatever else was rolled up, the read is taken per cell before anything is combined, and the software
// already accepts a cell list in place of a linker — so rows of cells are drawable in principle. What
// stands in the way is that main.tpl gates the whole verdict stage on the dataset ref. The page says what
// is true today and does not dress it up as a property of the punchcard.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// What the run was actually answered under. The software degrades a comparator it cannot serve, so the
// choice that SERVED is the only one worth stating: a reader meeting a grid of rings otherwise has nothing
// telling them the comparator they asked for was never available.
const runMeta = computed(() => app.model.outputs.verdictRunMeta);
// Compared against the machine token, not against a sentence. This branch used to string-match the
// display prose the Python enum happened to carry, so rewording that sentence for readability would have
// silently removed the warning below with nothing failing.
const noComparator = computed(() => runMeta.value?.referenceChoice === "none");
// Display wording comes from the model, which owns it for the pre-run dropdown too, so a comparator does
// not change its name once it has served.
const requestedLabel = computed(() =>
  runMeta.value ? REFERENCE_SOURCE_LABELS[runMeta.value.referenceSourceRequested] : "",
);
const servedLabel = computed(() =>
  runMeta.value ? REFERENCE_SOURCE_LABELS[runMeta.value.referenceChoice] : "",
);
const comparatorDegraded = computed(
  () =>
    runMeta.value !== undefined &&
    runMeta.value.referenceSourceRequested !== runMeta.value.referenceChoice,
);

// Tags the grouping column said nothing about stand as their own identity under a bare barcode. The
// software reports this to stderr; a column a reader cannot place needs saying on the page too.
const ungroupedTags = computed(() => runMeta.value?.tagsWithoutGroupingValue ?? []);

const identityOptions = computed(() => app.model.outputs.punchcardIdentityOptions ?? []);

// The pivot is size-gated upstream: a panel above the limit emits no identity columns at all, so a run can
// have produced verdicts and still have nothing here to draw. That is a different thing from a narrowed
// view, and it needs saying, because an empty grid looks the same either way.
const nothingToOffer = computed(() => !noDataset.value && identityOptions.value.length === 0);

// Empty means every antigen, which is the default the page opens on. Stated because an empty multi-select
// usually means the opposite, and a reader who assumes "none" will not trust a full grid.
const narrowed = computed(() => app.model.data.punchcardIdentities.length > 0);
</script>

<template>
  <PlBlockPage>
    <template #title>Punchcard</template>
    <template #append>
      <PlBtnGhost @click.stop="settingsOpen = true">
        Settings
        <template #append>
          <PlMaskIcon24 name="settings" />
        </template>
      </PlBtnGhost>
    </template>

    <PlAlert v-if="noDataset" type="warn">
      This run has no rows to punch: the verdict stage only runs once a single-cell V(D)J dataset is
      picked, so the run counted barcodes per cell and stopped there. Pick a dataset in Settings and
      run again.
    </PlAlert>

    <PlAlert v-else-if="nothingToOffer" type="info">
      This run produced no per-identity columns to draw. The punchcard costs one column per antigen
      identity, so it is emitted only for panels below the block's identity limit; a larger panel
      still produced its verdicts, and they are still exported to downstream blocks.
    </PlAlert>

    <template v-else>
      <PlAlert v-if="noComparator" type="warn">
        No comparator served this run, so every reading was taken against nothing and each punch is
        drawn from a count alone. Treat the whole grid as unsettled.
      </PlAlert>
      <PlAlert v-else-if="comparatorDegraded" type="warn">
        The comparator that served was not the one requested: asked for
        <b>{{ requestedLabel }}</b
        >, served <b>{{ servedLabel }}</b
        >. Every punch below was read against what served.
      </PlAlert>

      <PlAlert v-if="ungroupedTags.length > 0" type="info">
        {{ ungroupedTags.length }} declared
        {{ ungroupedTags.length === 1 ? "barcode carries" : "barcodes carry" }} no value in the
        grouping column, so each stands as its own identity under its raw sequence.
      </PlAlert>

      <PlDropdownMulti
        v-model="app.model.data.punchcardIdentities"
        :options="identityOptions"
        :label="
          narrowed
            ? `Antigens shown (${app.model.data.punchcardIdentities.length} of ${identityOptions.length})`
            : `Antigens shown (all ${identityOptions.length})`
        "
      />

      <PunchLegend />

      <PlAgDataTableV2
        v-if="app.model.outputs.punchcardTable"
        v-model="app.model.data.punchcardTableState"
        :settings="tableSettings"
        :cell-renderer-selector="cellRendererSelector"
        show-export-button
      />
    </template>

    <PlSlideModal v-model="settingsOpen" width="448px">
      <template #title>Binding verdict settings</template>
      <VerdictSettings />
    </PlSlideModal>
  </PlBlockPage>
</template>
