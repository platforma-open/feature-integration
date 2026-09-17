<script lang="ts" setup>
import {
  qcMeasurementDescription,
  type SampleQcMeasurement,
} from "@platforma-open/milaboratories.feature-integration.model";
import { PlStatusTag } from "@platforma-sdk/ui-vue";
import { computed, reactive } from "vue";
import { qcStatusTag } from "../results";

// One measurement row in the sample report's Quality Checks tab: its status, its label and value, a second
// line carrying the reason or a qualifier, and what the measurement counts, folded until the reader asks.
// Same shape and styling as blocks/mixcr-clonotyping's components/QcSection.vue, so the two blocks' reports
// read alike.
const props = defineProps<{
  value: SampleQcMeasurement;
}>();

const data = reactive({
  expanded: false,
});

const tag = computed(() => qcStatusTag(props.value.status));

// The words for this measurement, looked up by id. The software sends ids, numbers and statuses; every
// sentence on this row is authored in the model, so rewording one is a UI change and nothing else.
const qcContent = computed(() => qcMeasurementDescription(props.value.id));

// What went into the number, folded with the description and set in the same style. Two measurements take
// more than one form and the value alone cannot say which: the sticky count is a count of cells above a
// declared gate OR the median of the readings where none is declared. A reader who cannot see this reads
// one form as the other.
//
// Rendered as written, one line per part: each is a short sentence or a `Label: value` pair, so it reads
// on from the description above it rather than needing a second vocabulary here to keep in step.
const detailParts = computed(() =>
  (props.value.detail ?? "")
    .split("|")
    .map((part) => part.trim())
    .filter((part) => part.length > 0),
);

// One rule for every measurement: the set carries no unit, so nothing here can format per measurement without
// keeping a second copy of the software's set. Magnitude sets the precision -- no fractional part at a hundred
// and above, three places below it. A non-zero value under a thousandth goes out in exponential form; rounded
// to three places it would print as "0".
const printedValue = computed(() => {
  const v = props.value.value;
  if (v === null) return undefined;
  if (Math.abs(v) >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (v !== 0 && Math.abs(v) < 0.001) return v.toExponential(2);
  return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
});

// The one line that stays visible under the value: why there is no number. A reader must not have to open
// a row to find out that nothing computed it.
//
// Every measurement in this list is the SAMPLE's, so none of them needs a line saying its finding belongs
// somewhere else. A reagent's figures are on the Per-tag QC page, and they were never in this list.
const reasonLine = computed(() => {
  const m = props.value;
  return m.value === null && m.reason ? m.reason : undefined;
});

// What a bad value MEANS, folded with the description rather than standing beside the number. Collapsed,
// a row is its label and its value; opening it is what asks for the interpretation. Set in the
// description's own style, since both answer "what am I looking at" rather than "what happened here".
const whenBadLine = computed(() => {
  const m = props.value;
  return m.value !== null && qcContent.value.whenBad && m.status !== null && m.status !== "OK"
    ? qcContent.value.whenBad
    : undefined;
});
</script>

<template>
  <div class="qc-section" :class="{ expanded: data.expanded }">
    <div class="qc-section__status" @click.stop="data.expanded = !data.expanded">
      <PlStatusTag v-if="tag" :type="tag" />
      <!-- No line stands behind this measurement, so it carries no status. The em-dash marks the absence;
                 the value or the reason beside it says which case it is. -->
      <span v-else class="qc-section__no-status">—</span>
    </div>
    <div class="qc-section__text">
      <div class="qc-section__label" @click.stop="data.expanded = !data.expanded">
        {{ qcContent.label }}<template v-if="printedValue">: {{ printedValue }}</template>
      </div>
      <div v-if="reasonLine" class="qc-section__note">{{ reasonLine }}</div>
      <div class="qc-section__description">{{ qcContent.description }}</div>
      <div v-if="whenBadLine" class="qc-section__description">{{ whenBadLine }}</div>
      <div v-for="(part, i) in detailParts" :key="i" class="qc-section__description">
        {{ part }}
      </div>
    </div>
  </div>
</template>

<style lang="css" scoped>
.qc-section {
  --display: none;
  --bg: transparent;

  display: flex;
  flex-direction: row;
  align-items: flex-start;
  padding: 8px 24px 8px 8px;
  gap: 12px;

  border-width: 1px 0;
  border-style: solid;
  border-color: var(--color-div-grey);

  margin-top: -1px;

  background-color: var(--bg);
}

.qc-section__status {
  width: 96px;
  min-width: 96px;
}

.qc-section__no-status {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-txt-03);
  cursor: pointer;
}

.qc-section__text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  padding: 2px 0 0;
  gap: 4px;
}

.qc-section__label {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
  cursor: pointer;
}

.qc-section__note {
  font-weight: 400;
  font-size: 13px;
  color: var(--color-txt-03);
  line-height: 18px;
}

.qc-section__description {
  display: var(--display);
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-03);
  line-height: 20px;
  white-space: pre-wrap;
  margin-top: 4px;
}

.qc-section.expanded {
  --display: block;
  --bg: var(--bg-base-light);
}

.qc-section .pl-status-tag {
  cursor: pointer;
}
</style>
