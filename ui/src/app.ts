import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // Block-label pattern: mirror the model's suggestedBlockLabel ("<dataset> · <barcode> → <feature>")
  // into data.defaultBlockLabel, which the sidebar subtitle reads. The subtitle render context has no
  // result pool, so the dataset label is resolved in the output and copied here. Guarded so it only
  // writes on change (multi-client safe).
  watchEffect(() => {
    const suggested = app.model.outputs.suggestedBlockLabel ?? "";
    if (app.model.data.defaultBlockLabel !== suggested) {
      app.model.data.defaultBlockLabel = suggested;
    }
  });

  // Clear a stale negative-control selection. controlFeature persists in block data, but the valid
  // feature list (controlOptions) is re-derived from the uploaded tag→feature CSV. If the user swaps the
  // CSV or the feature-name column so the selected control is no longer a real feature, args() would
  // still send it and the workflow would silently score specificity against a zero control (inflated
  // scores, no error). Drop it once the repopulated options confirm it's gone. Guarded so it only writes
  // on change (multi-client safe); an empty/loading options list never triggers a spurious clear.
  watchEffect(() => {
    const control = app.model.data.controlFeature;
    if (!control) return;
    const options = app.model.outputs.controlOptions ?? [];
    if (options.length === 0) return;
    if (!options.some((o) => o.value === control)) {
      app.model.data.controlFeature = undefined;
    }
  });

  return {
    // Drive the block spinner while the main run is executing.
    progress: () => app.model.outputs.isRunning,
    routes: {
      "/": () => MainPage,
      "/qc": () => QcSummaryPage,
      "/results": () => ResultsPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
