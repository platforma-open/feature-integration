<script setup lang="ts">
import type { QcMeasurementStatus } from "@platforma-open/milaboratories.feature-integration.model";
import {
  PlAgCellStatusTag,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";
import { qcStatusTag } from "../results";

const app = useApp();

const qcSummarySettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.qcSummaryTable,
});

// The rolled-up per-sample status, as the same tag the sample list on Main draws. Both read the same
// rollup -- this column comes from `result_qc.csv`, the Main grid from `sampleQcReport` -- so the two
// surfaces cannot disagree about one sample.
//
// PlAgDataTableV2 hands `cellRendererSelector` to `defaultColDef`, so this selector sees every column and
// returns undefined for the ones it does not claim. `colDef.context.spec.name` is the p-column name the
// table was built from, which is how the column is recognised by name rather than by header text.
const SAMPLE_STATUS = "pl7.app/qc/sampleStatus";

type RendererParams = {
  value?: unknown;
  colDef?: { context?: { spec?: { name?: string } } };
};

const QC_STATUS_VALUES: readonly QcMeasurementStatus[] = ["OK", "warn", "alert"];

function asQcStatus(value: unknown): QcMeasurementStatus | null {
  return QC_STATUS_VALUES.includes(value as QcMeasurementStatus)
    ? (value as QcMeasurementStatus)
    : null;
}

// A sample whose measurements all carried no line leaves this column empty. `qcStatusTag` returns
// undefined there and the cell renders as an ordinary empty cell, never a fourth tag colour.
function statusCellRenderer(params: RendererParams) {
  if (params.colDef?.context?.spec?.name !== SAMPLE_STATUS) return undefined;
  const tag = qcStatusTag(asQcStatus(params.value));
  return tag === undefined ? undefined : { component: PlAgCellStatusTag, params: { type: tag } };
}
</script>

<template>
  <PlBlockPage>
    <template #title>Per-sample QC</template>
    <PlAgDataTableV2
      v-if="app.model.outputs.qcSummaryTable"
      v-model="app.model.data.qcSummaryTableState"
      :settings="qcSummarySettings"
      :cell-renderer-selector="statusCellRenderer"
      show-export-button
    />
    <PlAlert v-else type="info">
      One row per sample, carrying that sample's rolled-up status and every quality measurement,
      appears here once a V(D)J dataset is chosen and the block has run.
    </PlAlert>
  </PlBlockPage>
</template>
