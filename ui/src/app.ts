import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import GraphPage from "./pages/GraphPage.vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import TagstatPage from "./pages/TagstatPage.vue";

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

  return {
    // Drive the block spinner while the main run is executing.
    progress: () => app.model.outputs.isRunning,
    routes: {
      "/": () => MainPage,
      "/graph": () => GraphPage,
      "/qc": () => QcSummaryPage,
      "/tagstat": () => TagstatPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
