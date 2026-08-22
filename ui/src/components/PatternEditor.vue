<script setup lang="ts">
import {
  allPresets,
  assemblePattern,
  getPreset,
  parsePattern,
  validatePattern,
} from "@platforma-open/milaboratories.feature-integration.model";
import type { ListOption, SimpleOption } from "@platforma-sdk/ui-vue";
import {
  PlBtnGroup,
  PlCheckbox,
  PlDropdown,
  PlNumberField,
  PlSectionSeparator,
  PlTextField,
} from "@platforma-sdk/ui-vue";
import { computed, reactive, ref, watch } from "vue";
import { useApp } from "../app";

const app = useApp();

// Geometry used to seed the builder when switching to the generic preset with no pattern yet (10x 5' v2).
const DEFAULT_PARTS = {
  cellLen: 16,
  umiLen: 10,
  r1TrailingWildcard: true,
  featureLen: 15,
  featureOffset: 0,
};

const presetOptions = computed((): ListOption<string>[] =>
  // Dropdown text = "{product} — {vendor}"
  allPresets.map((p) => ({ value: p.id, label: p.vendor ? `${p.label} — ${p.vendor}` : p.label })),
);
const selectedPreset = computed(() => getPreset(app.model.data.presetId));
const isUserConfigurable = computed(() => selectedPreset.value?.userConfigurable === true);

function setPresetId(id: string | undefined) {
  app.model.data.presetId = id;
  const p = getPreset(id);
  // User-configurable preset: seed a starting pattern so the builder opens populated. A fixed preset owns its
  // pattern, which args reads from preset.pattern, so there is nothing to write into data.
  if (p?.userConfigurable && !app.model.data.pattern) {
    app.model.data.pattern = assemblePattern(DEFAULT_PARTS);
  }
}

// ── Editor mode (raw string vs field-based builder) + read tabs ─────────────
type EditorMode = "write" | "build";
const editorModeOptions: SimpleOption<EditorMode>[] = [
  { value: "write", text: "Pattern string" },
  { value: "build", text: "Pattern builder" },
];
const editorMode = ref<EditorMode>("write");

const readTab = ref<"r1" | "r2">("r1");

// ── Local builder fields (Read 1: cell + UMI; Read 2: feature + offset) ─────
const r1 = reactive({
  cellLen: DEFAULT_PARTS.cellLen,
  umiLen: DEFAULT_PARTS.umiLen,
  hasTrailingWildcard: DEFAULT_PARTS.r1TrailingWildcard,
});
const r2 = reactive({
  featureLen: DEFAULT_PARTS.featureLen,
  featureOffset: DEFAULT_PARTS.featureOffset,
});

// Live preview of the assembled pattern (build mode), same idea as peptide-extraction's preview box.
const preview = computed(() =>
  assemblePattern({
    cellLen: r1.cellLen,
    umiLen: r1.umiLen,
    r1TrailingWildcard: r1.hasTrailingWildcard,
    featureLen: r2.featureLen,
    featureOffset: r2.featureOffset,
  }),
);

const patternParseError = computed(() => {
  if (!isUserConfigurable.value) return null;
  const p = app.model.data.pattern;
  if (!p) return null;
  // Loose check, the same rule as the model's args. mitool does the real parsing, and only the CELL/UMI/FEATURE
  // tags and the R2 capture are required here. An extra flank, spacer or anchor is allowed through the string
  // field.
  return validatePattern(p);
});

// ── Bidirectional sync (data.pattern <-> builder fields). Hairpin-safe: both directions read and write
//    data.pattern, which is persisted data, and local refs. Never a model output. ──
const lastAssembled = ref<string | undefined>(undefined);

// data.pattern -> fields. Skips this component's own write, and leaves the fields untouched on an
// unparseable write-mode string.
watch(
  () => app.model.data.pattern,
  (pattern) => {
    if (pattern === lastAssembled.value || !pattern) return;
    const parts = parsePattern(pattern);
    if (!parts) return;
    r1.cellLen = parts.cellLen;
    r1.umiLen = parts.umiLen;
    r1.hasTrailingWildcard = parts.r1TrailingWildcard;
    r2.featureLen = parts.featureLen;
    r2.featureOffset = parts.featureOffset;
  },
  { immediate: true },
);

// fields -> data.pattern (build mode only)
function reassembleFromFields() {
  const assembled = preview.value;
  lastAssembled.value = assembled;
  app.model.data.pattern = assembled;
}

watch(
  [r1, r2],
  () => {
    if (isUserConfigurable.value && editorMode.value === "build") reassembleFromFields();
  },
  { deep: true },
);

// Entering Build mode: rebuild from fields so an invalid raw string is replaced by a valid one.
watch(editorMode, (mode) => {
  if (mode === "build" && isUserConfigurable.value && patternParseError.value)
    reassembleFromFields();
});
</script>

<template>
  <PlDropdown
    :model-value="app.model.data.presetId"
    :options="presetOptions"
    label="Preset"
    :required="true"
    :error="!selectedPreset ? 'Select a preset' : undefined"
    @update:model-value="setPresetId"
  >
    <template #tooltip>
      Feature-barcode chemistry preset. Sets the tag pattern — cell barcode + UMI on Read 1, feature
      barcode on Read 2. Pick the configurable preset to edit the read layout by hand.
    </template>
  </PlDropdown>

  <!-- User-configurable preset: unlock the Add/Build editor (same shape as blocks/peptide-extraction). -->
  <template v-if="isUserConfigurable">
    <PlBtnGroup v-model="editorMode" :options="editorModeOptions" class="fullWidthGroup" />

    <!-- Add mode: raw mitool tag-pattern text -->
    <template v-if="editorMode === 'write'">
      <PlTextField
        v-model="app.model.data.pattern"
        label="Tag pattern"
        :error="patternParseError ?? undefined"
      >
        <template #tooltip>
          mitool tag pattern. <code>CELL</code>/<code>UMI</code> on Read 1, <code>FEATURE</code> on
          Read 2.<br /><br />
          Syntax:
          <a href="https://mixcr.com/mixcr/reference/ref-tag-pattern/" target="_blank">
            mixcr.com/mixcr/reference/ref-tag-pattern
          </a>
        </template>
      </PlTextField>
    </template>

    <!-- Build mode: field-based editor with a live preview and Read 1 / Read 2 tabs -->
    <template v-if="editorMode === 'build'">
      <div class="preview">{{ preview }}</div>

      <div class="readTabs">
        <button :class="['readTab', { readTabActive: readTab === 'r1' }]" @click="readTab = 'r1'">
          Read 1
        </button>
        <button :class="['readTab', { readTabActive: readTab === 'r2' }]" @click="readTab = 'r2'">
          Read 2
        </button>
      </div>

      <template v-if="readTab === 'r1'">
        <PlNumberField v-model="r1.cellLen" :min-value="1" :step="1" label="Cell barcode length">
          <template #tooltip>
            Cell barcode length on Read 1. Fixed by your single-cell chemistry (16 nt for 10x);
            change only if your kit uses a different barcode length.
          </template>
        </PlNumberField>
        <PlNumberField v-model="r1.umiLen" :min-value="1" :step="1" label="UMI length">
          <template #tooltip>
            UMI length on Read 1. Fixed by your chemistry (10 nt for 10x 5' v2); change only if your
            kit uses a different UMI length.
          </template>
        </PlNumberField>
        <PlCheckbox v-model="r1.hasTrailingWildcard">
          Trailing wildcard
          <template #tooltip>
            Append <code>*</code> after the UMI so any sequence past the cell barcode + UMI is
            ignored — needed when Read 1 is sequenced longer than cell barcode + UMI (e.g. a 28 nt
            R1). Leave on unless Read 1 is exactly cell barcode + UMI.
          </template>
        </PlCheckbox>
      </template>

      <template v-if="readTab === 'r2'">
        <PlNumberField
          v-model="r2.featureOffset"
          :min-value="0"
          :step="1"
          label="Feature barcode offset"
        >
          <template #tooltip>
            Bases to skip at the start of Read 2 before the feature barcode. 0 for the 10x BEAM Core
            Kit; 10 for TotalSeq-C / next-gen antigen barcoding (the 15 nt barcode sits behind a 10
            nt lead).
          </template>
        </PlNumberField>
        <PlNumberField
          v-model="r2.featureLen"
          :min-value="1"
          :step="1"
          label="Feature barcode length"
        >
          <template #tooltip>
            Feature (antigen) barcode length on Read 2. Set by your feature-barcode panel (15 nt for
            10x BEAM / TotalSeq).
          </template>
        </PlNumberField>
      </template>
    </template>

    <!-- Divider marking the end of the custom read-layout block, shown only for the configurable preset, so
         it reads as its own section apart from the Tag-feature CSV below. -->
    <PlSectionSeparator compact />
  </template>
</template>

<style scoped>
.fullWidthGroup {
  width: 100%;
}

.readTabs {
  display: flex;
  align-items: center;
  gap: 0;
  border-bottom: 1px solid var(--pl-border, #ddd);
  margin-top: 8px;
}

.readTab {
  all: unset;
  cursor: pointer;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--pl-text-secondary, #888);
  border-bottom: 2px solid transparent;
  transition:
    color 0.15s,
    border-color 0.15s;
}

.readTab:hover {
  color: var(--pl-text-primary, #222);
}

.readTabActive {
  color: var(--pl-text-primary, #222);
  border-bottom-color: var(--pl-accent, #2563eb);
}

.preview {
  font-family: monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--pl-surface-secondary, #f5f5f5);
  border-radius: 4px;
  padding: 8px 10px;
  margin-top: 12px;
  color: var(--pl-text-primary, #222);
}
</style>
