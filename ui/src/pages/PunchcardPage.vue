<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlAgTextAndButtonCell,
  PlSlideModal,
  PlTabs,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import type { PTableKey } from "@platforma-open/milaboratories.feature-integration.model";
import {
  CELL_PUNCH_COLUMN_NAME,
  PUNCH_COLUMN_NAME,
  PUNCH_IDENTITY_DOMAIN,
  createPlDataTableStateV2,
} from "@platforma-open/milaboratories.feature-integration.model";
import { computed, ref } from "vue";
import { useApp } from "../app";
import { useClonotypeLabels } from "../clonotypeLabels";
import CellPunchCell from "../components/CellPunchCell.vue";
import PunchCell from "../components/PunchCell.vue";
import PunchLegend from "../components/PunchLegend.vue";

const app = useApp();

// Every clonotype set against every picked identity: rows are the sets, columns are the identities, and a
// cell is one punch.
const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.punchcardTable,
});

// Only the antigen columns are punches. The grid applies a renderer through `defaultColDef`, so a selector
// that answers unconditionally replaces EVERY cell, and the row number and the clonotype label render as
// "unreadable value" marks.
//
// Identified from the column's own SPEC, which the grid hands back on `colDef.context`. Never by matching
// the identity against the column id, which is wrong twice over. An id is
// `identityPunch_<substituteSpecialCharacters(identity)>`, and the substitution rewrites `-`, space, `.`,
// `/`, `(`, `)` and more to `_`, so an identity carrying any of them never matches its OWN column. And
// `.includes()` is a substring test, so `SpikeWT` also matches `identityPunch_SpikeWT_alt` and the hover
// panel names the wrong antigen with no sign anything is amiss.
//
// The identity travels in the column's domain, put there by identityPivotImportSpec. Should the grid ever
// stop supplying `context`, the punch renderer does not apply and the card renders raw values -- visibly
// broken rather than quietly mislabelled.
type PunchColumnContext = {
  type?: string;
  spec?: {
    name?: string;
    domain?: Record<string, string>;
    annotations?: Record<string, string>;
    axesSpec?: { name?: string }[];
  };
};

const identityOfColumnNamed = (
  params: { colDef?: { context?: PunchColumnContext } },
  columnName: string,
): string | undefined => {
  const spec = params.colDef?.context?.spec;
  if (spec === undefined || spec.name !== columnName) return undefined;
  return spec.domain?.[PUNCH_IDENTITY_DOMAIN];
};

const identityOfColumn = (params: {
  colDef?: { context?: PunchColumnContext };
}): string | undefined => identityOfColumnNamed(params, PUNCH_COLUMN_NAME);

// An unplaced identity gets a note explaining ITSELF. Its column header reads as a bare barcode where every
// other header is an antigen. The banner above the card says some barcodes carry no grouping value, but it
// sits far from the column it is about.
//
// An unplaced identity IS its barcode, since a tag the grouping column says nothing about becomes its own
// identity, so this is an exact set membership test rather than a search.
//
// It does NOT cover a barcode the panel names differently in different samples. The panel is read per tag
// AND sample, so such a barcode is placed under each name its own sample declared.
const mergedNote = (identity: string | undefined): string | undefined => {
  if (identity === undefined || !ungroupedTags.value.includes(identity)) return undefined;
  return (
    `Unplaced: the panel gives barcode ${identity} no value in the grouping column, ` +
    `so there is nothing to group it under — it stands alone under its own sequence. ` +
    `Every other column is an antigen the panel named.`
  );
};

// Identity -> full label, from the identity options output, which carries the workflow's label.
const labelOf = computed(() => {
  const m: Record<string, string> = {};
  for (const o of identityOptions.value) m[o.value] = o.label;
  return m;
});

// The clonotype's own column, which is where the row gets its button. The grid hands this column a context
// of `{type: "column", spec: {name: "pl7.app/label", axesSpec: [the clonotype axis]}}`. NOT an axis context,
// even though the value shown is the axis's label: the pool-resolved label column stands in for the axis.
//
// Matched by AXIS as well as by name, against the same axis id the expansion filters on, so the two provably
// agree. Name alone breaks the moment a second label column reaches this frame, which is what happens on the
// by-identity face.
const isClonotypeLabelColumn = (params: { colDef?: { context?: PunchColumnContext } }): boolean => {
  const spec = params.colDef?.context?.spec;
  const axisName = app.model.outputs.clonotypeAxisId?.name;
  if (spec === undefined || axisName === undefined) return false;
  return (
    spec.name === "pl7.app/label" &&
    spec.axesSpec?.length === 1 &&
    spec.axesSpec[0]?.name === axisName
  );
};

const cellRendererSelector = (params: { colDef?: { context?: PunchColumnContext } }) => {
  // The affordance. `invokeRowsOnDoubleClick` makes the button fire the ROW's double-click event, so it
  // routes through the same `openExpansion` handler as a double-click anywhere on the row. Nothing else on
  // a grid of coloured dots says it can be opened.
  //
  // Not `showCellButtonForAxisId`, which renders nothing here and no error: the SDK matches that prop against
  // an axis column's own id or a one-axis label column's id with `isJsonEqual`, and neither branch matches.
  // This route replaces the cell's renderer instead, the same mechanism the punch glyphs use.
  if (isClonotypeLabelColumn(params)) {
    return { component: PlAgTextAndButtonCell, params: { invokeRowsOnDoubleClick: true } };
  }
  const identity = identityOfColumn(params);
  if (identity === undefined) return undefined;
  return {
    component: PunchCell,
    params: {
      // The full name travels to the cell because a reader who hovers a dot far down a long grid cannot see
      // the header row at all.
      antigen: labelOf.value[identity] ?? identity,
      mergedNote: mergedNote(identity),
      showAsked: panelsDiffer.value,
    },
  };
};

// The by-cell face's renderer. Matched on the cell punch's own column NAME, so a column of one card can
// never be drawn by the other card's renderer: the two share an identity domain key but nothing else, and
// the set-level renderer handed a two-field value would report it unreadable.
const cellPunchRendererSelector = (params: { colDef?: { context?: PunchColumnContext } }) => {
  const identity = identityOfColumnNamed(params, CELL_PUNCH_COLUMN_NAME);
  if (identity === undefined) return undefined;
  return { component: CellPunchCell, params: { antigen: labelOf.value[identity] ?? identity } };
};

// The reading's own settings, reachable from the page they explain.

// The expansion: one clonotype's identities read DOWN, opposite this card's read ACROSS. The card stays a
// field of colour with no number in any position, and every number lives in here. The gesture is a
// double-click on the row, matching the Main page's own way of opening a sample report.
//
// `showCellButtonForAxisId` renders NOTHING here, with no error, and is worth re-testing only if the SDK's
// label-column branch changes. The SDK matches that prop with `isJsonEqual` against either an axis column's
// own id or the id of a one-axis LABEL column, and this card displays the clonotype axis through a
// pool-resolved label column, so neither branch matches and the cell renders as plain text. The axis id is
// not the problem: it is derived from an emitted column, domain and all.
//
// The key is all the event carries. `cellButtonClicked` emits a `PTableKey` and nothing else, so the key
// goes into block data and the model builds a table filtered to it. With `expandedSet` undefined the model
// returns no table at all, which matters because an unfiltered expansion would be every clonotype's
// identities at once.
const expansionOpen = computed({
  get: () => app.model.data.expandedSet !== undefined,
  set: (open: boolean) => {
    if (!open) app.model.data.expandedSet = undefined;
  },
});

// Which face of the expansion is showing. `identity` is one row per identity carrying the clonotype's
// verdict, `cell` is one row per cell carrying that cell's own reading at every identity.
//
// Local state rather than block data, and reset on every open. Nothing downstream reads it, no other client
// needs it, and reopening on whichever face was last used would answer a question the reader did not ask.
// Block data would also make it a migration.
const EXPANSION_TABS = [
  { label: "By identity", value: "identity" as const },
  { label: "By cell", value: "cell" as const },
];
type ExpansionTab = (typeof EXPANSION_TABS)[number]["value"];
const expansionTab = ref<ExpansionTab>("identity");

function openExpansion(key?: PTableKey) {
  if (key === undefined) return;
  app.model.data.expandedSet = key as (string | number)[];
  expansionTab.value = "identity";
}

// Seeded on first use rather than required in block data: a required field would need every stored project
// migrated to carry it, and a project saved before the expansion existed would otherwise open with an
// undefined grid state bound to v-model.
const expansionTableState = computed({
  get: () => app.model.data.expansionTableState ?? createPlDataTableStateV2(),
  set: (value) => {
    app.model.data.expansionTableState = value;
  },
});

// Its own grid state, and its own `sourceId`. Two tables over different axes: sharing either would carry one
// face's column order and filters into the other, where none of the column ids resolve.
const cellExpansionTableState = computed({
  get: () => app.model.data.cellExpansionTableState ?? createPlDataTableStateV2(),
  set: (value) => {
    app.model.data.cellExpansionTableState = value;
  },
});

const cellExpansionSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.cellExpansionTable,
  sourceId: () => app.model.data.expandedSet?.join(" "),
});

const expansionSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.expansionTable,
  // The expansion's data source changes on a DOUBLE-CLICK, not on a run. The SDK documents `sourceId` as
  // mandatory for exactly that case -- "when the table can change without block run" -- and without it the
  // component holds the previous source's cached state and the grid sits in Loading until a window reload.
  sourceId: () => app.model.data.expandedSet?.join(" "),
});

// The slide-over's title names the VIEW, never the raw `expandedSet[0]`. That is the scClonotypeKey axis
// value, `04zdk2ezKgFGCgckfLw5H` against a card showing `C-ZDKEZ`, and it names nothing to a reader.
//
// A title of `C-ZDKEZ — 4 cells` would need a value route that does not exist: a Parquet p-column cannot be
// read in the model, and no block in this workspace builds a header out of row values. The clonotype's name
// stays available as an optional column of the panel's own table.
//
// The name is fetched through the card's own pFrame handle. `fullPframeHandle` is the frame the grid already
// joined the upstream label column into, so the title and the card cannot disagree.
const labelsPframe = computed(() => {
  const out = app.model.outputs.punchcardTable;
  return out?.ok === true ? out.value?.fullPframeHandle : undefined;
});
// The clonotype axis, derived in the model from an emitted column so its domain is exact. The same id the
// expansion's filter uses, which is what keeps the label lookup and the filter talking about one axis.
const clonotypeAxisId = computed(() => app.model.outputs.clonotypeAxisId);
const { resolveTitle } = useClonotypeLabels(labelsPframe, clonotypeAxisId);

// The name when it is known, and the generic word until then -- never the raw scClonotypeKey. The lookup is
// a driver call, so there IS a first frame with no name yet, and "Clonotype" carries that frame rather than
// flashing a key.
const expansionTitle = computed(() => resolveTitle(app.model.data.expandedSet?.[0]) ?? "Clonotype");

// A missing V(D)J dataset is a legitimate state rather than a half-filled form: the block runs, and the
// verdict stage alone is skipped. Read from data rather than from an output, because the point is what the
// user has chosen, including before the next run.
//
// This is a limit of how the stage is currently WIRED, not of the view: the read is taken per cell before
// anything is combined, and the software already accepts a cell list in place of a linker. What stands in
// the way is that main.tpl gates the whole verdict stage on the dataset ref.
const noDataset = computed(() => app.model.data.datasetRef === undefined);

// What the run was actually answered under. A rung that cannot serve refuses the run rather than falling to
// another, so what served always equals what was asked for -- and where no baseline could be established at
// all, this record is what says so.
const runMeta = computed(() => app.model.outputs.verdictRunMeta);
// A run that established no baseline read no verdicts, so no punchcard is drawn and this reason is shown in
// its place. Only the tag-distribution rung reaches here: its conditions are properties of the data. Read
// from the boolean the record carries, never from a string match.
const noBaseline = computed(() => runMeta.value?.baselineEstablished === false);
const noBaselineReason = computed(() => runMeta.value?.noBaselineReason ?? "");
// Whether the run carried panels that differ. The per-identity could-answer count is shown only then,
// because only then does it vary: under one panel it is the clonotype's own cell count at every identity,
// which the grid already carries beside the name.
const panelsDiffer = computed(() => (runMeta.value?.samplePanelCount ?? 1) > 1);

// How many of this clonotype's cells the gate set aside, stated ONCE for the clonotype rather than at every
// identity: a set-aside cell answers nothing anywhere, so a number repeated down the identity column would
// read as a per-identity failure that did not happen.
//
// Read from the run record, which is the only route available: a Parquet p-column's values cannot be read in
// the model.
//
// Undefined unless a gate was declared. An absent entry under a declared gate is a real zero: the map is
// sparse.
const setAsideLine = computed(() => {
  const meta = runMeta.value;
  const key = app.model.data.expandedSet;
  if (meta === undefined || key === undefined) return undefined;
  if (meta.gateThreshold === undefined || meta.gateThreshold === null) return undefined;
  const count = meta.cellsSetAsideBySet?.[String(key[0])] ?? 0;
  return `Cells set aside: ${count}. A set-aside cell answers nothing at any identity.`;
});

// Tags the grouping column said nothing about stand as their own identity under a bare barcode. The software
// reports this to stderr, and a column a reader cannot place needs saying on the page too.
const ungroupedTags = computed(() => runMeta.value?.tagsWithoutGroupingValue ?? []);

const identityOptions = computed(() => app.model.outputs.punchcardIdentityOptions ?? []);

// The pivot is size-gated upstream: a panel above the limit emits no identity columns at all, so a run can
// have produced verdicts and still have nothing here to draw. That is a different thing from a narrowed
// view, and an empty grid looks the same either way.
const nothingToOffer = computed(() => !noDataset.value && identityOptions.value.length === 0);

// The figures behind each gate, where the run record carries them. A record written before these fields
// existed yields an empty string, and the sentence around it reads as it did before.
const identityGate = computed(() => {
  const meta = runMeta.value;
  if (meta?.identityCount === undefined || meta.identitySummaryLimit === undefined) return "";
  return ` — this run declared ${meta.identityCount.toLocaleString("en-US")} against a limit of ${meta.identitySummaryLimit.toLocaleString("en-US")}`;
});

const cellGate = computed(() => {
  const meta = runMeta.value;
  const cells = meta?.cellPunchCells ?? meta?.cellsAnalysed;
  if (cells === undefined) return "";
  const carried = ` — this run carried ${cells.toLocaleString("en-US")} cells`;
  return meta?.cellPunchLimit === undefined
    ? carried
    : `${carried} against a limit of ${meta.cellPunchLimit.toLocaleString("en-US")}`;
});

// Headers carry the identity's full name, never a truncation. The grid auto-sizes every column to its
// contents and exposes no width a block can set, so a long label does make its column wide. Every column is
// resizable, and the hover below names the identity as well.
//
// Nothing here narrows the card to a subset of identities, and nothing should. PlAgDataTableV2 ships a
// columns panel and a filters panel, both live on this table, so such a control re-implements in block
// state what the grid already does, and two narrowing mechanisms can disagree. The options output stays,
// because the card reads two other things off it.
</script>

<template>
  <PlBlockPage>
    <template #title>Clonotype tag binding</template>

    <PlAlert v-if="noDataset" type="warn">
      This run has no rows to punch: the verdict stage only runs once a single-cell V(D)J dataset is
      picked, so the run counted barcodes per cell and stopped there. Pick a dataset in the Main
      page's Settings and run again.
    </PlAlert>

    <PlAlert v-else-if="noBaseline" type="warn">
      {{ noBaselineReason }}
      <br /><br />
      No punchcard is drawn, because there are no verdicts to draw. Every reading is a comparison
      against a baseline, so a run that established none produced no answers — and a grid where
      every position read <b>unreliable</b> would cost what a real run costs while looking like a
      result at a glance. Pick a baseline this run's data can support in the Main page's Settings,
      or a sample with more cells, and run again.
    </PlAlert>

    <PlAlert v-else-if="nothingToOffer" type="info">
      This run produced no per-identity columns to draw. The punchcard costs one column per antigen
      identity, so it is emitted only for panels below the block's identity limit{{ identityGate }}.
      A larger panel still produced its verdicts, and they are still exported to downstream blocks.
    </PlAlert>

    <template v-else>
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
        @row-double-clicked="openExpansion"
      />
    </template>

    <!-- Full width, where the settings drawer beside it stays narrow. The by-cell face is a matrix as wide as
         the panel, and a 720px drawer showed a handful of its columns with the rest behind a scrollbar. -->
    <PlSlideModal v-model="expansionOpen" width="100vw">
      <template #title>{{ expansionTitle }}</template>
      <PlTabs v-model="expansionTab" :options="EXPANSION_TABS" />

      <template v-if="expansionTab === 'identity'">
        <PlAlert v-if="setAsideLine" type="info">{{ setAsideLine }}</PlAlert>
        <PlAgDataTableV2
          v-if="app.model.outputs.expansionTable"
          v-model="expansionTableState"
          :settings="expansionSettings"
        />
      </template>

      <template v-else>
        <PlAlert v-if="app.model.outputs.cellExpansionTable === undefined" type="info">
          This run carries no per-cell card. The dense grid it needs is one row per cell against
          every identity, so the verdict stage skips it above its own limits on panel width and cell
          count{{ cellGate }} — and a run that skipped it says so here rather than showing an empty
          grid.
        </PlAlert>
        <template v-else>
          <PunchLegend variant="cell" />
          <PlAgDataTableV2
            v-model="cellExpansionTableState"
            :settings="cellExpansionSettings"
            :cell-renderer-selector="cellPunchRendererSelector"
            show-export-button
          />
        </template>
      </template>
    </PlSlideModal>
  </PlBlockPage>
</template>
