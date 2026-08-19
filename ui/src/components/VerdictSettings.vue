<script setup lang="ts">
import type { GroupingRule } from "@platforma-open/milaboratories.feature-integration.model";
import {
  PlAccordionSection,
  PlAlert,
  PlBtnGhost,
  PlDropdown,
  PlDropdownMulti,
  PlDropdownRef,
  PlNumberField,
  PlSectionSeparator,
} from "@platforma-sdk/ui-vue";
import { computed } from "vue";
import { useApp } from "../app";

// The settings for the binding reading. Rendered in the Main page's Settings drawer and again in the
// Punchcard page's own — one component, one set of controls, both writing the same data. A scientist who
// meets a card of grey punches can change the rule that produced it without leaving the page.
//
// Everything this component EDITS is below the "binding reading" line in BlockArgs, so a change here
// recovers every per-sample mitool body from cache and re-runs the verdict stage alone. That is what
// makes it safe to offer from a results page. It also READS three fields it must never edit —
// tagFeatureCsvHandle, barcodeSeqColumn, sampleColumn — to tell whether the panel has loaded and to keep
// a role or grouping setting from naming a column the panel reader consumes as a key. Those three force
// the whole per-sample fan-out to re-run, and they belong to the Main page alone; adding a control for
// one of them here would make the punchcard's drawer silently expensive.
const app = useApp();

// The panel-derived dropdowns have nothing to offer until the panel file is uploaded and staging has read
// its columns; disabled + dimmed so their empty state reads as "waiting" rather than "nothing found".
const panelUnread = computed(
  () => !app.model.data.tagFeatureCsvHandle || app.model.outputs.csvColumnsLoading === true,
);

// The panel's PROPERTY columns: every header except the ones the panel reader consumes as keys — the
// barcode column, and the sample column where one is set. This mirrors panel.py's own rule. A column the
// reader strips is not one the role or grouping setting may name, and emit_verdicts.py ends the run
// rather than degrading when it is handed one.
const panelPropertyOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) => o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.sampleColumn,
  ),
);

// The distinct values of the chosen role column — what the comparator is designated by.
const roleValueOptions = computed(() => {
  const column = app.model.data.roleColumn;
  if (!column) return [];
  return (app.model.outputs.csvValuesByColumn?.[column] ?? []).map((v) => ({
    value: v,
    label: v,
  }));
});

// The panel's headers as they stand right now. Snapshotted into data on the gesture that names a panel
// column, so args() can refuse a column the panel does not carry without reaching outside data — the same
// reason the sample column snapshots its values. Left to a watcher this would be an output written back
// into data, which two open clients would race to write.
function snapshotPanelColumns() {
  app.model.data.panelColumnSnapshot = (app.model.outputs.csvColumnOptions ?? []).map(
    (o) => o.value,
  );
}

// Changing the role column drops the values chosen under the old one: they designate values of THIS
// column, and left behind they would mark no tag at all while still reading as a configured comparator.
function setRoleColumn(column: string | undefined) {
  app.model.data.roleColumn = column || undefined;
  app.model.data.referenceValues = undefined;
  snapshotPanelColumns();
}

// One control for the whole rule: an empty pick is the reading's own default (one identity per tag), so
// it projects as an absent rule rather than as a hand-built { by: "tag" }.
const groupingColumn = computed(() =>
  app.model.data.grouping?.by === "property" ? app.model.data.grouping.column : "",
);
const groupingOptions = computed(() => [
  { value: "", label: "One identity per tag" },
  ...panelPropertyOptions.value,
]);

function setGrouping(column: string | undefined) {
  const rule: GroupingRule | undefined = column ? { by: "property", column } : undefined;
  app.model.data.grouping = rule;
  // The identities ARE the values of the grouping column, so groups declared under the previous rule name
  // things that no longer exist. Cleared on the gesture that invalidates them rather than left to fail.
  app.model.data.contendingGroups = undefined;
  snapshotPanelColumns();
}

// The comparator sources this panel can serve. Both the option list and the reasons come from a model
// output rather than from a watcher: the facts behind them are the panel's, and copying them into data
// would make the output depend on the data it feeds.
const referenceSources = computed(() => app.model.outputs.referenceSources);

// Only while the source is unset: an unset source is not "no comparator", it is the panel's default, and
// a scientist who leaves it blank should be able to read which one that is without running.
const defaultSourceHelper = computed(() =>
  app.model.data.referenceSource === undefined && referenceSources.value !== undefined
    ? `Left blank, this panel would use ${referenceSources.value.fallback}.`
    : undefined,
);

// The identities the contending-groups editor picks from, live from the uploaded panel.
const identityOptions = computed(() => app.model.outputs.identityOptions ?? []);
// Under the default rule an identity id IS a feature barcode. The panel metadata staging emits is
// column-wise — each column's distinct values, with no pairing between a barcode and the name beside it —
// so the antigen names cannot be offered here. Said in the editor rather than left for the user to
// discover from a list of 15-mers.
const identitiesAreBarcodes = computed(() => app.model.data.grouping?.by !== "property");

const contendingGroups = computed(() => app.model.data.contendingGroups ?? []);

function addContendingGroup() {
  app.model.data.contendingGroups = [...contendingGroups.value, []];
}

function setContendingGroup(index: number, members: string[]) {
  app.model.data.contendingGroups = contendingGroups.value.map((group, i) =>
    i === index ? members : group,
  );
}

function removeContendingGroup(index: number) {
  const remaining = contendingGroups.value.filter((_, i) => i !== index);
  app.model.data.contendingGroups = remaining.length > 0 ? remaining : undefined;
}
</script>

<template>
  <PlSectionSeparator compact> Binding verdicts </PlSectionSeparator>
  <PlDropdownRef
    v-model="app.model.data.datasetRef"
    :options="app.model.outputs.datasetOptions"
    label="Single-cell V(D)J dataset (optional)"
    clearable
  >
    <template #tooltip>
      The clonotype sets each verdict is about. Leave it blank to run the block without verdicts —
      the antigen counts, the per-cell values and the QC are produced either way.
    </template>
  </PlDropdownRef>
  <PlAlert v-if="!app.model.data.datasetRef" type="info">
    Without a V(D)J dataset the block skips the verdict stage: no antigen verdicts, no per-antigen
    columns and no panel check are produced. Everything not keyed by a clonotype still is.
  </PlAlert>

  <PlDropdown
    :model-value="app.model.data.roleColumn"
    :options="panelPropertyOptions"
    label="Reference role column"
    :disabled="panelUnread"
    clearable
    @update:model-value="setRoleColumn"
  >
    <template #tooltip>
      The panel column declaring what each tag is for — the one whose value marks a tag as the
      comparator every other count is read against. Leave blank if the panel declares no such role.
    </template>
  </PlDropdown>
  <PlDropdownMulti
    :model-value="app.model.data.referenceValues ?? []"
    :options="roleValueOptions"
    label="Values marking the reference"
    :disabled="panelUnread || !app.model.data.roleColumn"
    @update:model-value="app.model.data.referenceValues = $event.length > 0 ? $event : undefined"
  >
    <template #tooltip>
      Which values of the role column designate a comparator tag. A tag is a comparator in every
      sample or in none.
    </template>
  </PlDropdownMulti>

  <PlDropdown
    v-model="app.model.data.referenceSource"
    :options="referenceSources?.options ?? []"
    label="What counts are read against"
    :helper="defaultSourceHelper"
    clearable
  >
    <template #tooltip>
      Selected, never inferred: two runs answered against different comparators produce numbers that
      do not compare, and a scientist who did not choose the rule cannot know that happened.
    </template>
  </PlDropdown>
  <PlAlert v-if="referenceSources?.unavailable.length" type="info">
    <div v-for="(line, i) in referenceSources.unavailable" :key="i">{{ line }}</div>
  </PlAlert>

  <PlDropdown
    :model-value="groupingColumn"
    :options="groupingOptions"
    label="Group tags into antigens by"
    :disabled="panelUnread"
    @update:model-value="setGrouping"
  >
    <template #tooltip>
      A verdict is about an antigen identity. By default every tag is its own identity; naming a
      panel column instead reads all the tags sharing a value of it as one antigen — which is how a
      dual-barcoded antigen becomes one row rather than two.
    </template>
  </PlDropdown>

  <PlNumberField v-model="app.model.data.countFloor" :min-value="0" :step="1" label="Count floor">
    <template #tooltip>
      Counts below this are not evidence of binding: they are read as zero rather than as a small
      signal. Shipped at 4, a declared default rather than a calibrated line.
    </template>
  </PlNumberField>
  <PlNumberField
    v-model="app.model.data.boundCutoff"
    :min-value="0"
    :max-value="100"
    :step="1"
    label="Bound cutoff (0–100)"
  >
    <template #tooltip>
      The score at or above which one cell reads as bound. Inherited from the dominant tool's cutoff
      rather than justified independently.
    </template>
  </PlNumberField>
  <PlNumberField
    v-model="app.model.data.minVotingCells"
    :min-value="1"
    :step="1"
    label="Minimum voting cells"
  >
    <template #tooltip>
      How many cells must answer before their majority settles a verdict. At 1 a verdict may rest on
      a single cell and says so — the answering-cell count travels in the table.
    </template>
  </PlNumberField>

  <!--
    DEFERRED — the contending-antigen editor is not offered.

    Only the editor is deferred. `contendingGroups` stays in the block data, the args projection still
    passes it, and the workflow still threads `--contending`, so a project that already carries groups
    keeps its competitor notes and nothing about the emitted verdicts changes. Restoring the editor is
    uncommenting this block.

    Why it is not offered. `contending-grouping-chosen-at-annotation` holds that which members are
    taken together as one contending group is chosen "over whatever properties the scientist's panel
    file carries" — a declared property such as a binding-site column, chosen at annotation and never
    frozen. What this editor offered instead was hand-entered lists, which is a different thing: it
    asks the scientist to retype a grouping the panel file is supposed to carry.

    It was also close to unusable at the default grouping, which the removed warning admitted in as
    many words. Under one-identity-per-tag the identities ARE the barcodes, and the panel is read
    column by column before the run, so no barcode-to-name pairing exists yet and the dropdown could
    only offer 15-mers. A scientist would have been picking sequences out of a list.

    What it should become: contention derived from a panel column, on the same footing as the grouping
    dropdown above. That needs a column the panel file declares, which the files observed in the field
    do not yet carry — the same gap that blocks reading a variant family as one identity. Both wait on
    the same conversation about what the panel file should hold.

    Nothing in the corpus is contradicted by deferring it: `contention-travels-with-the-negative`
    requires the note to travel WITH the verdict where a group exists, and it still does. It does not
    require this block to offer a way to type one in.

  <PlAccordionSection label="Contending antigens">
    <PlAlert type="info">
      Antigens declared to compete for one binding site. Where one of them reads bound for a
      clonotype, the others reading "not bound" are marked as competed — the verdict is unchanged,
      and a downstream statement can test the mark.
    </PlAlert>
    <PlAlert v-if="identitiesAreBarcodes" type="warn">
      Under "one identity per tag" the identities are the feature barcodes themselves. The panel is
      read column by column before the run, which carries no barcode-to-name pairing, so the antigen
      names cannot be offered here. Group by a panel column — the feature-name column, for instance
      — to pick antigens by name.
    </PlAlert>
    <div v-for="(group, index) in contendingGroups" :key="index">
      <PlDropdownMulti
        :model-value="group"
        :options="identityOptions"
        :label="`Group ${index + 1}`"
        :disabled="panelUnread"
        @update:model-value="setContendingGroup(index, $event)"
      />
      <PlBtnGhost @click.stop="removeContendingGroup(index)">Remove group</PlBtnGhost>
    </div>
    <PlBtnGhost :disabled="panelUnread" @click.stop="addContendingGroup()">
      Add a contending group
    </PlBtnGhost>
  </PlAccordionSection>
  -->

  <PlAccordionSection label="Advanced verdict settings">
    <PlNumberField
      v-model="app.model.data.gateThreshold"
      :min-value="1"
      :step="1"
      clearable
      label="Admissibility gate (comparator UMIs)"
    >
      <template #tooltip>
        Off when empty. When set, a cell whose comparator reading reaches this is set aside instead
        of being read — its counts are background, not binding.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.highReferenceLine"
      :min-value="1"
      :step="1"
      label="High reference reading"
    >
      <template #tooltip>
        With the gate off, where a comparator reading counts as high. Cells above it are reported,
        never silently dropped.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.panelReferenceMinMembers"
      :min-value="1"
      :step="1"
      label="Panel minimum for self-comparison"
    >
      <template #tooltip>
        How many tags a panel needs before its own readings can stand in as the comparator. Below
        it, that source is not offered.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.referenceThinLine"
      :min-value="0"
      :step="1"
      label="Thin-comparator line"
    >
      <template #tooltip>
        Below this the comparator rests on too little to compare against, and the reading is left
        unreliable rather than called not bound.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.minAgreement"
      :min-value="0"
      :max-value="1"
      :step="0.05"
      clearable
      label="Minimum cell agreement (0–1)"
    >
      <template #tooltip>
        Off when empty: a narrow majority stands and reports how narrow. Set it to leave a verdict
        unsettled where the answering cells agree less than this share of the time.
      </template>
    </PlNumberField>
  </PlAccordionSection>
</template>
