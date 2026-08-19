<script setup lang="ts">
import type {
  GroupingRule,
  ReferenceSource,
} from "@platforma-open/milaboratories.feature-integration.model";
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
//
// VOCABULARY, and the split is deliberate. Everything a USER reads says "baseline": the level a count
// must exceed, measured in the same cell from a tag declared to bind nothing. The DATA layer keeps
// `reference` — `ReferenceSource`, the run-meta keys, and the p-column domain values — and those cannot
// follow, because domain is part of column identity and renaming one would change what every emitted
// column IS. Code comments here describe the data layer, so they still say reference and comparator.
//
// The UI used four words for this one thing (reference, comparator, "read against", self-comparison),
// and none of them was the word a scientist reaches for, which is "control". That collided with the
// separate `controlFeature` field on the Main page — a marker for downstream readers that changes no
// number and no verdict. So "control" now belongs to that field alone and never appears here.
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
  // The punchcard's narrowing is the same kind of state and was missed here. Going from per-tag to
  // per-property turns every identity from a barcode into a property value, so the old picks match no
  // column at all — and this control is one drawer away from the card it would blank. The model falls back
  // to the whole panel in that case, so clearing is what keeps the PICKER from listing antigens that are
  // gone; the card is safe either way.
  app.model.data.punchcardIdentities = [];
  snapshotPanelColumns();
}

// The comparator sources this panel can serve. Both the option list and the reasons come from a model
// output rather than from a watcher: the facts behind them are the panel's, and copying them into data
// would make the output depend on the data it feeds.
const referenceSources = computed(() => app.model.outputs.referenceSources);

// The source the field SHOWS. There is no "automatic" row: it would have named the same rule as one of
// the rows beside it — the panel's own readings today, the declared tag as soon as one is marked — and two
// rows meaning one thing reads as a duplicate rather than a choice.
//
// Instead the rule is DERIVED, the same way `args()` derives what it sends: a declared baseline tag where
// values mark one, otherwise the panel's own readings. So declaring a tag moves this field visibly, which
// beats an invisible rule that followed the panel silently. The derivation lives in `args()` too, and the
// two must agree — that is the one thing to keep in step if either changes.
//
// An explicit pick wins and sticks, because `served_source` never swaps a requested rung for a different
// one. A pick that has stopped being serviceable (declared, after its values were cleared) falls back to
// the derived rule here so the dropdown still shows something real; `args()` refuses that combination
// loudly, which is where the user learns about it.
const serviceableSources = computed(() => referenceSources.value?.options ?? []);

const derivedSource = computed<string>(() => {
  const offered = serviceableSources.value.map((o) => o.value as string);
  const wanted = (app.model.data.referenceValues?.length ?? 0) > 0 ? "declared" : "panel";
  return offered.includes(wanted) ? wanted : (offered[0] ?? "none");
});

const shownSource = computed<string>(() => {
  const chosen = app.model.data.referenceSource;
  const offered = serviceableSources.value.map((o) => o.value as string);
  return chosen !== undefined && offered.includes(chosen) ? chosen : derivedSource.value;
});

function setBaselineSource(value: string | undefined) {
  app.model.data.referenceSource = value === undefined ? undefined : (value as ReferenceSource);
}

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
      The clonotype sets each verdict is about. Leave it blank to run the block without verdicts.
      The antigen counts, the per-cell values and the per-sample QC are produced either way. The
      fifteen quality measurements and the panel-versus-reads check belong to the verdict stage.
      They need a dataset.
    </template>
  </PlDropdownRef>
  <PlAlert v-if="!app.model.data.datasetRef" type="info">
    Without a V(D)J dataset the block skips the verdict stage: no antigen verdicts, no per-antigen
    columns and no panel check are produced. Everything not keyed by a clonotype still is.
  </PlAlert>

  <PlSectionSeparator compact> Baseline (background) level </PlSectionSeparator>
  <PlDropdown
    :model-value="app.model.data.roleColumn"
    :options="panelPropertyOptions"
    label="Panel column naming the baseline tag"
    :disabled="panelUnread"
    clearable
    @update:model-value="setRoleColumn"
  >
    <template #tooltip>
      Name the panel column that declares each tag's role. One value of that column marks a tag as
      the baseline. The block then judges every other count in the same cell against that tag.<br /><br />
      <b>This setting changes the numbers.</b> "Control feature marker" on the Main page only labels
      a feature in the output.<br /><br />
      Leave blank if your panel declares no role.
    </template>
  </PlDropdown>
  <PlDropdownMulti
    :model-value="app.model.data.referenceValues ?? []"
    :options="roleValueOptions"
    label="Values that mark the baseline tag"
    :disabled="panelUnread || !app.model.data.roleColumn"
    @update:model-value="app.model.data.referenceValues = $event.length > 0 ? $event : undefined"
  >
    <template #tooltip>
      Select which values of the role column mark the baseline tag. A tag is the baseline in every
      sample, or in none. You cannot give some samples a different baseline.
    </template>
  </PlDropdownMulti>

  <PlDropdown
    :model-value="shownSource"
    :options="serviceableSources"
    label="What sets the baseline"
    :disabled="serviceableSources.length === 0"
    @update:model-value="setBaselineSource"
  >
    <template #tooltip>
      Set from what you declared above, and you can override it. Marking a baseline tag selects that
      tag. With none marked, the panel's own readings serve.<br /><br />
      <b>Declared baseline tag</b> — the tag your panel marks as bound by nothing.<br />
      <b>The panel's own readings</b> — the median of each cell's own counts. Verdicts read this way
      are local to this run and do not compare with another run.<br />
      Where neither can serve, the run reports no baseline and every verdict is left unreliable.
      That is an outcome, not a setting, so it is not on this list.<br /><br />
      Selected, never inferred: two runs answered against different baselines produce numbers that
      do not compare, and a scientist who did not choose the rule cannot know that happened.
    </template>
  </PlDropdown>
  <!-- Above the info alert, and warn rather than info: this is the one case the user has shown us is
       a mistake rather than a configuration. It sits in this section instead of beside the control
       field on the Main page because the fix — the two dropdowns above — is here. -->
  <PlAlert v-if="referenceSources?.controlNotBaseline" type="warn">
    {{ referenceSources.controlNotBaseline }}
  </PlAlert>
  <PlAlert v-if="referenceSources?.unavailable.length" type="info">
    <div v-for="(line, i) in referenceSources.unavailable" :key="i">{{ line }}</div>
  </PlAlert>

  <PlSectionSeparator compact> The binding reading </PlSectionSeparator>
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

  <PlAccordionSection label="Baseline thresholds">
    <PlNumberField
      v-model="app.model.data.panelReferenceMinMembers"
      :min-value="1"
      :step="1"
      label="Minimum panel size to serve as baseline"
    >
      <template #tooltip>
        How many tags the panel needs before its own readings can serve as the baseline. Below this,
        that source is not offered.<br /><br />
        No published work sets this line. The default of 8 is this block's choice, not a standard.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.referenceThinLine"
      :min-value="0"
      :step="1"
      label="Minimum usable baseline reading"
    >
      <template #tooltip>
        The lowest baseline count this block will judge against, in UMIs. Below it, the cell reads
        unreliable and gives the reason. It is not called not bound.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.highReferenceLine"
      :min-value="1"
      :step="1"
      label="High baseline reading"
    >
      <template #tooltip>
        The baseline count, in UMIs, at which a cell counts as sitting in high background. This is a
        measurement, not a filter. The block counts these cells whether or not the gate below is on,
        so you can see the run's exposure even when no gate is set.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.gateThreshold"
      :min-value="1"
      :step="1"
      clearable
      label="Admissibility gate (baseline UMIs)"
    >
      <template #tooltip>
        Off when empty. When set, a cell whose baseline reading reaches this is set aside. That cell
        reads unreliable at every antigen and gives no verdict anywhere.<br /><br />
        Off is a deliberate default, and a contested one. Published practice gates. The dominant
        tool does not. Off matches the tool, so first-run numbers stay recognisable. The cost is
        that a sticky cell stays in and returns a confident "not bound".
      </template>
    </PlNumberField>
  </PlAccordionSection>

  <PlAccordionSection label="Advanced reading settings">
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
