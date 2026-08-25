<script lang="ts" setup>
import type { SampleQcMeasurement } from "@platforma-open/milaboratories.feature-integration.model";
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

// The second line, under the value: the reason where the measurement has no number, what a bad value implies
// where it carries one and is not OK, and, for a measurement that does not roll up, that its status is not
// the sample's.
const notes = computed(() => {
  const m = props.value;
  const lines: string[] = [];
  if (m.value === null && m.reason) lines.push(m.reason);
  if (m.value !== null && m.implies && m.status !== null && m.status !== "OK")
    lines.push(m.implies);
  if (m.value !== null && !m.rollsUp)
    lines.push(
      "This measurement is about a reagent rather than about this sample, so its status stays off " +
        "the sample's own. It is reported on the run quality page.",
    );
  return lines;
});
</script>

<template>
  <div class="qc-section" :class="{ expanded: data.expanded }">
    <div class="qc-section__status" @click.stop="data.expanded = !data.expanded">
      <PlStatusTag v-if="tag" :type="tag" />
      <!-- No line stands behind this measurement, so it carries no status, and there is no fourth word for
           that. The em-dash marks the absence; the value or the reason beside it says which case it is. -->
      <span v-else class="qc-section__no-status">—</span>
    </div>
    <div class="qc-section__text">
      <div class="qc-section__label" @click.stop="data.expanded = !data.expanded">
        {{ props.value.label }}<template v-if="printedValue">: {{ printedValue }}</template>
      </div>
      <div v-for="(note, i) in notes" :key="i" class="qc-section__note">{{ note }}</div>
      <div class="qc-section__description">{{ props.value.counts }}</div>
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
