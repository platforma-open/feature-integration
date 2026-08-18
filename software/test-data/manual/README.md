# Manual BEAM-Ab Test Data

Synthetic, **coherent** single-cell BEAM-Ab data for driving the **Feature Integration (FI)** and **VDJ
Multiomic Integration (VDJM)** blocks by hand — from a one-arm FI check up to the full multiomic
convergence chain. Everything here is a **recipe**: only the generators (`generate.py` + `lib/*.py`) and
docs are tracked; all generated data (`runs/**`) is gitignored and rebuilt on demand.

Standard library only, deterministic (seeded). One command builds a whole run:

```bash
python3 generate.py realistic       # full multiomic run (all 3 arms) + offline validation
python3 generate.py tiny            # small, fast hand-upload version of the same
python3 generate.py --scenario errors   # an antigen-only behavioural bed
```

## Layout

A run is **colocated** — one folder holds all three arms plus the shared uploads and ground truth, so a
multiomic run no longer means jumping between arm directories:

```
runs/<preset>/                 a full multiomic run
  antigen/  donorNN_R{1,2}.fastq.gz          (Feature Integration input)
  vdj/      donorNN.tsv                       (Import V(D)J input, AIRR single-cell)
  gex/      donorNN.csv                       (Import scRNA-seq input, genes-in-rows)
  annotations/ donorNN.tsv                    (per-cell cell-type/cluster; only with --with-annotations)
  tags.csv  feature_reference.csv  samples-metadata.tsv   (block uploads)
  truth/    expected-abundance.tsv  expected-consensus.tsv
            truth_clonotypes.csv  truth_cells_gex.csv
runs/scenarios/<name>/         antigen-only behavioural beds (self-contained)
assets/                        fetched/harvested inputs (see Assets); gitignored except whitelist_cells.txt
lib/                           the arm generators (antigen, vdj, gex, annotations, panel, panelswap, validate)
```

`fixtures/per-cell-metrics/` (a sibling of `manual/`, under `test-data/`) is separate: a tiny **committed**
bed the automated pytest reads directly. It is out of scope here — this tree is all manual/regenerable.

## Presets

A preset bundles scale + calibration + cell-barcode source into a named run. All are calibrated to a real
5k BEAM-T dataset (see `real-data-calibration.md`); any dimension is overridable.

| preset | donors | cells/donor | antigens (+control) | cell barcodes |
|---|---|---|---|---|
| `tiny` | 2 | 80 | 4 | random 16-mers |
| `realistic` | 24 | 2000 | 15 | random 16-mers |
| `whitelist737k` | 24 | 2000 | 15 | real `737K-august-2016` members + ambient tail |

Overrides: `--samples N`, `--cells-per-sample K`, `--panel-size M`, `--out DIR`. The panel scales up to
64 antigens (`--panel-size 64`) for a capacity stress test — the first up-to-4 are the real 10x anchors,
the rest synthetic 15-mers (pairwise Hamming ≥ 3).

At the `realistic` default (~48k cells, ~42M read pairs) a run is ~1 GB and takes ~6 min end to end. For a
hand upload prefer `tiny` (or `realistic --samples 2 --cells-per-sample 80`).

## Commands

```bash
python3 generate.py realistic                 # all arms into runs/realistic/, then validate
python3 generate.py tiny                       # runs/tiny/
python3 generate.py whitelist737k              # runs/whitelist737k/ (see the 737K variant below)
python3 generate.py realistic --arm antigen    # one arm only (others must already exist in the run)
python3 generate.py realistic --no-validate     # skip the offline validator
python3 generate.py realistic --validate-only    # re-validate an existing run, no regeneration
python3 generate.py --scenario errors           # runs/scenarios/errors/  (also: offpanel, multilane,
python3 generate.py --scenario panel-swap       #   control, degraded, panel-swap, multisample)
```

## The One Rule: The Canonical Cell Barcode

The convergence join is a silent inner-join on `[sampleId, cellId]`. All three arms resolve to the **bare
16 nt** barcode, so they line up:

| Arm | barcode handling |
|---|---|
| Antigen (`feature-integration`) | bare 16 nt from R1, de-novo corrected (error-free input → verbatim) |
| VDJ (`import-vdj-data`, `airr-sc`) | `cell_id` used verbatim (`cellKeyMode=direct`) |
| GEX (`import-sc-rnaseq-data`) | strips any `-N` suffix → bare 16 nt |

Because the generators plant the same barcode across arms and Samples & Data derives one `sampleId` from
the bare `donorNN` filename stem, the join is non-empty by construction. `generate.py` runs the offline
validator after every full run to prove this before any backend run.

## Read Geometry (Matches The Block Defaults Exactly)

10x 5′ v2 (read = R2, barcode at R2 position 0):

```
R1 (26 bp) = CELL(16) + UMI(10)
R2 (25 bp) = feature barcode(15) + filler   ← block reads the first 15 bp as the feature; rest ignored
```

This is exactly what `workflow/src/tag-pattern.lib.tengo` builds:
`^(CELL:N{16})(UMI:N{10})\^(FEATURE:N{15})(R2:*)` with `cellLen=16, umiLen=10, featureLen=15`.

## Antigen Panel

The first up-to-4 barcodes are the **real** 10x BEAM-Ab panel from the public *"2k transgenic HEL mouse
splenocytes (BEAM-Ab)"* dataset; the rest are synthetic 15-mers filling the panel to `--panel-size`.

| antigen | feature barcode (15 bp) | source |
|---|---|---|
| SARS-TRI-S_WT | `CGATGCCGGACGATC` | real 10x anchor |
| Anti-Hen_Egg_Lysozyme | `CCGTCTCACCGATAT` | real 10x anchor |
| gp120 | `GATTGGCTACTCAAT` | real 10x anchor |
| H5N1 | `CGGCTCACCGCGTCT` | real 10x anchor |
| `antigen_005` … `antigen_NNN` | synthetic (deterministic) | fills the panel to `--panel-size` |
| negative_control | `CTATCTACCGGCTCG` | real 10x anchor |

---

# Running The Blocks In The App

The full chain, verified live (project **BEAM7**, 2026-07). Blocks and per-block settings for exercising
**FI** and **VDJM** on this data:

**Pipeline:** `samples-and-data` → three arms (**antigen** `feature-integration`, **VDJ**
`import-vdj-data`, **GEX** `import-sc-rnaseq-data` + `cell-type-annotation`) →
**`vdj-multiomic-integration`** (convergence) → **`sequence-space`** + **`antibody-tcr-lead-selection`**
(payoff).

## Prerequisites

1. **Build FI and VDJM from the right source.** `blocks/feature-integration` and
   `blocks/vdj-multiomic-integration` may be stubs — build/publish FI from its
   `MILAB-6496_feature-integration-wip` worktree and VDJM from its own worktree/branch; the other blocks
   (`import-vdj-data`, `import-sc-rnaseq-data`, `cell-type-annotation`, `samples-and-data`, `sequence-space`,
   `antibody-tcr-lead-selection`) build from `blocks/` (main). `pnpm build:dev` in each.
2. **A backend running** (`./scripts/run-platforma.sh start --bg`). A prebuilt backend is fine. It must
   have these assets cached (matters only for strictly-offline): `gene-annotations-assets:homo-sapiens`
   (import-sc-rnaseq) and the CellTypist model assets (cell-type-annotation).

### ⚠️ Two Settings That Silently Break The Run If Wrong

- **Match the cell whitelist to the preset.** `realistic`/`tiny` use random barcodes → **Feature
  Integration → Advanced → "Cell barcode whitelist (10x)" = `None — de-novo`** (a real whitelist would
  drop every cell → empty join). The `whitelist737k` preset has real 737K-compliant barcodes → set the
  whitelist to `737K-august-2016` (see the 737K variant below).
- **One preset everywhere.** Use a single run's paths for all three arms. Don't mix presets.

## Data Manifest — What To Upload Where

One Fastq pair / VDJ TSV / GEX CSV per donor, all under `runs/<preset>/`:

| Arm | Dataset type in Samples & Data | per-donor file |
|---|---|---|
| **Antigen** | Fastq (R1, R2; gzipped) | `runs/<preset>/antigen/donorNN_R{1,2}.fastq.gz` |
| **VDJ** | Table / Xsv (**tsv**) | `runs/<preset>/vdj/donorNN.tsv` |
| **GEX** | Table / Xsv (**csv**) | `runs/<preset>/gex/donorNN.csv` |

Supporting files (uploaded inside a block, not as a Samples & Data dataset):
- **Tag → feature panel CSV** (Feature Integration upload): `runs/<preset>/tags.csv`.
- **Sample metadata (optional)**: `runs/<preset>/samples-metadata.tsv` (`Sample / Donor / Condition`) —
  only if you want grouping labels downstream; the pipeline and the join do not need it.

## Per-Cell Annotation (Optional; `--with-annotations`)

`python3 generate.py <preset> --with-annotations` also writes `runs/<preset>/annotations/donorNN.tsv`
(one file per donor/sample) — a synthetic **per-cell categorical annotation** independent of the GEX
arm. Columns:

| Column | Meaning |
|---|---|
| `cell_id` | the arm-shared **bare 16 nt** cell barcode (copied verbatim from the antigen truth, so it joins) |
| `cell_type` | one of a small fixed vocabulary: `plasma`, `naive_b`, `memory_b` |
| `cluster` | integer cluster label `0-4`, coherent with `cell_type` |

Cell types are biased by the planted antigen class (binder / cross-reactive → `plasma`; ambiguous /
non-binder → `naive_b`; a fixed share → `memory_b`), so clusters are coherent with the antigen signal.
Everything is deterministic under a fixed seed (`ANNOTATION_SEED` in `lib/common.py`).

**Import route.** Upload each `annotations/donorNN.tsv` as a Table / Xsv dataset, keyed on the cell
barcode, so it imports as a per-cell **String** column on axes `[pl7.app/sampleId, pl7.app/sc/cellId]`.
The generator's canonical axis order is **`[sampleId, cellId]`** (sample outer, cell inner).

**Why it exists.** It feeds vdj-multiomic-integration's **annotation-integration** path — a deterministic
alternative to deriving cell types from the GEX arm via `cell-type-annotation` (CellTypist). Because the
`cell_id` values are the same bare 16-mers as the antigen / VDJ / GEX arms, the annotation joins the
convergence spine on `[sampleId, cellId]` with no barcode drift (see "The One Rule" above).

Off by default: without the flag no `annotations/` dir is written and every other output is byte-identical.

## Per-Block Settings (Run Each Block In Order; Press Run Before Adding The Next)

### 0 · Samples & Data
- Create one sample per donor (`donor01`…`donorNN`).
- **Dataset 1 — Fastq** (read indices **R1, R2**; **gzipped ✓**): each donor's antigen R1/R2.
- **Dataset 2 — Table / Xsv (tsv)**: each donor's VDJ `donorNN.tsv`.
- **Dataset 3 — Table / Xsv (csv)**: each donor's GEX `donorNN.csv`.
- *(Optional)* import `samples-metadata.tsv` as metadata. **Run.**

### 1 · Feature Integration (antigen)
| Setting | Value |
|---|---|
| Feature-barcode FASTQ | Dataset 1 (Fastq) |
| Tag → feature CSV (upload) | `runs/<preset>/tags.csv` |
| Negative-control feature | `negative_control` (dropdown populated from the CSV) — enables specificity scoring |
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

Keep chains to **IG only** — configuring extra chain groups makes Import V(D)J emit one `cellLinker` per
group, and VDJM (which resolves the linker by name) then errors with "single resolve has 2 or more
results". Chain routing keys off `v_call` (`IGHV…`/`IGKV…`), not the `locus` column.

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

**Run** → `pl7.app/rna-seq/cellType`. Cells are B-lineage; binders should read as B/plasma.

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
| Per-antigen settings (optional) | expand a feature to set its own presence threshold, or hide it (e.g. hide `negative_control`) |

The **cell linker is auto-resolved** workflow-side from the VDJ dataset — no linker setting to pick; just
make sure block 2 has run so the linker exists in the pool.

**Run** → per-clonotype table. **A non-empty table is the core pass** (proves the three-arm barcode join).
Per-clonotype outputs (keyed on `scClonotypeKey`, no sample axis): `dominantFeature`, `restrictionIndex`,
`breadth`, per-feature `clonotypeUmiCount`/`clonotypeFraction`, and (with GEX/annotation)
`clonotypeExpression`/`clonotypeDominantCellType`.

### 5b · Sequence Space *(optional; off the Import V(D)J anchor)*
| Setting | Value |
|---|---|
| Input anchor | the Import V(D)J dataset (block 2) `uniqueCellCount` |
| Input mode | **Sequence features** |
| Sequence type | **amino acid** |
| Sequences | **Heavy CDR3 aa (primary)** |
| UMAP | neighbors `15`, min-dist `0.5` |

A UMAP embedding over heavy-chain CDR3 sequences (feeds Lead Selection). **Requires a GPU** (`requireGpu`,
~64 GB job) — skip it on a CPU-only backend; it is not needed for the FI/VDJM convergence check.

### 6 · Antibody / TCR Lead Selection *(optional payoff)*
| Setting | Value |
|---|---|
| Input dataset (primary) | the VDJ sc-clonotype dataset (block 2 anchor; `[sampleId, scClonotypeKey]`) |
| Rank by | a per-clonotype column from block 5 — e.g. **`restrictionIndex`** or `breadth`. There is no per-clonotype specificity score; FI's specificity is per-cell only. |
| Take from | **Highest** (descending) |
| Number of sequences to select | e.g. `10` — **required** (throws if empty) |
| Filters | none (a half-filled ranking/filter card also throws) |

**Run** → the lead clones surface as top antibody candidates.

## Expected Results (Ground Truth)

The run plants a **lead clone per (donor, clear antigen)** plus minor/ambiguous clones. Invariants:
- every clear-antigen clonotype's dominant antigen == its planted antigen (target UMIs ≫ control), and
- plasma-marker expression is higher in binder clonotypes than naive/ambiguous ones.

`generate.py` asserts both offline (the validator) and prints the exact clonotype count, per-donor
lead-clone previews, and binder-vs-naive plasma means for the current scale (they scale with the run size,
so don't hardcode them). Ground-truth files live in `runs/<preset>/truth/`.

## Sample Model & Assumptions (Revisit Against Real Data)

This cohort assumes **one capture = one donor**: each donor's three libraries share one 16 nt barcode and
import as one Samples & Data sample with three datasets (one shared `sampleId`). Two assumptions are baked
in — don't over-index on them:

- **Filenames carry no library suffix.** All three arms write `donorNN.<ext>`, so Samples & Data mints one
  shared `sampleId`. Real deliveries may name files per library (e.g. `donorNN_airr_sc`) — which would fork
  a donor into disjoint per-library samples and empty the `[sampleId, cellId]` join. If real data arrives
  that way, import as one sample per donor (multiple datasets) or collapse the sampleIds in Samples & Data.
- **Pooled donors are out of scope (for now).** Real cohorts may pool donors in one capture and demultiplex
  afterward. This bed and the block assume separate per-donor captures; don't hard-code a 1:1
  capture↔donor relationship downstream.

---

# Scenarios (Antigen-Only)

`python3 generate.py --scenario <name>` → `runs/scenarios/<name>/`. Each is self-contained (own `tags.csv`,
FASTQs, ground truth). Default to a small scale; `--samples`/`--panel-size`/`--cells-per-sample` override.

| scenario | tests | expected |
|---|---|---|
| `errors` | 1 bp errors in ~15% of cell/feature barcodes | refine-tags corrects them → per-cell distinct-UMI ≈ baseline truth |
| `offpanel` | feature barcodes not in `tags.csv` + malformed reads | off-panel dropped by the tags.csv inner-join; malformed dropped at parse |
| `multilane` | two lanes/sample (`fb-pipeline` `keyLength==2` branch) | lane-merged per-cell totals == single-lane baseline |
| `control` | binders + ~30% **true non-binder** population | with the negative control set: binders → high `specificityScore` on their dominant antigen; non-binders → ~0 everywhere (truth: `expected-specificity.tsv`) |
| `degraded` | samples degraded to different levels | block's Quality tag OK/WARN/ALERT + Read-recovery bar show a full spread |
| `panel-swap` | one read set + **three swappable tag→feature CSVs** | swap `panel_full` / `panel_merged` / `panel_core` on the same reads to see the whitelist filter and the tag→feature merge (Spike + Spike-v2 → one call) and off-panel dropping |
| `multisample` | two samples with different binding profiles | exercises the per-sample axis the next block aggregates on |

The `panel-swap` bed writes `beam_R{1,2}.fastq.gz` (clean) + `beam_errors_R{1,2}.fastq.gz` (1 bp errors +
off-panel junk) and the three panel CSVs; re-upload a different panel CSV and re-run to compare.

## Cell-Barcode Correction — De-Novo Default, Optional 10x Whitelist

The **feature/antigen** barcode is always corrected against the panel whitelist (the `tag` column of your
CSV): within-Hamming-1 reads snap to a panel barcode, off-panel reads drop. The **cell** barcode has two
modes (Advanced → "Cell barcode whitelist (10x)"):

- **`None — de-novo`** (default) — clusters observed barcodes; does not snap 1 bp errors to a reference.
  In the `errors` scenario this leaves phantom low-count cells, so the truth is the *ideal*; de-novo output
  shows extras. Use this for `tiny`/`realistic` (random barcodes — a whitelist would drop every cell).
- **A 10x built-in** (e.g. `737K-august-2016`) — snaps cells to that real list; off-list barcodes drop.
  Use this with the `whitelist737k` preset (below).

## 737K-Compliant Variant (Exercise The Cell Whitelist)

```bash
python3 generate.py whitelist737k        # builds runs/whitelist737k/ + validates
```

Its cell barcodes are real `737K-august-2016` members (sampled from `assets/737K-august-2016.txt`, fetched
on demand; falls back to the harvested `assets/whitelist_cells.txt`) plus a realistic ambient off-list read
tail, so the whitelist does real work. Run exactly like the guide above with two changes: use
`runs/whitelist737k/` paths, and set **Feature Integration → Advanced → Cell barcode whitelist =
`737K-august-2016`** (not None). `refine-tags -t CELL#builtin:737K-august-2016` keeps the real cells and
drops the ~15% ambient tail — the same normalization real Cell Ranger/mixcr apply.

## Offline Validation (No Backend)

The validator runs automatically after every full `generate.py <preset>`. To re-validate without
regenerating:

```bash
python3 generate.py realistic --validate-only    # → ALL PASS (count scales with the run)
```

It re-derives per-(cell, antigen) UMIs from the antigen FASTQ, builds the cell→clonotype linker from the
VDJ pairing, inner-joins on cellId, groups by clonotype, and confirms every clear-antigen clonotype's
dominant antigen matches the planted biology — i.e. it predicts the block-5 result. An in-app mismatch
therefore points at a block/config issue, not the data. Stdlib only; streams the FASTQs so it stays
tractable at scale.

## Assets

Two large inputs are fetched on demand (both gitignored; a `curl` hint prints if missing):
- `assets/737K-august-2016.txt` (~12 MB) — the full 10x cell-barcode inclusion list (whitelist737k pool).
- `assets/homo_sapiens_gene_annotations.csv` (~21 MB) — the pipeline's human gene map (real Ensembl IDs
  for the GEX arm; the same map CellTypist uses).

One committed asset: `assets/whitelist_cells.txt` (~800 real `737K-august-2016` barcodes harvested from a
real BEAM-T run) — a non-regenerable fallback pool used only if the full list above is absent.

## Reference Docs

- `real-data-calibration.md` — the per-cell UMI/dup/dominance shapes measured on a real 5k BEAM library
  that the `realistic` calibration targets.
- `design-and-schemas.md` — design rationale, the join-spine axis contract, per-arm file schemas, and the
  biology/coherence model.

## The Two Shapes A Real Panel File Arrives In

`generate.py` emits one panel shape: `tag,feature,Type,Species,Class`, one panel for every sample, with
the control carrying its own `Decoy` role. Two other shapes were observed in use at one account at the
same time, on two of its projects, and neither looks like that. `reshape_panel.py` rewrites a generated
run's `tags.csv` into both, **keeping every barcode unchanged** so either can be uploaded against the
same FASTQs:

```bash
python3 generate.py tiny --arm antigen --panel-size 12 --offtarget-count 3
python3 generate.py tiny --arm vdj --panel-size 12
python3 reshape_panel.py runs/tiny
```

| File | Shape | What it exercises |
|---|---|---|
| `tags_narrow.csv` | `Sample,Sequence,Antigen` | No role column at all, so nothing can be named as the comparator and the panel's own readings serve. The control is an ordinary row nothing marks. |
| `tags_wide.csv` | `Samples,Name,Barcode,Sequence,Channel,Residues,Type` | A role column that declares target vs off-target and carries **no** comparator value; a catalogue id 1:1 with the sequence; a channel column holding four values that are three channels; a constant column; and case-variant role values. |

Both rename a barcode between samples (`--rename`, default 2), so the same sequence carries a different
antigen name in different samples. Under the per-tag grouping the identity is the barcode, so those
identities lose their label and show a raw 15-mer. `--drop-from-later N` makes a later sample declare
fewer tags, which is what makes *never asked* reachable.

**A panel below 8 tags cannot serve as its own comparator**, so generate at least that many
(`--panel-size 12` gives 12 + 1 control). The script warns if you are under.

### ⚠️ Set the count floor to 1 for these two shapes

This bed plants background at 1–3 UMIs per barcode — **253 of 432 readings in a `tiny --panel-size 12`
run sit below the shipped count floor of 4**. Neither shape declares a comparator, so the panel's own
readings have to serve, and with the floor at 4 that background is zeroed, the panel median collapses to
0, and 0 is below the reference thin line of 2. Every cell carrying signal then reads *impossible to
compare*:

```
--floor 4   not bound 702, unreliable 260, bound 0      <- looks broken, is not
--floor 1   not bound 909, bound 27, unreliable 26
```

So set **Advanced → count floor = 1** in the block when uploading either shape. The two numbers are each
defensible and simply do not compose: the bed's background is calibrated to a real 5k BEAM-T library,
and the floor of 4 comes from the antibody-side lineage. A declared comparator would sidestep it, which
is exactly what neither of these shapes can supply.
