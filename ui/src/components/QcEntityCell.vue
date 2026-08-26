<script setup lang="ts">
import { computed } from "vue";

// The measurement subject as a reader names it. The `qcEntity` axis carries one opaque key per level -- a
// sampleId on the sample rows, a barcode on the tag rows, the literal "run" on the run row -- and only the
// sample keys have a label elsewhere in the block. A key with no label renders as itself.
//
// ag-grid hands a cell renderer its whole parameter object as a single `params` prop, so the fields it
// carries are read from there rather than declared as props of their own.
const props = defineProps<{
  params: { value: unknown; labels: Record<string, string> };
}>();

const shown = computed(() => {
  const value = props.params.value;
  if (value === undefined || value === null) return "";
  const key = String(value);
  return props.params.labels[key] ?? key;
});
</script>

<template>
  <span>{{ shown }}</span>
</template>
