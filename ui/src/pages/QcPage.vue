<script setup lang="ts">
import {
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { useApp } from "../app";

const app = useApp();

const qcSummarySettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.qcSummaryTable,
});

const tagstatSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.tagstatQcTable,
});
</script>

<template>
  <PlBlockPage>
    <template #title>QC</template>

    <h3>Per-sample QC summary</h3>
    <!-- PlAgDataTableV2 is height:100%; stacked tables collapse to just the footer without a bounded
         parent. The per-sample summary is small, so give it a compact fixed height. -->
    <div class="fi-qc-table fi-qc-table--compact">
      <PlAgDataTableV2
        v-if="app.model.outputs.qcSummaryTable"
        v-model="app.model.data.qcSummaryTableState"
        :settings="qcSummarySettings"
        show-export-button
      />
      <PlAlert v-else type="info">
        Per-sample QC metrics (reads parsed/matched, cells, features, UMIs) appear here once the
        block has run.
      </PlAlert>
    </div>

    <h3>Raw tag-stat counts</h3>
    <div class="fi-qc-table fi-qc-table--grow">
      <PlAgDataTableV2
        v-if="app.model.outputs.tagstatQcTable"
        v-model="app.model.data.tagstatTableState"
        :settings="tagstatSettings"
        show-export-button
      />
      <PlAlert v-else type="info">
        The raw per-(cell, feature-barcode) UMI counts appear here once the block has run.
      </PlAlert>
    </div>
  </PlBlockPage>
</template>

<style scoped>
.fi-qc-table {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.fi-qc-table--compact {
  height: 240px;
}
/* Grow to fill the remaining page height, but never collapse below a usable height. */
.fi-qc-table--grow {
  flex: 1;
  min-height: 360px;
}
</style>
