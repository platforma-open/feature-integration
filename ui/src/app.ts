import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import AntigenQcPage from "./pages/AntigenQcPage.vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import PunchcardPage from "./pages/PunchcardPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // Block-label pattern: mirror the model's suggestedBlockLabel ("<dataset> / <barcode> → <feature>") into
  // data.defaultBlockLabel, which the sidebar subtitle reads. The subtitle render context has no result
  // pool, so the dataset label is resolved in the output and copied here. Guarded to write only on change,
  // which is multi-client safe.
  watchEffect(() => {
    const suggested = app.model.outputs.suggestedBlockLabel ?? "";
    if (app.model.data.defaultBlockLabel !== suggested) {
      app.model.data.defaultBlockLabel = suggested;
    }
  });

  // A stale negative-control selection is cleared on the user gesture that invalidates it, meaning a change
  // to the CSV or the feature-name column (see MainPage.vue's clearControlOnInputChange). Never here,
  // through a watcher on the controlOptions output: that is the spec-facts-resync hairpin, an output-to-data
  // write (see hairpin.md and model.md). A gesture-driven data-to-data write has no multi-client interleave
  // and no hydration-timing risk.

  return {
    // Drive the block spinner while the main run is executing.
    progress: () => app.model.outputs.isRunning,
    routes: {
      "/": () => MainPage,
      "/qc": () => QcSummaryPage,
      "/results": () => ResultsPage,
      "/punchcard": () => PunchcardPage,
      // The run's own quality: the verdict stage's measurements and the panel-versus-reads check. Distinct
      // from "/qc", the mitool per-sample read statistics.
      "/antigen-qc": () => AntigenQcPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
