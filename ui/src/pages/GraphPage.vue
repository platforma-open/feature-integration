<script setup lang="ts">
import type { PredefinedGraphOption } from "@milaboratories/graph-maker";
import { GraphMaker } from "@milaboratories/graph-maker";
import { computed } from "vue";
import { useApp } from "../app";

const app = useApp();

// Default violin layout, matching the Graph Maker block config: y = feature fraction, grouped by
// sample, faceted by feature. Sourced from this block's exported matrix (the `pf` / graphPf frame);
// the fraction column carries axes [sampleId, cellId, featureId].
const defaultOptions = computed((): PredefinedGraphOption<"discrete">[] | null => {
  const pcols = app.model.outputs.pfPcols;
  if (!pcols) return null;

  const fractionCol = pcols.find((c) => c.spec.name === "pl7.app/feature/fraction");
  if (!fractionCol) return [];

  const axes = fractionCol.spec.axesSpec;
  return [
    { inputName: "y", selectedSource: fractionCol.spec },
    { inputName: "primaryGrouping", selectedSource: axes[0] }, // pl7.app/sampleId
    { inputName: "facetBy", selectedSource: axes[2] }, // pl7.app/feature/featureId
  ];
});
</script>

<template>
  <GraphMaker
    v-model="app.model.data.graphState"
    chart-type="discrete"
    :p-frame="app.model.outputs.pf"
    :default-options="defaultOptions"
  />
</template>
