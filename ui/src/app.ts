import { platforma } from "@platforma-open/milaboratories.feature-integration.model";
import { plRefsEqual } from "@platforma-sdk/model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { computed, watch } from "vue";
import AntigenQcPage from "./pages/AntigenQcPage.vue";
import MainPage from "./pages/MainPage.vue";
import QcSummaryPage from "./pages/QcSummaryPage.vue";
import PunchcardPage from "./pages/PunchcardPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // The sidebar subtitle is the picked dataset's label and nothing else. The panel's column names are not a
  // choice the scientist made -- the block detects them where it can -- and they name the spelling of a CSV
  // header rather than anything about what was profiled.
  //
  // The subtitle never renders with dots. Periods come from a dotted dataset or file label. A subtitle the user
  // types in the sidebar does not pass through here.
  //
  // "" when no dataset is picked; undefined while the picked dataset is not among the options yet.
  const subtitleLabel = computed<string | undefined>(() => {
    const fastqRef = app.model.data.fbFastqRef;
    if (!fastqRef) return "";
    const label = app.model.outputs.fastqOptions?.find((o) => plRefsEqual(o.ref, fastqRef))?.label;
    if (label === undefined) return undefined;
    return label.replace(/\./g, " ").replace(/ {2,}/g, " ").trim();
  });
  watch(
    subtitleLabel,
    (label) => {
      if (label !== undefined && label !== app.model.data.subtitleLabel) {
        app.model.data.subtitleLabel = label;
      }
    },
    { immediate: true },
  );

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
