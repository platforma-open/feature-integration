<script setup lang="ts">
import type { GraphMakerState, PredefinedGraphOption } from "@milaboratories/graph-maker";
import { GraphMaker } from "@milaboratories/graph-maker";
import type { PColumnSpec } from "@platforma-sdk/model";
import { PlAlert, PlChartStackedBar } from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";
import type { SampleResult } from "../results";

// The Visual Report tab: where this sample's reads went, and the shape of this sample's own antigen
// counts. RecoveryBar is already the settings object a stacked bar wants ({ title, data: [{ label,
// value, color, description }] }), so the read-recovery half presents data results.ts builds for the
// grid anyway.
//
// PlChartStackedBar, never PlAgChartStackedBarCell. The Ag* one is an ag-grid cell renderer taking
// ICellRendererParams, so using it outside a grid would mean fabricating a fake cell params object.
// PlChartStackedBar is the same chart's standalone form, and the grid's cell renderer is a thin wrapper
// around its compact variant.
const props = defineProps<{
  sampleData: SampleResult | undefined;
}>();

const app = useApp();

// The chart's own legend gives colour and label only. The segment meanings are spelled out below the chart
// instead, so the legend would be a redundant second colour key.
const settings = computed(() => {
  const recovery = props.sampleData?.recovery;
  return recovery === undefined ? undefined : { ...recovery, showLegends: false };
});

// Read counts and shares per segment, for the written breakdown under the chart. The descriptions results.ts
// attaches to each segment are otherwise reachable only by hovering the bar, and the equivalent prose in the
// grid only by hovering the column header. A reader who does neither should still learn what the segments
// mean.
const segments = computed(() => {
  const recovery = props.sampleData?.recovery;
  if (recovery === undefined) return undefined;
  const total = recovery.data.reduce((sum, segment) => sum + segment.value, 0);
  return recovery.data.map((segment) => ({
    label: segment.label,
    color: segment.color.toString(),
    // The description results.ts builds carries the label and the read count on their own lines. Those are
    // shown as fields here, so keep only the explanatory middle line.
    meaning: segment.description.split("\n")[1] ?? "",
    printedValue:
      total > 0
        ? `${segment.value.toLocaleString()} reads (${((segment.value / total) * 100).toFixed(1)}%)`
        : `${segment.value.toLocaleString()} reads`,
  }));
});

// --- the antigen-count distribution: this sample alone -------------------------------------------
//
// 330-the-quality-readout: the shape of antigen counts per barcode is a plot about one sample and
// belongs with that sample. emit_verdicts.py writes its deciles into result_qc_sample_deciles.csv,
// keyed (sampleId, decile), imported as its own column (`qcSampleDecileValue`) so it carries no
// identity in common with the run-level score and reference-reading deciles.
const SAMPLE_DECILE_VALUE = "pl7.app/antigen/qcSampleDecileValue";
const DECILE_AXIS = "pl7.app/antigen/qcDecile";
const SAMPLE_AXIS = "pl7.app/sampleId";

function col(name: string, valueType: "Double" | "Int"): PColumnSpec {
  return { kind: "PColumn", name, valueType, axesSpec: [] };
}

// A discrete chart has no `x`: its categorical input is `primaryGrouping`, filtered to this sample
// through the sample axis GraphMaker reaches because `qcSampleDecileValue` carries it.
const antigenCountOptions = computed<PredefinedGraphOption<"discrete">[]>(() => [
  { inputName: "y", selectedSource: col(SAMPLE_DECILE_VALUE, "Double") },
  { inputName: "primaryGrouping", selectedSource: { name: DECILE_AXIS, type: "Int" } },
  {
    inputName: "filters",
    selectedSource: { name: SAMPLE_AXIS, type: "String" },
    filterType: "equals",
    selectedFilterValues: props.sampleData ? [props.sampleData.sampleId] : [],
  },
]);

const antigenCountColumns = (spec: PColumnSpec) => spec.name === SAMPLE_DECILE_VALUE;

// One chart config, reused across samples: the component is re-keyed on sampleId below, so each
// sample's view mounts fresh and reads `antigenCountOptions` for its own filter.
const antigenGraphState = ref<GraphMakerState>({
  title: "Antigen count per cell barcode",
  template: "line",
});

// Absent means the verdict stage has not reported this run's distributions at all yet. Distinct from
// a reported run whose deciles carry no row for this sample, which is the "no counts" case below.
const antigenDistributionsAbsent = computed(
  () => app.model.outputs.runQualityDistributions === undefined,
);

// The sample's own antigenCountDistribution measurement, read from the same report the Quality
// Checks tab shows. `value === null` there means no barcode in this sample held a counted reading,
// and the measurement's `reason` states that -- the wording this plot shows in its place.
const antigenMeasurement = computed(() =>
  props.sampleData?.qcReport?.measurements.find((m) => m.id === "antigenCountDistribution"),
);
const antigenNoCountsReason = computed(() =>
  antigenMeasurement.value?.value === null
    ? (antigenMeasurement.value.reason ?? undefined)
    : undefined,
);
</script>

<template>
  <div class="visual-report">
    <div v-if="settings && segments" class="visual-report__block">
      <PlChartStackedBar :settings="settings" />
      <div class="visual-report__segments">
        <div v-for="segment in segments" :key="segment.label" class="visual-report__segment">
          <div class="visual-report__swatch" :style="{ backgroundColor: segment.color }" />
          <div class="visual-report__text">
            <div class="visual-report__label">{{ segment.label }}: {{ segment.printedValue }}</div>
            <div class="visual-report__meaning">{{ segment.meaning }}</div>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="visual-report__pending">
      The read-recovery breakdown appears when this sample finishes — it is computed from the read
      counts in the completed parse and refine-tags reports. The Log tab already works: the per-step
      logs are what the running pipeline is producing right now.
    </div>

    <div class="visual-report__block">
      <div class="visual-report__title">Antigen count per cell barcode</div>
      <PlAlert v-if="antigenDistributionsAbsent" type="info">
        No antigen-count distribution has arrived from this run yet. It is taken by the same verdict
        stage as the read-recovery breakdown, so it arrives once the run reports.
      </PlAlert>
      <PlAlert v-else-if="antigenNoCountsReason" type="info">
        {{ antigenNoCountsReason }}
      </PlAlert>
      <div v-else-if="sampleData" :key="sampleData.sampleId" class="visual-report__plot">
        <GraphMaker
          v-model="antigenGraphState"
          chart-type="discrete"
          :p-frame="app.model.outputs.runQualityDistributions"
          :default-options="antigenCountOptions"
          :data-column-predicate="antigenCountColumns"
        />
      </div>
    </div>
  </div>
</template>

<style lang="css" scoped>
.visual-report {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.visual-report__block {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.visual-report__title {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
}

.visual-report__plot {
  height: 360px;
}

.visual-report__segments {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.visual-report__segment {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  gap: 12px;
}

.visual-report__swatch {
  width: 12px;
  min-width: 12px;
  height: 12px;
  margin-top: 4px;
  border-radius: 2px;
}

.visual-report__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.visual-report__label {
  font-weight: 500;
  font-size: 14px;
  color: var(--color-txt-01);
}

.visual-report__meaning {
  font-weight: 500;
  font-size: 14px;
  line-height: 20px;
  color: var(--color-txt-03);
}

.visual-report__pending {
  padding: 24px 8px;
  color: var(--color-txt-03);
  font-size: 14px;
  line-height: 20px;
}
</style>
