import type { Preset } from "../types";

// Chromium Single Cell 5′ Barcode Enabled Antigen Mapping (BEAM), the block's default and the only
// chemistry 10x supports for BEAM. GEM-X and v3 do not support it. 5′ v2: CELL 16 + UMI 10 on Read 1,
// 15 nt feature barcode at the start of Read 2. Geometrically identical for BEAM-Ab and BEAM-T.
// Ref: https://www.10xgenomics.com/support/universal-five-prime-gene-expression/documentation/steps/experimental-design-and-planning/chromium-single-cell-5-barcode-enabled-antigen-mapping-beam-–-experimental-planning-guide
const preset: Preset = {
  id: "tenx-beam",
  vendor: "10x Genomics",
  kit: "BEAM",
  // `label` is the product name only. The UI appends " — {vendor}" for the dropdown.
  label: "Chromium Single Cell 5′ BEAM",
  description:
    "Chromium Single Cell 5′ Barcode Enabled Antigen Mapping (BEAM): 16 nt cell barcode + 10 nt UMI on Read 1, 15 nt feature barcode at the start of Read 2. 5′ v2 chemistry; covers BEAM-Ab and BEAM-T.",
  pattern: "^(CELL:N{16})(UMI:N{10})*\\^(FEATURE:N{15})(R2:*)",
};

export default preset;
