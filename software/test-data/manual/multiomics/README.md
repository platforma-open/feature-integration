# Full BEAM-Ab multiomics manual run — data + per-block settings

End-to-end manual test of the BEAM-Ab single-cell multiomics pipeline on **coherent synthetic data**
(the `realistic` profile, calibrated to a real 5k BEAM-T dataset — see `../antigen/real-data-calibration.md`).

**Pipeline:** `samples-and-data` → three parallel arms (**antigen** `feature-integration`, **VDJ**
`import-vdj-data`, **GEX** `import-sc-rnaseq-data` + `cell-type-annotation`) → **`vdj-multiomic-integration`**
(convergence) → **`antibody-tcr-lead-selection`** (payoff).

All data here is **gitignored** and reproducible from the generators (`generate_vdj.py`, `generate_gex.py`,
+ `../antigen/generate.py`); validate with `validate_multiomics.py`.

---

## Prerequisites

1. **Build each block from the RIGHT source (verified 2026-07-01).** `blocks/feature-integration` and
   `blocks/vdj-multiomic-integration` are **stubs** (the former emits no outputs; the latter is
   README-only) — building from them makes the convergence step's feature dropdown empty so it cannot
   run. Build/publish from:

   | Block | Build from |
   |---|---|
   | feature-integration | worktree `MILAB-6496_feature-integration-wip` (NOT `blocks/`) |
   | vdj-multiomic-integration | worktree `MILAB-6496_vdj-multiomic-integration` (NOT `blocks/`) |
   | antibody-tcr-lead-selection | `blocks/` (main) — its worktree is a different, older ticket |
   | import-vdj-data, import-sc-rnaseq-data, cell-type-annotation, samples-and-data | `blocks/` (main) |

   `pnpm build:dev` in each.
2. **A backend running** (`./scripts/run-platforma.sh start --bg`). A prebuilt backend is fine — the FI
   and convergence blocks use published / block-internal software only (no dev-only `file:` overrides).
   The backend must have these **assets cached** (fine on an online/registry-connected backend, matters
   only for strictly-offline): `gene-annotations-assets:homo-sapiens` (import-sc-rnaseq) and the
   CellTypist model assets (cell-type-annotation).
3. **Blocks available** in the project palette: the seven listed above.

### ⚠️ Two settings that will silently break the run if wrong

- **Match the cell whitelist to the profile.** The default guide below uses the **`realistic`** profile,
  whose cell barcodes are random 16-mers → **Feature Integration → Advanced → "Cell barcode whitelist
  (10x)" = `None — de-novo`** (a real whitelist would drop every cell → empty join). The alternative
  **`whitelist737k`** profile has real 737K-compliant barcodes → set the whitelist to `737K-august-2016`
  (see "737K-compliant variant" below). Getting this wrong empties the join or noises up the FI table.
- **One profile everywhere.** Use a single profile's paths for all three arms. Don't mix profiles.

---

## The one rule: the canonical cell barcode

The convergence join is a silent inner-join on `[sampleId, cellId]`. All three arms resolve to the
**bare 16 nt** barcode, so they line up:

| Arm | barcode handling |
|---|---|
| Antigen (`feature-integration`) | bare 16 nt from R1, de-novo corrected (error-free input → verbatim) |
| VDJ (`import-vdj-data`, `airr-sc`) | `cell_id` used verbatim (`cellKeyMode=direct`) |
| GEX (`import-sc-rnaseq-data`) | strips any `-N` suffix → bare 16 nt |

---

## Sample model & assumptions (revisit against real data)

This synthetic cohort assumes **one capture = one donor**: each donor is a single BEAM-Ab capture whose
three libraries (antigen / VDJ / GEX) share one 16 nt cell barcode and import as **one Samples & Data
sample with three datasets** — i.e. one shared `sampleId`. The convergence join depends on that shared
`sampleId` (see the canonical-barcode rule above). Two assumptions are baked in here that we should **not
over-index on** — confirm them against real BEAM data before treating them as fixed:

- **Filenames carry no library suffix.** All three arms write `donorNN.<ext>` (antigen
  `donorNN_R{1,2}.fastq.gz`, VDJ `donorNN.tsv`, GEX `donorNN.csv`), so Samples & Data extracts the same
  `donorNN` stem from each and mints **one** shared `sampleId`. This is a deliberate synthetic-data
  convenience. **Real deliveries may name files per library** (e.g. `donorNN_airr_sc`, `donorNN_counts`) —
  Samples & Data would then fork each donor into separate per-library samples with disjoint `sampleId`s,
  and the convergence `[sampleId, cellId]` join returns nothing. If real data arrives that way, import as
  one sample per donor (multiple datasets under it) or collapse the sampleIds in Samples & Data — do **not**
  assume filenames are already suffix-free.
- **Pooled donors are out of scope (for now).** Real cohorts often **pool several donors in one capture**
  and separate them afterward by genetic / hashtag demultiplexing. This test bed and the block both assume
  **separate per-donor captures** — spec-consistent, since demux is a separate/optional upstream step
  (atoms A-0002 / A-0005 defer per-sample stratification). Pooled input + demux is a **future input mode**,
  not a current guarantee; don't hard-code a 1:1 capture↔donor relationship downstream. (No
  `demultiplex-fastq` block in the guide below — samples are already per-donor.)

---

## Data manifest (realistic profile) — what to upload where

Scale is set by the antigen generator's flags (`--samples`/`--panel-size`/`--cells-per-sample`) and the
VDJ/GEX arms follow it automatically. Defaults: **24 donors** (`donor01`…`donor24`), **2000 cells/donor**,
a **15-antigen panel + control** (real 10x BEAM Core Kit size; up to 4 real 10x anchors + the rest
synthetic; pass `--panel-size 64` for a capacity stress test). One Fastq pair / VDJ TSV / GEX CSV
per donor:

| Arm | Dataset type in Samples & Data | per-donor file (`donor01`…`donorNN`) |
|---|---|---|
| **Antigen** | Fastq (R1, R2; gzipped) | `../antigen/realistic/donorNN_R{1,2}.fastq.gz` |
| **VDJ** | Table / Xsv (**tsv**) | `vdj/realistic/donorNN.tsv` |
| **GEX** | Table / Xsv (**csv**) | `gex/realistic/donorNN.csv` |

> The 24×2000 default is a big manual upload — for a hand run, regenerate at a smaller scale first
> (e.g. `python3 ../antigen/generate.py --profile realistic --samples 2
> --cells-per-sample 80`, then the VDJ/GEX arms), which is closer to the old 2-donor bed.

Supporting files (uploaded inside a block, not as a Samples & Data dataset):
- **Tag → feature panel CSV** (Feature Integration upload): `../antigen/realistic/tags.csv`
  — `--panel-size` antigens + `negative_control` (default 16 features = 15 antigens + control;
  first up-to-4 antigens are the real 10x anchors).
- **Sample metadata (optional)**: `../antigen/realistic/samples-metadata.tsv`
  (`Sample / Donor / Condition`). Import in Samples & Data only if you want grouping labels downstream —
  the pipeline and the join do **not** need it.

---

## Per-block settings (run each block, in order; press **Run** before adding the next)

### 0 · Samples & Data
- Create one sample per donor (`donor01`…`donorNN`).
- **Dataset 1 — Fastq** (read indices **R1, R2**; **gzipped ✓**): upload each donor's antigen R1/R2.
- **Dataset 2 — Table / Xsv (tsv)**: each donor's VDJ `donorNN.tsv`.
- **Dataset 3 — Table / Xsv (csv)**: each donor's GEX `donorNN.csv`.
- *(Optional)* import `samples-metadata.tsv` as metadata.
- **Run.** (No `demultiplex-fastq` — samples are per-sample, not pooled.)

### 1 · Feature Integration (antigen)
| Setting | Value |
|---|---|
| Feature-barcode FASTQ | Dataset 1 (Fastq) |
| Tag → feature CSV (upload) | `../antigen/realistic/tags.csv` |
| Negative-control feature | `negative_control` (dropdown is populated from the CSV) — enables specificity scoring |
| Advanced → Dominance threshold | `0.6` |
| Advanced → Cell / UMI / Feature length | `16` / `10` / `15` |
| **Advanced → Cell barcode whitelist (10x)** | **`None — de-novo`** ← required (see warning above) |

**Run** → per-cell feature table with `pl7.app/feature/umiCount` (+ `fraction`, `consensus`, and
`specificityScore` since a control is set).

### 2 · Import V(D)J Data
| Setting | Value |
|---|---|
| Dataset | Dataset 2 (Xsv-**tsv**) |
| Format | **AIRR single cell** |
| Chains / Receptor | **IG** (→ IG Heavy + IG Light; our IGH + IGK rows satisfy this) |

Note: there is **no** "Primary count type" control for AIRR single-cell (it only appears for the `custom`
format). The block reads `duplicate_count` as read-count; the single-cell anchor is a **cell count**
(`pl7.app/vdj/uniqueCellCount`) regardless, which is what the convergence block matches. Chain routing
keys off `v_call` (must start `IGHV…`/`IGKV…`), not the `locus` column — our data satisfies this.

**Run** (enables after header validation) → sc-clonotype dataset + `pl7.app/sc/cellLinker` + anchor
`pl7.app/vdj/uniqueCellCount` (carries `pl7.app/isAnchor:"true"`).

### 3 · Import scRNA-seq Data (GEX)
| Setting | Value |
|---|---|
| Dataset | Dataset 3 (Xsv-**csv**) |

Species (human) + gene format (Ensembl) auto-detect from the `ENSG…` IDs. **Run** →
`pl7.app/rna-seq/countMatrix`.

### 4 · Cell Type Annotation
| Setting | Value |
|---|---|
| Counts input | the count matrix from block 3 |
| Model | **Human healthy immune populations** (default) |

**Run** → `pl7.app/rna-seq/cellType`. (Cells are B-lineage; binders should read as B/plasma. Any label
still flows downstream — annotation is optional.)

### 5 · VDJ Multiomic Integration (convergence)
| Setting | Value |
|---|---|
| VDJ single-cell dataset | the Import V(D)J dataset (block 2) — **required** (block throws without it) |
| Feature Integration per-cell column | the `umiCount` column (block 1) — **required** (block throws without it) |
| Gene expression (optional) | the `countMatrix` column (block 3) |
| Cell-type annotation (optional) | the `cellType` column (block 4) |
| Dominance threshold | `0.6` (floor 0.5) |
| Presence threshold | `0.0` |
| Expression aggregation | **Mean** |

The **cell linker is auto-resolved** workflow-side from the VDJ dataset — there is no linker setting to
pick; just make sure block 2 has run so the linker exists in the pool.

**Run** → per-clonotype table. **A non-empty table is the core pass** (proves the three-arm barcode join).
Per-clonotype outputs (keyed on `scClonotypeKey`, no sample axis): `dominantFeature`, `restrictionIndex`,
`breadth`, per-feature `clonotypeUmiCount`/`clonotypeFraction`, and (with GEX/annotation)
`clonotypeExpression`/`clonotypeDominantCellType`.

### 6 · Antibody / TCR Lead Selection *(optional payoff)* — build from `blocks/` (main)
| Setting | Value |
|---|---|
| Input dataset (primary) | the VDJ sc-clonotype dataset (block 2 anchor; `isAnchor` + `[sampleId, scClonotypeKey]`) |
| Rank by | a per-clonotype column from block 5 — e.g. **`restrictionIndex`** (antigen restriction) or `breadth`. NB: there is **no** per-clonotype "specificity score"; FI's specificity is per-cell only. |
| Take from | **Highest** (= descending) |
| Number of sequences to select | e.g. `10` — **required** (block throws if empty) |
| Filters | none (a half-filled ranking/filter card also throws) |

**Run** → the lead clones surface as top antibody candidates.

> ⏳ **One live check remains:** the convergence columns are spec-compatible with lead-selection's ranking
> (sampleId-free, `scClonotypeKey`-keyed, non-String), but the block doesn't reference them by name in
> source. Confirm `restrictionIndex`/`breadth` actually appear in the "Rank by" dropdown on a live run.

---

## Expected results (ground truth)

The realistic dataset plants a **lead clone per (donor, clear antigen)** — so up to `--panel-size` lead
clones per donor, each binding one antigen — plus minor/ambiguous clones. The invariant to check:

- **every clear-antigen clonotype's dominant antigen == its planted antigen** (target UMIs ≫ control), and
- **plasma-marker expression is higher in binder clonotypes than naive/ambiguous ones**.

`validate_multiomics.py` asserts both offline and prints the exact clonotype count, per-donor lead-clone
previews, and the binder-vs-naive plasma means for the current scale (they scale with
`--samples`/`--panel-size`/`--cells-per-sample`, so don't hardcode them). Ground-truth files:
`vdj/realistic/truth_clonotypes.csv` (clone → target antigen), `gex/realistic/truth_cells_gex.csv`,
`../antigen/realistic/expected-consensus.tsv`.

---

## Offline validation (no backend)

```bash
# antigen (in ../antigen/) — scale flags optional (defaults 24/15/2000: donors/antigens/cells)
python3 generate.py --profile realistic
# arms (here) — no scale flags; they follow the antigen arm automatically
python3 generate_vdj.py --realistic && python3 generate_gex.py --realistic
python3 validate_multiomics.py --realistic     # → ALL PASS (check count scales with the data)
```
All three generators and the validator are **stdlib-only** (no numpy/polars). The validator streams the
FASTQs and indexes the join by cell, so it stays tractable at scale. Measured at the 24×2000 default: the
full chain (generate antigen + VDJ + GEX, then validate) ran in **~6 min** end to end. Regenerate whenever
you change the generators or the scale flags.

`validate_multiomics.py --realistic` re-derives per-(cell, antigen) UMIs from the antigen FASTQ, joins to
the VDJ linker, and confirms every clear-antigen clonotype's dominant antigen matches the planted biology
— i.e. it predicts the block-5 result. An in-app mismatch therefore points at a block/config issue, not
the data.

---

## 737K-compliant variant (exercise the cell whitelist)

The guide above uses the `realistic` profile (random barcodes; run with cell whitelist = None). To
exercise the **cell-barcode whitelist** end to end, use the **`whitelist737k`** profile instead: its cell
barcodes are **real `737K-august-2016` members** — sampled from the full 10x inclusion list
(`../antigen/737K-august-2016.txt`, fetched on demand; falls back to the harvested
`whitelist_cells.txt` pool) — plus a realistic **ambient off-list read tail**, so the whitelist does real
work.

Generate it:
```bash
# antigen (in ../antigen/) — same scale flags as realistic
python3 generate.py --profile whitelist737k
# arms (here)
python3 generate_vdj.py --profile whitelist737k && python3 generate_gex.py --profile whitelist737k
python3 validate_multiomics.py --profile whitelist737k     # → ALL PASS
```

Run it exactly like the guide above, with two changes:
- **Data paths** use `whitelist737k/` in place of `realistic/` (antigen
  `../antigen/whitelist737k/…`, VDJ `vdj/whitelist737k/…`, GEX `gex/whitelist737k/…`).
- **Feature Integration → Advanced → Cell barcode whitelist = `737K-august-2016`** (not None).

What it demonstrates (verified offline with mitool 2.3.1-131-main): `refine-tags
-t CELL#builtin:737K-august-2016` keeps the real cells (the `--cells-per-sample` planted per donor) and
**drops the ~15% ambient off-list tail**, whereas de-novo keeps that tail as phantom low-count cells. With
the whitelist on, FI's `cellId`s are the canonical 737K strings that match the VDJ/GEX arms by construction
— the same normalization real Cell Ranger/mixcr apply.

## Background

Design rationale, the join-spine axis contract, per-arm file schemas, and the biology/coherence model are
in **`design-and-schemas.md`** (the consolidated scoping report + generator spec).
