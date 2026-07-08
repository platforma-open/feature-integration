import type { Preset } from "../types";

// User-configurable single-cell feature-barcode kit. Covers non-BEAM-Core reagents on the same 5'
// chemistry — e.g. TotalSeq-C / next-gen antigen barcoding, whose 15 nt barcode sits behind a 10 nt
// lead on Read 2 (set the Read 2 offset to 10). "Feature barcode", not "amplicon": these are single-cell
// tag reads, not targeted-insert amplicons.
const preset: Preset = {
  id: "generic-fb-umi",
  vendor: "",
  kit: "Custom",
  label: "Custom feature-barcode kit",
  description:
    "Single-cell feature-barcode reads: cell barcode + UMI on Read 1, feature barcode on Read 2. Use the builder to set the cell/UMI/feature lengths and an optional Read 2 offset (10 for TotalSeq-C), or paste a raw mitool pattern.",
  pattern: "",
  userConfigurable: true,
};

export default preset;
