<script lang="ts" setup>
import { PlStatusTag } from "@platforma-sdk/ui-vue";
import { reactive } from "vue";
import type { QcCheck } from "../results";

// One QC check row in the sample report's Quality Checks tab: status tag, label, the measured value,
// and a description that stays folded until the reader asks for it. Same shape and styling as
// blocks/mixcr-clonotyping's components/QcSection.vue, so the two blocks' reports read alike.
const props = defineProps<{
  value: QcCheck;
}>();

const data = reactive({
  expanded: false,
});
</script>

<template>
  <div class="qc-section" :class="{ expanded: data.expanded }">
    <div class="qc-section__status" @click.stop="data.expanded = !data.expanded">
      <PlStatusTag v-if="props.value.status" :type="props.value.status" />
      <!-- No status means the metric could not be evaluated for this sample. PlStatusTag renders
           nothing for an absent type, and leaving the slot empty would read as an oversight rather
           than as a deliberate "we have no verdict here". -->
      <span v-else class="qc-section__no-status">NOT EVALUATED</span>
    </div>
    <div class="qc-section__text">
      <div class="qc-section__label" @click.stop="data.expanded = !data.expanded">
        {{ props.value.label }}: {{ props.value.printedValue }}
      </div>
      <div class="qc-section__description">{{ props.value.description }}</div>
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
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--color-txt-03);
  cursor: pointer;
}

.qc-section__text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  padding: 2px 0 0;
  gap: 8px;
}

.qc-section__label {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
  cursor: pointer;
}

.qc-section__description {
  display: var(--display);
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-03);
  line-height: 20px;
  white-space: pre-wrap;
}

.qc-section.expanded {
  --display: block;
  --bg: var(--bg-base-light);
}

.qc-section .pl-status-tag {
  cursor: pointer;
}
</style>
