import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import AntigenQcPage from "./pages/AntigenQcPage.vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import PunchcardPage from "./pages/PunchcardPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // Block-label pattern: mirror the model's suggestedBlockLabel ("<dataset> / <barcode> → <feature>")
  // into data.defaultBlockLabel, which the sidebar subtitle reads. The subtitle render context has no
  // result pool, so the dataset label is resolved in the output and copied here. Guarded so it only
  // writes on change (multi-client safe).
  watchEffect(() => {
    const suggested = app.model.outputs.suggestedBlockLabel ?? "";
    if (app.model.data.defaultBlockLabel !== suggested) {
      app.model.data.defaultBlockLabel = suggested;
    }
  });

  // A stale negative-control selection is cleared on the user gesture that invalidates it — changing the
  // CSV or the feature-name column (see MainPage.vue's clearControlOnInputChange). It is deliberately NOT
  // done here via a watcher on the controlOptions output: that would be the spec-facts-resync hairpin
  // (output → data write; see hairpin.md / model.md), and a gesture-driven data→data write has no
  // multi-client interleave and no hydration-timing risk.

  return {
    // Drive the block spinner while the main run is executing.
    progress: () => app.model.outputs.isRunning,
    routes: {
      "/": () => MainPage,
      "/qc": () => QcSummaryPage,
      "/results": () => ResultsPage,
      "/punchcard": () => PunchcardPage,
      // The run's own quality: the verdict stage's measurements and the panel-versus-reads check. Distinct
      // from "/qc", which is the mitool per-sample read statistics.
      "/antigen-qc": () => AntigenQcPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
