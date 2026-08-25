<script setup lang="ts">
// The measurement subject as a reader names it. The `qcEntity` axis carries one opaque key per level --
// a sampleId on the sample rows, a barcode on the tag rows, the literal "run" on the run row -- and only
// the sample keys have a label elsewhere in the block.
//
// A key with no label renders as itself. Tag rows and the run row take that path.
defineProps<{ value: string; labels: Record<string, string> }>();
</script>

<template>
  <span>{{ labels[value] ?? value }}</span>
</template>
