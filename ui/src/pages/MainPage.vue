<script setup lang="ts">
import {
  PlAccordionSection,
  PlAgDataTableV2,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdown,
  PlDropdownRef,
  PlFileInput,
  PlLogView,
  PlNumberField,
  PlSlideModal,
  usePlDataTableSettingsV2,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

const app = useApp();
// Auto-open Settings for a fresh block (no FASTQ chosen yet); stay closed once configured.
const settingsOpen = ref(app.model.data.fbFastqRef === undefined);

const tableSettings = usePlDataTableSettingsV2({
  model: () => app.model.outputs.perCellTable,
});

// Per-sample × per-step mitool/Python log streams (key = [sampleId, step]; value = log handle),
// surfaced in a Logs slide-over so the run isn't a black box.
const logEntries = computed(() => app.model.outputs.stepLogs?.data ?? []);
const logsOpen = ref(false);

// No-negative-control info banner: shown when results exist and no control is set, until the user
// dismisses it (dismissal persisted in data so it stays hidden).
const controlInfoVisible = computed(
  () =>
    app.model.outputs.perCellTable !== undefined &&
    !app.model.data.controlFeature &&
    !app.model.data.controlInfoDismissed,
);
function dismissControlInfo() {
  app.model.data.controlInfoDismissed = true;
}

// Cell-barcode whitelist options for refine-tags CELL correction. "" = de-novo (default), which keeps
// non-10x / synthetic data working; selecting the chemistry's list makes cellIds match the VDJ block
// exactly. Static enumeration (not output-derived → no hairpin). See docs/cell-whitelist-correction-plan.md.
//
// Source of the names + chemistry mapping: 10x Genomics' canonical cell-barcode whitelists, vendored
// into mitool as built-ins. Registry: tools/mitool/.../pattern/SequenceSetCollection.kt (the
// `sequenceSetNames` map; the `.bin` sets ship inside the mitool jar). The chemistry→whitelist mapping
// is 10x's own table copied verbatim into that file's header comment, citing
// https://kb.10xgenomics.com/hc/en-us/articles/115004506263-What-is-a-barcode-whitelist-
// NB: 3M-5pgex-jan-2023 postdates that 10x table — its "5' GEM-X" chemistry is read from the
// barcode-set name, not the vendored table.
const cellWhitelistOptions = [
  { value: "", label: "None — de-novo (non-10x / synthetic)" },
  { value: "737K-august-2016", label: "10x 3' v2 / 5' v1–v2 (737K-august-2016)" },
  { value: "3M-5pgex-jan-2023", label: "10x 5' GEM-X (3M-5pgex-jan-2023)" },
  { value: "3M-february-2018", label: "10x 3' v3 / v3.1 (3M-february-2018)" },
  { value: "737K-arc-v1", label: "10x Multiome ATAC+GEX (737K-arc-v1)" },
];
</script>

<template>
  <PlBlockPage>
    <template #title>Feature Integration</template>
    <template #append>
      <PlBtnGhost v-if="logEntries.length > 0" @click.stop="logsOpen = true">Logs</PlBtnGhost>
      <PlBtnGhost @click.stop="settingsOpen = true">Settings</PlBtnGhost>
    </template>

    <PlAlert v-if="controlInfoVisible" type="info" closable @close="dismissControlInfo">
      No negative control selected — specificity scores are not computed. Pick a "Negative-control
      feature" in Settings to add them. Consensus feature shows the assigned antigen,
      <b>ambiguous</b> when no feature passes the dominance threshold, and is empty when the cell
      has no feature signal.
    </PlAlert>

    <PlAgDataTableV2
      v-if="app.model.outputs.perCellTable"
      v-model="app.model.data.tableState"
      :settings="tableSettings"
      show-export-button
    />
    <PlAlert v-else type="info">
      Per-cell feature results appear after you set the inputs in Settings and run the block.
    </PlAlert>

    <PlSlideModal v-model="settingsOpen">
      <template #title>Settings</template>
      <PlDropdownRef
        v-model="app.model.data.fbFastqRef"
        :options="app.model.outputs.fastqOptions"
        label="Select dataset"
      />
      <PlFileInput
        v-model="app.model.data.tagFeatureCsvHandle"
        label="Tag → feature CSV"
        placeholder="tags.csv"
        :extensions="['csv']"
        required
      />
      <PlDropdown
        v-if="app.model.data.tagFeatureCsvHandle"
        v-model="app.model.data.barcodeSeqColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Barcode-sequence column"
      />
      <PlDropdown
        v-if="app.model.data.tagFeatureCsvHandle"
        v-model="app.model.data.featureNameColumn"
        :options="app.model.outputs.csvColumnOptions"
        label="Feature-name column"
      />
      <PlDropdown
        v-model="app.model.data.controlFeature"
        :options="app.model.outputs.controlOptions"
        label="Negative-control feature (optional)"
      />
      <!-- Less-common params: dominance threshold + read geometry (DP-1: 10x 5' v2 defaults). -->
      <PlAccordionSection label="Advanced Settings">
        <PlNumberField
          v-model="app.model.data.dominanceThreshold"
          :min-value="0.5"
          :max-value="1"
          :step="0.05"
          label="Dominance threshold"
          helper="Fraction of a cell's signal one feature must reach to be the consensus. Floor 0.5 (spec A-0012)."
        />
        <PlNumberField
          v-model="app.model.data.cellLen"
          :min-value="1"
          :step="1"
          label="Cell barcode length (R1)"
        />
        <PlNumberField
          v-model="app.model.data.umiLen"
          :min-value="1"
          :step="1"
          label="UMI length (R1)"
        />
        <PlNumberField
          v-model="app.model.data.featureLen"
          :min-value="1"
          :step="1"
          label="Feature barcode length (R2)"
        />
        <PlDropdown
          v-model="app.model.data.cellWhitelist"
          :options="cellWhitelistOptions"
          label="Cell barcode whitelist (10x)"
        >
          <template #tooltip>
            Snap cell barcodes to a 10x whitelist so cellIds match the VDJ block exactly. Leave as
            de-novo for non-10x or synthetic data.
          </template>
        </PlDropdown>
      </PlAccordionSection>
    </PlSlideModal>

    <PlSlideModal v-model="logsOpen">
      <template #title>Logs</template>
      <PlLogView
        v-for="entry in logEntries"
        :key="entry.key.join('/')"
        :label="entry.key.join(' / ')"
        :log-handle="entry.value"
      />
    </PlSlideModal>
  </PlBlockPage>
</template>
