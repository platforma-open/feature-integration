<script setup lang="ts">
import type {
  GroupingRule,
  ReferenceSource,
} from "@platforma-open/milaboratories.feature-integration.model";
import { groupingColumns } from "@platforma-open/milaboratories.feature-integration.model";
import {
  PlAccordionSection,
  PlCheckbox,
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

// The settings for the binding reading. Rendered in the Main page's Settings drawer and again in the Explore
// readout page's own: one component, one set of controls, both writing the same data. A scientist who meets a
// card of grey punches can change the rule that produced it without leaving the page.
//
// Everything this component EDITS is below the "binding reading" line in BlockArgs, so a change here recovers
// every per-sample mitool body from cache and re-runs the verdict stage alone. That is what makes it safe to
// offer from a results page. It also READS three fields it must never edit -- tagFeatureCsvHandle,
// barcodeSeqColumn, sampleColumn -- to tell whether the panel has loaded, and to keep a role or grouping
// setting from naming a column the panel reader consumes as a key. Those three force the whole per-sample
// fan-out to re-run and belong to the Main page alone. A control for one of them here would make the explore
// readout's drawer silently expensive.
//
// VOCABULARY, and the split is deliberate. Everything a USER reads says "baseline": the level a count must
// exceed, measured in the same cell from a tag declared to bind nothing. The DATA layer keeps `reference` --
// `ReferenceSource`, the run-meta keys, the p-column domain values -- and those cannot follow, because domain
// is part of column identity and renaming one would change what every emitted column IS. Code comments here
// describe the data layer, so they still say reference and comparator.
//
// One user-facing word, never four. "control" is not it: that word belongs to the separate `controlFeature`
// field on the Main page, a marker for downstream readers that changes no number and no verdict, and it must
// never appear here.
const app = useApp();

// The panel-derived dropdowns have nothing to offer until the panel file is uploaded and staging has read its
// columns. Disabled and dimmed, so their empty state reads as "waiting" rather than "nothing found".
const panelUnread = computed(
  () => !app.model.data.tagFeatureCsvHandle || app.model.outputs.csvColumnsLoading === true,
);

// The panel's PROPERTY columns: every header except the ones the panel reader consumes as keys, which are the
// barcode column and the sample column where one is set. Mirrors panel.py's own rule. A column the reader
// strips is not one the role or grouping setting may name, and emit_verdicts.py ends the run rather than
// degrading when handed one.
const panelPropertyOptions = computed(() =>
  (app.model.outputs.csvColumnOptions ?? []).filter(
    (o) => o.value !== app.model.data.barcodeSeqColumn && o.value !== app.model.data.sampleColumn,
  ),
);

// The distinct values of the chosen role column -- what the comparator is designated by.
const roleValueOptions = computed(() => {
  const column = app.model.data.roleColumn;
  if (!column) return [];
  return (app.model.outputs.csvValuesByColumn?.[column] ?? []).map((v) => ({
    value: v,
    label: v,
  }));
});

// The panel's headers as they stand now. Snapshotted into data on the gesture that names a panel column, so
// args() can refuse a column the panel does not carry without reaching outside data, the same reason the
// sample column snapshots its values. Left to a watcher this would be an output written back into data, which
// two open clients would race to write.
function snapshotPanelColumns() {
  app.model.data.panelColumnSnapshot = (app.model.outputs.csvColumnOptions ?? []).map(
    (o) => o.value,
  );
}

// Changing the role column drops the values chosen under the old one. They designate values of THIS column,
// and left behind they would mark no tag while still reading as a configured comparator.
//
// Declaring a baseline is also what the baseline CHOICE was made against, so changing the declaration drops
// the choice too. An override means "I want this rung given what I have declared", not a standing instruction
// that outlives the declaration it answered. Left behind, marking a baseline tag could not move the field
// onto it, which reads as the block ignoring what you just declared.
//
// The same rule the two settings either side of it follow: a setting does not outlive the thing it was chosen
// against. Only on a GESTURE, never from a watcher -- a watcher on a model output writing back into data is
// the hairpin, and two clients with the project open would race on it.
function clearBaselineChoice() {
  app.model.data.referenceSource = undefined;
}

function setRoleColumn(column: string | undefined) {
  app.model.data.roleColumn = column || undefined;
  app.model.data.referenceValues = undefined;
  clearBaselineChoice();
  snapshotPanelColumns();
}

// ONE value, stored as a one-element list. `040-glossary` splits the two cardinalities: being a control is
// a property of the tag and a panel may carry several, but the reference is one job given to one of them.
// So the value that marks the baseline is singular. Several values here only ever described a panel whose
// role column spells one role more than one way, and a panel that does that is asking to be corrected
// rather than accommodated.
//
// `data.referenceValues` stays a LIST. The field name, the `--reference-values` flag and every stored
// project keep their shape, so this tightens the control without a migration. The `> 1 tag` refusal in
// `verdict.py` stays too, and is now the only thing that can fire: one value can still mark several tags,
// which is a panel fact this control cannot see.
function setReferenceValue(value: string | undefined) {
  app.model.data.referenceValues = value ? [value] : undefined;
  clearBaselineChoice();
}

// One control for the whole rule, taking SEVERAL columns: an identity is the distinct combination of the
// named columns' values, so naming antigen and concentration together makes the same antigen at two
// concentrations two identities.
//
// The barcode column sits in the same list as the property columns, because naming it IS a grouping -- the
// finest one available, one identity per barcode -- rather than a mode beside grouping. It cannot be offered
// as a property column, since the panel reader consumes it as the `tag` key, so it maps to the `tag` rule,
// which produces exactly that reading. A sentinel value stands for it, prefixed with a space so no real
// column name can collide.
const TAG_GROUPING_VALUE = " tag";

const groupingSelection = computed<string[]>(() => {
  const rule = app.model.data.grouping;
  if (rule === undefined) return [];
  if (rule.by === "tag") return [TAG_GROUPING_VALUE];
  return groupingColumns(rule);
});

const groupingOptions = computed(() => [
  {
    value: TAG_GROUPING_VALUE,
    label: `${app.model.data.barcodeSeqColumn || "Barcode"} — one identity per barcode`,
  },
  ...panelPropertyOptions.value,
]);

function setGrouping(selected: string[] | undefined) {
  const picked = (selected ?? []).filter((c) => c !== "");
  // The barcode column is the finest grouping there is, so it does not combine with a coarser one: a
  // combination including it is already one identity per barcode. Picking it therefore wins alone, and
  // picking nothing leaves the rule absent, which reads the same way.
  const rule: GroupingRule | undefined = picked.includes(TAG_GROUPING_VALUE)
    ? { by: "tag" }
    : picked.length > 0
      ? { by: "property", columns: picked }
      : undefined;
  app.model.data.grouping = rule;
  // The identities ARE the values of the grouping columns, so groups declared under the previous rule name
  // things that no longer exist. Cleared on the gesture that invalidates them rather than left to fail.
  app.model.data.contendingGroups = undefined;
  snapshotPanelColumns();
}

// The comparator sources this panel can serve. Both the option list and the reasons come from a model output
// rather than from a watcher: the facts behind them are the panel's, and copying them into data would make
// the output depend on the data it feeds.
const referenceSources = computed(() => app.model.outputs.referenceSources);

// The rungs this panel can serve, and the rung the run will be answered under. Both come from model outputs:
// the option list from `referenceSources`, the shown value from `effectiveReferenceSource`.
//
// NEVER derive the shown value here. Writing the rule twice -- once to decide what to display, once in
// `args()` to decide what to send -- makes the field lie the moment a stored choice stops being serviceable.
// Clearing the role values then leaves a dead "declared" behind: this component re-renders as "the panel's
// own readings" while the data still holds "declared", so the form shows a scientist the exact value they are
// being asked to supply while Run stays greyed out, and only re-picking the already-shown value fixes it.
//
// No derivation exists anywhere. The block does not choose a baseline, because a baseline nobody chose is a
// methodology nobody knows they used. `effectiveReferenceSource` is the stored choice, or the bottom rung
// where none was made. Reading an output to display it is not a hairpin: nothing here writes back.
const serviceableSources = computed(() => referenceSources.value?.options ?? []);
const shownSource = computed(() => app.model.outputs.effectiveReferenceSource);
// Whether the scientist has actually chosen. Read from data rather than from the output above, which answers
// "none" both for an explicit choice of no baseline and for no choice at all. The two look the same to a run
// and are opposite things to say to a reader.
const baselineUnchosen = computed(() => app.model.data.referenceSource === undefined);

// A checkbox binds a boolean, and the field is optional in data so a project that never touched it carries no
// key. `undefined` reads as false here, and the setter writes `undefined` back rather than `false`, which
// keeps such a project's args vector unchanged.
const minimumAppliesToBaseline = computed({
  get: () => app.model.data.minimumAppliesToBaseline === true,
  set: (on: boolean) => {
    app.model.data.minimumAppliesToBaseline = on ? true : undefined;
  },
});

function setBaselineSource(value: string | undefined) {
  app.model.data.referenceSource = value === undefined ? undefined : (value as ReferenceSource);
}

// The identities the contending-groups editor picks from, live from the uploaded panel.
const identityOptions = computed(() => app.model.outputs.identityOptions ?? []);
// Grouped on the barcode column, an identity id IS a feature barcode. The panel metadata staging emits is
// column-wise, carrying each column's distinct values with no pairing between a barcode and the name beside
// it, so the identity names cannot be offered here. Said in the editor rather than left for the user to
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
      The clonotypes each verdict is about. Leave it blank to run the block without verdicts. The
      block still emits the tag counts, the per-cell values and the per-sample QC. The fifteen
      quality measurements and the panel-versus-reads check belong to the verdict stage. They need a
      dataset.
    </template>
  </PlDropdownRef>
  <PlAlert v-if="!app.model.data.datasetRef" type="info">
    Without a V(D)J dataset the block skips the verdict stage. It emits no verdicts, no per-identity
    columns and no panel check. It still emits everything that is not keyed by a clonotype.
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
      Leave it blank if your panel declares no role.
    </template>
  </PlDropdown>
  <!-- Required exactly while a role column is named, and not otherwise. The column alone marks no tag: it is
       validated, recorded, and changes no number, so the pair is the setting and half of it is an unfinished
       form. Blank column plus blank values stays legitimate -- that is the panel which declares no baseline,
       which `292-no-declared-reference` serves. -->
  <PlDropdown
    :model-value="app.model.data.referenceValues?.[0]"
    :options="roleValueOptions"
    label="Value that marks the baseline tag"
    :disabled="panelUnread || !app.model.data.roleColumn"
    :required="!!app.model.data.roleColumn"
    clearable
    @update:model-value="setReferenceValue($event)"
  >
    <template #tooltip>
      Select which value of the role column marks the baseline tag. A tag is the baseline in every
      sample, or in none. You cannot give some samples a different baseline.<br /><br />
      Required once you name a role column. That column says where each tag's role is written; this
      value is what actually marks one. Named alone, the column changes nothing.<br /><br />
      The block reads counts against <b>one</b> baseline tag. A panel may carry several control
      tags, but only one is nominated to supply the baseline. If the value you pick marks more than
      one tag, the run stops and names the tags it found.
    </template>
  </PlDropdown>

  <PlDropdown
    :model-value="shownSource"
    :options="serviceableSources"
    label="What sets the baseline"
    :disabled="serviceableSources.length === 0"
    @update:model-value="setBaselineSource"
  >
    <template #tooltip>
      You choose this. The block does not choose it for you and does not change it when you change
      what you declared above: two runs answered against different baselines produce numbers that do
      not compare, and a baseline nobody chose is a method nobody knows they used.<br /><br />
      <b>Declared baseline tag</b> — the tag your panel marks as the one nothing should bind.<br />
      <b>The panel's own readings</b> — the median of each cell's own counts.<br />
      <b>Each tag's own distribution</b> — that tag's counts across the sample's cells, split in
      two.<br />
      <b>No baseline</b> — nothing is compared. Every verdict that needs a baseline reads
      unreliable.<br /><br />
      The last three are local to this run: what a count was read against was a population of this
      run, so those magnitudes do not travel between runs.<br /><br />
      If the rule you chose cannot serve — you raise the minimum panel size past your panel, or
      clear the values that marked your baseline tag — the run does not quietly use a different one.
      It reports no baseline, leaves every verdict that needs one unreliable, and records both what
      you asked for and what served.
    </template>
  </PlDropdown>
  <!-- Not an error and not a block: leaving this unchosen is answered under the bottom rung, which is a
       legitimate position. It is a loud one, though, because the reader gets no verdicts out of it and the
       field itself shows nothing to explain why. -->
  <PlAlert v-if="baselineUnchosen && serviceableSources.length > 0" type="warn">
    No baseline is chosen, so this run judges no count against anything and every verdict that needs
    a baseline will read unreliable. Choose one above. "No baseline" is on that list if it is what
    you mean.
  </PlAlert>
  <!-- Above the info alert, and warn rather than info: this is the one case the user has shown us is a
       mistake rather than a configuration. It sits in this section instead of beside the control field on
       the Main page because the fix -- the two dropdowns above -- is here. -->
  <PlAlert v-if="referenceSources?.controlNotBaseline" type="warn">
    {{ referenceSources.controlNotBaseline }}
  </PlAlert>
  <PlAlert v-if="referenceSources?.unavailable.length" type="info">
    <div v-for="(line, i) in referenceSources.unavailable" :key="i">{{ line }}</div>
  </PlAlert>

  <!-- Directly under the baseline section rather than with the other accordion at the foot of the form.
       Grouping the accordions together sorted this form by how advanced a control is, which split the
       baseline's own thresholds away from the baseline. Collapsed, this costs the reader one line and puts
       every baseline control in one place. The heading does NOT repeat "(background)": that parenthetical
       glosses the word once, where the reader first meets it, and repeating it would read as part of the name
       and re-open the several-names-for-one-thing problem this form already closed. The shared word
       "Baseline" is the link. -->
  <PlAccordionSection label="Baseline thresholds">
    <PlNumberField
      v-model="app.model.data.panelReferenceMinMembers"
      :min-value="1"
      :step="1"
      label="Minimum panel size to serve as baseline"
    >
      <template #tooltip>
        How many tags the panel needs before its own readings can serve as the baseline. Below this,
        the block does not offer that source.<br /><br />
        The default of 25 comes from one preprint, whose own panels held 50 and 100 tags. Nothing
        validates it lower. Lowering it is a departure from the method rather than a preference:
        comparing a count against a handful of other antigens is not a background estimate.<br /><br />
        An antibody kit caps at 15 tags, so this source is out of reach of one. Such a panel can use
        each tag's own distribution instead.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.distributionMinCells"
      :min-value="1"
      :step="10"
      label="Cells needed to fit a tag's own distribution"
    >
      <template #tooltip>
        How many cells a sample needs before a tag's own distribution across those cells can serve
        as the baseline. Below this, the block cannot fit the two components and every reading in
        that sample is unreliable.<br /><br />
        The default of 300 comes from the study this method comes from. Lowering it is a departure
        from that method rather than a preference: below it the baseline is not conservative, it is
        wrong.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.distributionSeparation"
      :min-value="0.01"
      :max-value="1"
      :step="0.05"
      label="Separation the two components must show"
    >
      <template #tooltip>
        How deep the dip between the two fitted components must be, as a share of the smaller of the
        two peaks around it. A tag whose counts do not separate this far gets no baseline, and only
        the antigens that tag carries read unreliable.<br /><br />
        No published work sets this line. The default of 0.5 is this block's choice, not a standard.
        At 1 any dip counts, which would let a tag nothing bound stand in as its own background.
      </template>
    </PlNumberField>
    <PlNumberField
      v-model="app.model.data.highReferenceLine"
      :min-value="1"
      :step="1"
      label="High baseline reading"
    >
      <template #tooltip>
        The baseline count, in UMIs, at which a cell is in high background. This is a measurement,
        not a filter. The block counts these cells whether or not the gate below is on. You can
        therefore see the run's exposure even when no gate is set.
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
        The gate is off when this field is empty. When you set it, the block sets aside a cell whose
        baseline reading reaches this value. That cell reads unreliable at every identity and gives
        no verdict anywhere.<br /><br />
        Off is a deliberate default, and a contested one. Published practice uses a gate. The
        dominant tool does not. Off matches the tool, so first-run numbers stay recognisable. The
        cost is that a sticky cell remains in the set and returns a confident "not bound".
      </template>
    </PlNumberField>
  </PlAccordionSection>

  <PlSectionSeparator compact> The binding reading </PlSectionSeparator>
  <PlDropdownMulti
    :model-value="groupingSelection"
    :options="groupingOptions"
    label="Panel columns that define an identity"
    :disabled="panelUnread"
    @update:model-value="setGrouping"
  >
    <template #tooltip>
      A verdict is about an identity, not a barcode. Name one or more panel columns. Every tag that
      shares a value in all of them becomes one identity. That is how an antigen on two barcodes
      gives one column rather than two.<br /><br />
      Name several columns and the identity becomes the combination. Antigen and concentration
      together read the same antigen at two concentrations as two identities.<br /><br />
      The barcode column is the finest grouping: one identity per barcode. Select it and the block
      ignores the other columns, because any combination that includes the barcode gives the same
      identities.<br /><br />
      An identity's reading in a cell is the highest of its tags, never their sum. Tags differ in
      uptake, so a sum would need the baseline scaled to match.
    </template>
  </PlDropdownMulti>

  <PlNumberField v-model="app.model.data.countFloor" :min-value="0" :step="1" label="Minimum count">
    <template #tooltip>
      Counts below this are not evidence of binding. The block reads them as zero rather than as a
      small signal.<br /><br />
      The minimum does not apply to the baseline tag. A minimum on the baseline would lower the
      level every count is judged against, and push the whole run toward bound.<br /><br />
      The default is 4. It is a declared default, not a calibrated line.
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
      The specificity score at or above which one cell reads that identity as bound. This is a
      per-cell reading. The clonotype's verdict is the majority of its cells.<br /><br />
      This default comes from the dominant tool's cutoff. This block does not justify it
      independently.
    </template>
  </PlNumberField>
  <PlNumberField
    v-model="app.model.data.minVotingCells"
    :min-value="1"
    :step="1"
    label="Minimum voting cells"
  >
    <template #tooltip>
      How many cells must answer before their majority settles a verdict. Below this number the
      verdict reads unreliable, and gives too few voters as the reason.<br /><br />
      At 1 a verdict may rest on a single cell. The table carries the answering-cell count, so you
      can see when it does.
    </template>
  </PlNumberField>

  <!--
    DEFERRED — the contending-group editor is not offered.

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
    many words. Grouped on the barcode column the identities ARE the barcodes, and the panel is read
    column by column before the run, so no barcode-to-name pairing exists yet and the dropdown could
    only offer 15-mers. A scientist would have been picking sequences out of a list.

    What it should become: contention derived from a panel column, on the same footing as the grouping
    dropdown above. That needs a column the panel file declares, which the files observed in the field
    do not yet carry — the same gap that blocks reading a variant family as one identity. Both wait on
    the same conversation about what the panel file should hold.

    Nothing in the corpus is contradicted by deferring it: `contention-travels-with-the-negative`
    requires the note to travel WITH the verdict where a group exists, and it still does. It does not
    require this block to offer a way to type one in.

  <PlAccordionSection label="Contending groups">
    <PlAlert type="info">
      Identities declared to compete for one binding site. Where one of them reads bound for a
      clonotype, the block marks the others that read "not bound" as competed. The verdict does not
      change, and a downstream statement can test the mark.
    </PlAlert>
    <PlAlert v-if="identitiesAreBarcodes" type="warn">
      You grouped on the barcode column, so each identity is one feature barcode. The block reads
      the panel column by column before the run, and that reading carries no barcode-to-name
      pairing. The block therefore cannot offer the identity names here. Group on a panel column,
      the feature-name column for instance, to pick identities by name.
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

  <PlAccordionSection label="Advanced reading settings">
    <PlCheckbox v-model="minimumAppliesToBaseline">
      Apply the minimum count to the baseline tag
      <template #tooltip>
        By default the minimum count is not applied to the tag your panel marks as the baseline. The
        minimum removes what is not evidence of binding, and the baseline is not evidence of binding
        — it is what binding is measured against.<br /><br />
        Turning this on changes no verdict. Each baseline source reads its own counts before the
        minimum, so the level a count is judged against is the same either way. What changes is the
        run's own accounting: how many readings it reports as removed, how many cells it reports as
        emptied, and which of a clonotype's cells count as empty.
      </template>
    </PlCheckbox>
    <PlNumberField
      v-model="app.model.data.minAgreement"
      :min-value="0"
      :max-value="1"
      :step="0.05"
      clearable
      label="Minimum cell agreement (0–1)"
    >
      <template #tooltip>
        This setting is off when the field is empty. A narrow majority then stands, and reports how
        narrow. Set it to leave a verdict unsettled where the answering cells agree less than this
        share of the time.
      </template>
    </PlNumberField>
  </PlAccordionSection>
</template>
