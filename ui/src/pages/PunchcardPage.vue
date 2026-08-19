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
import {
  PUNCH_COLUMN_NAME,
  PUNCH_IDENTITY_DOMAIN,
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
// Identified from the column's own SPEC, which the grid hands back on `colDef.context`, and never by
// matching the identity against the column id. Two ways that string match was wrong, one root:
//
//   1. A column id is `identityPunch_<substituteSpecialCharacters(identity)>`, and the SDK's substitution
//      rewrites `-`, space, `.`, `/`, `(`, `)` and more to `_`. So an identity containing any of them —
//      every antigen name under a property grouping — never matched its OWN column, and the tooltip
//      silently lost both the antigen line and the merged note.
//   2. `.includes()` is a substring test, so `SpikeWT` also matched `identityPunch_SpikeWT_alt`. The
//      hover panel then named the wrong antigen with no sign anything was amiss, which is worse than
//      naming none.
//
// The spec is the exact handle: the identity travels in the column's domain, put there by
// identityPivotImportSpec, and reading it needs no knowledge of how an id is spelled. If the grid ever
// stops supplying `context` the punch renderer simply does not apply, and the card renders raw values —
// visibly broken, rather than quietly mislabelled.
type PunchColumnContext = {
  type?: string;
  spec?: { name?: string; domain?: Record<string, string>; annotations?: Record<string, string> };
};

const identityOfColumn = (params: {
  colDef?: { context?: PunchColumnContext };
}): string | undefined => {
  const spec = params.colDef?.context?.spec;
  if (spec === undefined || spec.name !== PUNCH_COLUMN_NAME) return undefined;
  return spec.domain?.[PUNCH_IDENTITY_DOMAIN];
};

// An unplaced identity gets a note explaining ITSELF. Its column header reads as a bare barcode where
// every other header is an antigen, and nothing on the header says why. The banner above the card says
// some barcodes carry no grouping value, but it sits far from the column it is about. Attaching the note
// to the column's cells puts the explanation where the reader's cursor already is.
//
// An unplaced identity IS its barcode. A tag the grouping column says nothing about becomes its own
// identity. This is therefore an exact set membership test, not a search.
//
// It does NOT cover a barcode the panel names differently in different samples. That used to land here,
// because a dataset-wide map could hold only one value per tag and a second one read as a conflict. The
// panel is now read per tag AND sample, so such a barcode is placed under each name its own sample
// declared and never reaches this note.
const mergedNote = (identity: string | undefined): string | undefined => {
  if (identity === undefined || !ungroupedTags.value.includes(identity)) return undefined;
  return (
    `Unplaced: the panel gives barcode ${identity} no value in the grouping column, ` +
    `so there is nothing to group it under — it stands alone under its own sequence. ` +
    `Every other column is an antigen the panel named.`
  );
};

// Identity -> full label, from the identity options output (which carries the workflow's label).
const labelOf = computed(() => {
  const m: Record<string, string> = {};
  for (const o of identityOptions.value) m[o.value] = o.label;
  return m;
});

const cellRendererSelector = (params: { colDef?: { context?: PunchColumnContext } }) => {
  const identity = identityOfColumn(params);
  if (identity === undefined) return undefined;
  return {
    component: PunchCell,
    params: {
      // The full name travels to the cell because a reader who hovers a dot far down a long grid cannot
      // see the header row at all. The options output supplies the label.
      antigen: labelOf.value[identity] ?? identity,
      mergedNote: mergedNote(identity),
    },
  };
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
const baselineDegraded = computed(
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

// Headers carry the identity's full name. A cut to 20 characters was applied here before. It removed the
// one thing a reader needs from a header, which is the identity the column holds.
//
// The grid auto-sizes every column to its contents and exposes no width a block can set, so a long label
// does make its column wide. Every column is resizable, and the hover below names the identity as well.

// An "Antigens shown" multi-select used to sit above the legend, narrowing the card to the identities it
// had picked and holding that pick in block data. Removed: PlAgDataTableV2 ships a columns panel and a
// filters panel, both live on this table, so the control re-implemented in block state something the grid
// already did — and two narrowing mechanisms can disagree with each other, where the grid's own cannot
// disagree with itself. Every identity column the pivot produced renders now, and narrowing is done in the
// grid. The options output stays, because the card reads two other things off it.
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
        No baseline served this run, so every reading was taken against nothing and each punch is
        drawn from a count alone. Treat the whole grid as unsettled.
      </PlAlert>
      <PlAlert v-else-if="baselineDegraded" type="warn">
        The baseline that served was not the one requested: asked for
        <b>{{ requestedLabel }}</b
        >, served <b>{{ servedLabel }}</b
        >. Every punch below was read against what served.
      </PlAlert>

      <PlAlert v-if="ungroupedTags.length > 0" type="info">
        {{ ungroupedTags.length }} declared
        {{ ungroupedTags.length === 1 ? "barcode carries" : "barcodes carry" }} no value in the
        grouping column, so each stands as its own identity under its raw sequence.
      </PlAlert>

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
