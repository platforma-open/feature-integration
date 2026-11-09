import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import AntigenQcPage from "./pages/AntigenQcPage.vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import PunchcardPage from "./pages/PunchcardPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
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
