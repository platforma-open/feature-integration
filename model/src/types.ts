import type { PlDataTableStateV2, PlRef } from "@platforma-sdk/model";

/** Workflow inputs (projected from BlockData by the args lambda; validated there). */
export type BlockArgs = {
  fbFastqRef: PlRef; // feature-barcode FASTQ column
  tagFeatureCsvRef: PlRef; // tag->feature CSV (spec A-0004, A-0009)
  controlFeature?: string; // negative-control feature name (spec A-0014); omitted -> no score
  dominanceThreshold: number; // spec A-0012, default 0.6, floor 0.5
};

/** Unified persisted UI state. */
export type BlockData = {
  fbFastqRef?: PlRef;
  tagFeatureCsvRef?: PlRef;
  controlFeature?: string;
  dominanceThreshold: number;
  tableState: PlDataTableStateV2; // PlAgDataTableV2 grid state (UI-only, never projected to args)
};
