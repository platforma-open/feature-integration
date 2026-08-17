<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlMaskIcon24,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";
import VerdictSettings from "../components/VerdictSettings.vue";

const app = useApp();

// One row per (clonotype set, antigen identity): the four-state verdict, why an unsettled one is
// unsettled, and the cells behind it. Filtering comes from the column specs — the states and the competed
// flag are discrete filters, and nothing in the family is orderable.
const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.verdictTable,
});

// The reading's own settings, reachable from the page they explain.
const settingsOpen = ref(false);

// A missing V(D)J dataset is a legitimate state, not a half-filled form: the block runs, and the verdict
// stage alone is skipped. Read from data rather than from an output because the point is what the user
// has chosen, including before the next run.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// What the run was actually answered under. The software degrades a comparator it cannot serve, so the
// choice that SERVED is the only one worth stating: a reader meeting a table of unreliable rows otherwise
// has nothing telling them the comparator they asked for was never available.
const runMeta = computed(() => app.model.outputs.verdictRunMeta);
const noComparator = computed(() => runMeta.value?.referenceChoice === "no comparator available");
const comparatorDegraded = computed(
  () =>
    runMeta.value !== undefined &&
    runMeta.value.referenceSourceRequested !== runMeta.value.referenceChoice,
);
// Tags the grouping column said nothing about stand as their own identity, under a bare barcode. The
// software reports this to stderr; a row a reader cannot place needs saying in the page too.
const ungroupedTags = computed(() => runMeta.value?.tagsWithoutGroupingValue ?? []);
</script>

<template>
  <PlBlockPage>
    <template #title>Binding verdicts</template>
    <template #append>
      <PlBtnGhost @click.stop="settingsOpen = true">
        Settings
        <template #append>
          <PlMaskIcon24 name="settings" />
        </template>
      </PlBtnGhost>
    </template>

    <PlAlert v-if="noDataset" type="warn">
      Verdicts are about clonotype sets, so they need a single-cell V(D)J dataset. Without one this
      run skipped the verdict stage entirely — no antigen verdicts, no per-antigen columns and no
      panel check were produced, not merely no verdicts. Select a dataset in Settings and run again;
      the per-cell antigen counts and the QC are unaffected either way.
    </PlAlert>

    <template v-else>
      <PlAlert v-if="noComparator" type="warn">
        This run had no comparator: no tag is designated as the one bound by nothing, and the panel
        is too small to stand in as its own. Every reading is therefore unreliable — the counts were
        made and could not be compared, which is not the same as "not bound". In Settings, either
        name the panel column that declares each tag's role and the values marking the comparator,
        or choose a different source under "What counts are read against".
      </PlAlert>
      <PlAlert v-else-if="comparatorDegraded" type="warn">
        The comparator asked for ({{ runMeta?.referenceSourceRequested }}) could not be served by
        this panel, so the run was answered against {{ runMeta?.referenceChoice }}.
      </PlAlert>
      <PlAlert v-if="ungroupedTags.length" type="info">
        {{ ungroupedTags.length }} tag(s) carry no agreed value in the grouping column and stand as
        their own identity, under a bare barcode: {{ ungroupedTags.slice(0, 8).join(", ")
        }}{{ ungroupedTags.length > 8 ? "…" : "" }}.
      </PlAlert>

      <PlAgDataTableV2
        v-if="app.model.outputs.verdictTable"
        v-model="app.model.data.verdictTableState"
        :settings="tableSettings"
        show-export-button
      />
      <PlAlert v-else type="info">
        Verdicts appear here once the block has run with a V(D)J dataset selected.
      </PlAlert>
    </template>

    <PlSlideModal v-model="settingsOpen" width="40%">
      <template #title>Settings</template>
      <VerdictSettings />
    </PlSlideModal>
  </PlBlockPage>
</template>
