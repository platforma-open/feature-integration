<script setup lang="ts">
import {
  PlAlert,
  PlBlockPage,
  PlDropdown,
  PlDropdownRef,
  PlNumberField,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>

    <PlDropdownRef
      v-model="app.model.data.fbFastqRef"
      :options="app.model.outputs.fastqOptions"
      label="Feature-barcode FASTQ"
    />

    <PlDropdownRef
      v-model="app.model.data.tagFeatureCsvRef"
      :options="app.model.outputs.csvOptions"
      label="Tag → feature CSV"
    />

    <PlDropdown
      v-model="app.model.data.controlFeature"
      :options="app.model.outputs.controlOptions"
      label="Negative-control feature (optional)"
    />

    <PlNumberField v-model="app.model.data.dominanceThreshold" label="Dominance threshold" />

    <PlAlert type="info">
      Per-cell results appear once the feature-barcode workflow is implemented (pending the assay /
      mitool design spike).
    </PlAlert>
  </PlBlockPage>
</template>
