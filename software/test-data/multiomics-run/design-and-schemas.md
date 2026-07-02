# Multiomics synthetic data — design, schemas, and the join contract

Background for the manual run in `README.md`: the experiment modeled, the pipeline, the axis contract the
data must satisfy, per-arm file schemas (verified against block code), the coherence model, and the
viability tests. (Consolidates the former `multiomics-manual-test-data-report.md` scoping report +
`multiomics-generator-spec.md` build spec.)

---

## 1. The experiment: BEAM-Ab

One GEM emulsion produces **three co-registered 10x 5′ v2 libraries from the same cells**, all sharing
**one 16 nt cell barcode** (from the 5′ gel-bead list `737K-august-2016`) — the *only* multiomic linking key.

| Library | R1 | R2 | Purpose |
|---|---|---|---|
| Gene Expression (GEX) | 16 nt CB + 10 nt UMI | cDNA (5′) | transcriptome / cell typing |
| BCR V(D)J | 16 nt CB + 10 nt UMI | V(D)J contig | paired IGH + IGK/IGL → clonotype |
| Antigen Capture (BEAM) | 16 nt CB + 10 nt UMI | 15 nt antigen barcode @ pos 0 + adapter | per-cell antigen binding |

UMIs are independent per library; the **cell barcode string** is the shared key. Specificity score
(Cell Ranger BEAM, which `feature-integration` reproduces): `(1 − beta.cdf(0.925, antigenUMI+1, controlUMI+3)) × 100`.

**Why the import path (not raw FASTQ + Cell Ranger + MiXCR):** only the antigen arm has no import entry
point, so only it needs synthetic FASTQ. GEX (`import-sc-rnaseq-data`) accepts a count matrix; VDJ
(`import-vdj-data`) accepts an AIRR contig table and emits the clonotype key + linker directly. Three
lightweight assets off one shared barcode population — no aligners, no references.

---

## 2. Pipeline

```
                         samples-and-data
        ┌─────────────────────────┼──────────────────────────────┐
   GEX arm                    VDJ arm                         Antigen arm
 import-sc-rnaseq-data     import-vdj-data                 feature-integration
        │                        │                                │
 rna-seq/countMatrix    anchor: vdj/uniqueCellCount        feature/umiCount
   [cellId, geneId]     linker: sc/cellLinker              [sampleId, cellId, featureId]
        │                [sampleId, cellId, scClonotypeKey]       │
 cell-type-annotation           │                                 │
   → rna-seq/cellType           │                                 │
        └───────────────┬───────┴─────────────────────────────────┘
                        ▼
             vdj-multiomic-integration
   anchor = VDJ sc-clonotype dataset; REQUIRED: feature umiCount + cellLinker;
   OPTIONAL: GEX countMatrix, cellType. Inner-join on [sampleId, cellId], group by scClonotypeKey.
                        ▼
             antibody-tcr-lead-selection  → top-N antibody leads
```

---

## 3. The canonical cell barcode (the one rule)

The convergence join is a **silent inner-join on `[sampleId, cellId]`** — any barcode mismatch drops
cells with no error. The canonical `cellId` = the **bare 16 nt** barcode. Verified per-arm normalization:

| Arm | barcode handling | verified at |
|---|---|---|
| Antigen (`feature-integration`) | bare 16 nt from R1; de-novo corrected (error-free input → verbatim) | tag-pattern / mitool CELL |
| VDJ (`import-vdj-data`, `airr-sc`) | `cell_id` verbatim (`cellKeyMode:"direct"`) | `formats.lib.tengo:126-146` |
| GEX (`import-sc-rnaseq-data`) | strips `-\d+$` suffix → bare 16 nt | `clean_barcode_suffix` |

Synthetic barcodes are random 16-mers (not real `737K` members), so keep them **error-free** and leave
cell-barcode whitelist correction **off** in the run (a whitelist would drop them all). The de-novo-error
scenario is for *standalone* `feature-integration` testing only — it would split cells across arms.

---

## 4. The join-spine — axes that must align (byte-identical name + domain)

| Axis / column | valueType | Key annotations | Produced by | Consumed by |
|---|---|---|---|---|
| `pl7.app/sampleId` | String | — | samples-and-data | all |
| `pl7.app/sc/cellId` | String | `parents=[sampleId]`, no domain | all three arms | the linker; **the multiomic key** |
| `pl7.app/vdj/scClonotypeKey` | String | domain: receptor/structure/runId | import-vdj-data | integration anchor + outputs; lead-selection |
| `pl7.app/vdj/uniqueCellCount` | Int/Long | **`isAnchor:"true"`**, `isAbundance` | import-vdj-data | integration `datasetOptions` anchor |
| `pl7.app/sc/cellLinker` | Int | **`isLinkerColumn:"true"`**, axes `[sampleId, cellId, scClonotypeKey]` | import-vdj-data | integration (REQUIRED linker) |
| `pl7.app/feature/umiCount` | Int | `isAbundance` | feature-integration | integration (REQUIRED feature) |
| `pl7.app/feature/featureId` | String | — | feature-integration | integration feature axis |
| `pl7.app/rna-seq/countMatrix` | Double | axes `[sampleId, cellId, geneId]` (geneId domain `{species}`) | import-sc-rnaseq-data | integration (OPTIONAL GEX) |
| `pl7.app/rna-seq/cellType` | String | axes `[sampleId, cellId]` | cell-type-annotation | integration (OPTIONAL annotation) |

Integration mechanism: materialize `cellLinker` → `linker.csv [sampleId, cellId, scClonotypeKey]`; write
each per-cell input to its own CSV; inner-join each to the linker on `[sampleId, cellId]`; group by
`scClonotypeKey`. Outputs reuse `scClonotypeKey` verbatim, joining back onto the VDJ clonotype table.

---

## 5. Per-arm file schemas (verified against block code)

All three upload through **one Samples & Data block** as datasets keyed by the same `sampleId`(s).

### 5.1 Antigen — `feature-integration` (paired FASTQ)
- **R1** (`*_R1.fastq.gz`): `[16 nt cell barcode][10 nt UMI]` = 26 nt.
- **R2** (`*_R2.fastq.gz`): `[15 nt antigen barcode @ pos 0][tail]`.
- **Panel CSV** (`tag,feature`): antigens + `negative_control`; barcodes pairwise Hamming ≥ 3.
- Error-free cell barcodes in the multiomics dataset.

### 5.2 VDJ — `import-vdj-data`, format `airr-sc` (AIRR rearrangement TSV, one row per contig)
Columns present: `cell_id`, `locus`, `v_call`, `j_call`, `c_call`, `junction`, `junction_aa`,
`productive`, `duplicate_count`.
- `cell_id` = bare 16 nt (used verbatim). `junction` = CDR3 nt (ACGT, len %3==0 for productive).
- `v_call`/`j_call` = real IMGT gene names. `duplicate_count` (Int ≥1) = UMI support → primary abundance,
  gets `isAnchor:"true"` (`infer-columns-airr.lib.tengo:141`).
- Each cell has ≥1 IGH + ≥1 IGK/IGL row. Clonotype key = CDR3-nt + V + J (+ C). Cells with identical
  paired rows → same `scClonotypeKey` (lead clones); unique rows → singletons.
- Consumer settings: format = "AIRR single cell", chains = IG Heavy + IG Light.

### 5.3 GEX — `import-sc-rnaseq-data` (CSV, genes-in-rows)
- First column = **gene IDs** (real Ensembl `ENSG…`); header = **cell barcodes**; body = non-neg integer counts.
- `detect_orientation` → genes-in-rows on an all-numeric body; `check_format` passes on gene-like first col.
- Include real marker genes per class (B: MS4A1/CD79A; plasmablast: MZB1/XBP1/PRDM1) so
  `cell-type-annotation` is meaningful. No mapping file needed — species/format inferred.

---

## 6. Coherence model — one cell, three consistent modalities

Each synthetic cell gets a latent identity; all three arms derive from it, so the downstream result is
assertable.

| Cell class | VDJ | Antigen (UMIs) | GEX program |
|---|---|---|---|
| **Lead B cells** (few dominant clones) | one of clones L1..L4 (paired IGH+IGK/L) | high on-target, low control → specificity ~100 | plasmablast: MZB1/XBP1/PRDM1 high |
| **Background B cells** | many singleton clones | antigen ≈ control (low) | naive-B: MS4A1/CD79A/TCL1A |
| **Non-B contaminants** (optional) | absent from VDJ | none / control-only | T/myeloid |

Each lead clone binds exactly one antigen. **Every lead-clone cell appears in all three arms** (survives
the inner-join). Background/contaminant cells may be partial (realistic per-arm dropout). Seeded RNG.

---

## 7. Viability tests (`validate_multiomics.py`, stdlib-only)

- **Barcode alignment** (the test): the `cellId` set each arm will produce must overlap as intended; every
  lead-clone cell ∈ all three sets.
- **Per-arm schema/geometry:** antigen R1=26/R2≥15, on-panel; VDJ AIRR header + heavy/light pairing +
  junction validity; GEX orientation + Ensembl IDs + non-neg + no all-zero row/col + markers elevated.
- **Join simulation** (strongest offline proof): build the linker from VDJ pairing, join the antigen
  per-cell UMIs on `cellId`, group by clonotype, apply the dominant-antigen + specificity rules; assert
  the per-clonotype table is non-empty and L1..L4 show their intended antigen + high specificity.
- Realistic profile: **38/38 PASS**.

---

## 8. Verification status (source review, 2026-07-01)

A source-level review of all seven blocks (not just the docs) confirmed the join spine ties together.
Resolved items (previously "assumed"):

1. **`import-vdj-data` per-clonotype anchor carries `isAnchor` — VERIFIED.** The SC anchor
   `pl7.app/vdj/uniqueCellCount` (cell count) is freshly authored with `isAnchor:"true"` at the
   per-clonotype stage (`process-single-cell.tpl.tengo:191`), so aggregation can't strip it, and it
   matches the integration `datasetOptions` predicate (`[sampleId, scClonotypeKey]` + isAnchor).
2. **Samples & Data `Xsv` → import dropdowns — VERIFIED.** Xsv publishes `pl7.app/sequencing/data`/File
   with `pl7.app/fileExtension` (csv/tsv); import-vdj matches tsv, import-sc matches csv/tsv.
3. **cellId join key — VERIFIED byte-identical** (`pl7.app/sc/cellId`, String, no domain) across FI,
   the VDJ linker, the GEX countMatrix, and cellType. The integration join is a Python inner-join on
   `[sampleId, cellId]` grouped by `scClonotypeKey`; cellType's cell axis is inherited from its input.

Remaining risks / live checks:

- **Build from the right source (CRITICAL):** `blocks/feature-integration` (stub, no outputs) and
  `blocks/vdj-multiomic-integration` (README-only) are NOT the real code — build both from their
  MILAB-6496 worktrees, else the convergence feature dropdown is empty and it can't run.
- **Silent inner-join on cellId:** a barcode mismatch drops cells with no error → keep barcodes
  byte-identical bare-16nt and match the cell-whitelist setting to the profile.
- **Single-receptor only:** import-vdj emits one linker/anchor per receptor (receptor domain); a
  TCR+BCR dataset publishes multiple `pl7.app/sc/cellLinker` columns and the integration's `addSingle`
  expects exactly one. BEAM-Ab (BCR/IG) is safe.
- **lead-selection ranking (live check):** the integration's per-clonotype outputs (`restrictionIndex`,
  `breadth`, `dominantFeature`, keyed on `scClonotypeKey`, no sample axis) are spec-compatible with
  lead-selection's ranking discovery, but confirm they appear in its "Rank by" dropdown on a live run.
- **Backend assets:** import-sc needs `gene-annotations-assets:homo-sapiens`; cell-type needs the
  CellTypist model assets — cached automatically online, required for a strictly-offline backend.

**Tiering:** Tier 0 = VDJ + antigen → integration (feature + linker only). Tier 1 adds GEX + annotation
(the full run in `README.md`). Tier 2 = wider ecosystem on the same cells.

---

## Key code references

- Convergence: `vdj-multiomic-integration/.../model/src/index.ts`, `.../workflow/src/{main,aggregate}.tpl.tengo`, `.../software/aggregate-clonotypes/`.
- VDJ import: `blocks/import-vdj-data/workflow/src/{process-single-cell.tpl,infer-columns-airr.lib,formats.lib}.tengo`.
- GEX import: `blocks/import-sc-rnaseq-data/workflow/src/libs/pf-counts-conv.lib.tengo`.
- Antigen: this block (`feature-integration`) + `docs/cell-whitelist-correction-plan.md`.
