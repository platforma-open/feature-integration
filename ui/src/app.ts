import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import MainPage from "./pages/MainPage.vue";
import QcPage from "./pages/QcPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  return {
    // Drive the block spinner while the main run is executing.
    progress: () => app.model.outputs.isRunning,
    routes: {
      "/": () => MainPage,
      "/qc": () => QcPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
