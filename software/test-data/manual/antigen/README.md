# Synthetic BEAM-Ab data for manually testing the Feature Integration block

Realistic synthetic **antigen-capture (feature-barcode)** reads for driving the `feature-integration`
block by hand — standalone (this doc) or as the antigen arm of the full multiomics run
(`../multiomics/README.md`). Gitignored; regenerate with `python3 generate.py` (stdlib only, seeded →
reproducible). This is the **canonical** FI-standalone test bed (it supersedes the older `../panel-swap/`,
whose panel-swap fixtures are still referenced below).

## What it models

10x **BEAM-Ab** (Barcode Enabled Antigen Mapping, B cells), 10x 5′ v2 geometry. Each cell has one dominant
antigen plus low ambient signal; ~12% are deliberately ambiguous (two antigens tied) to exercise the
consensus rule. Every molecule is 1–4 PCR-duplicate reads (realistic profile: median ~1.3), so `count` >
`unique_UMI` and the `tag-stat -u` dedup does something.

### Scale (parameterized)

Scale is set by CLI flags so the fixture can range from a toy bed to a cohort-scale run. Defaults target a
realistic multi-donor BEAM cohort:

| flag | default | meaning | corroboration |
|---|---|---|---|
| `--samples N` | `24` | donor samples (`donor01`…`donorNN`) | verified cohort high-water ~22–50 donors |
| `--panel-size M` | `64` | antigens, **excluding** the control | verified BEAM feature ceiling = 64 (BEAM-proper alone is ~6) |
| `--cells-per-sample K` | `2000` | cells per donor | a real GEM well is 2k–10k cells |

At the defaults that is ~48k cells and (realistic profile) ~42M read pairs → **~870 MB gzipped FASTQ**
(~1 GB for the full realistic multiomics chain incl. VDJ+GEX); generation measured **~6 min** end to end
(machine-dependent). Dial any flag down for a quick bed (e.g. `--samples 2 --panel-size 6
--cells-per-sample 50`). See `real-data-calibration.md` for the per-cell shapes.

### Antigen panel

The first up-to-4 barcodes are the **real** 10x BEAM-Ab panel from the public *"2k transgenic HEL mouse
splenocytes (BEAM-Ab)"* dataset; the rest are synthesized as distinct 15-mers (pairwise Hamming ≥ 3 from
each other and the anchors + control) so the panel scales to `--panel-size` while keeping authentic
anchors. The negative control is always the real `negative_control` barcode.

| antigen | feature barcode (15 bp) | source |
|---|---|---|
| SARS-TRI-S_WT | `CGATGCCGGACGATC` | real 10x anchor |
| Anti-Hen_Egg_Lysozyme | `CCGTCTCACCGATAT` | real 10x anchor |
| gp120 | `GATTGGCTACTCAAT` | real 10x anchor |
| H5N1 | `CGGCTCACCGCGTCT` | real 10x anchor |
| `antigen_005` … `antigen_NNN` | synthetic (deterministic) | fills the panel to `--panel-size` |
| negative_control | `CTATCTACCGGCTCG` | real 10x anchor |

**Profiles:** `default` is simple/hand-verifiable; `--profile realistic` (→ `realistic/`) is calibrated to
a real 5k BEAM-T dataset (`real-data-calibration.md`) and is what the multiomics run uses (random
barcodes → run with cell whitelist = None); `--profile whitelist737k` (→ `whitelist737k/`) uses **real
`737K-august-2016`-compliant cell barcodes** (sampled from the full 10x inclusion list `737K-august-2016.txt`,
fetched on demand; falls back to the harvested `whitelist_cells.txt`) + a realistic ambient off-list tail,
so the **cell-barcode whitelist knob** can be exercised (run with cell whitelist = `737K-august-2016`). All
profiles share the same panel + dominant-antigen logic.

## Read geometry (matches the block defaults exactly)

10x 5′ v2, confirmed against the 10x BEAM docs (read = R2, barcode at R2 position 0):

```
R1 (26 bp) = CELL(16) + UMI(10)
R2 (25 bp) = feature barcode(15) + filler   ← block reads the first 15 bp as the feature; rest ignored
```

This is exactly what `workflow/src/tag-pattern.lib.tengo` builds:
`^(CELL:N{16})(UMI:N{10})\^(FEATURE:N{15})(R2:*)` with `cellLen=16, umiLen=10, featureLen=15`.
(The `FEATURE` tag is mitool's first-class feature tag type, PR milaboratory/mitool#86.)

## Files

| file | what it is |
|---|---|
| `donor01_R{1,2}.fastq.gz` … `donorNN_R{1,2}.fastq.gz` | paired antigen-capture reads, one pair per donor (`--samples` of them) |
| `tags.csv` | **the block's required upload** — `tag,feature` (feature barcode → antigen name). This *is* the feature/antigen panel whitelist (`--panel-size` antigens + control). |
| `feature_reference.csv` | the real 10x BEAM-Ab format; `tags.csv` is its `sequence→name` projection |
| `samples-metadata.tsv` | sample metadata (`Sample, Donor, Condition`) for Samples & Data — one row per donor, alternating `baseline`/`stimulated` |
| `expected-abundance.tsv` | ground truth: planted distinct-UMI per `(sample, cellId, feature)` |
| `expected-consensus.tsv` | ground truth: planted dominant antigen per `(sample, cellId)` |
| `example-mitool-tagstat-donorA.tsv` | a real `tag-stat -u` output (what the block's Python consumes) |
| `realistic/` | the `--profile realistic` variant (used by the multiomics run) |
| `whitelist737k/` | the `--profile whitelist737k` variant: real 737K cell barcodes + ambient tail (exercises the cell whitelist) |
| `737K-august-2016.txt` | the full 10x `737K-august-2016` inclusion list (~737k barcodes), **fetched on demand, gitignored** — the whitelist737k cell pool at scale |
| `whitelist_cells.txt` | ~800 real `737K-august-2016` cell barcodes harvested from real BEAM-T — a committed fallback pool (used only if the full list above is absent) |
| `scenarios/{errors,multilane,offpanel,control}/` | edge-case datasets (see below); `control/` also has `expected-specificity.tsv` |
| `generate.py` | the generator (seeded; scale via `--samples`/`--panel-size`/`--cells-per-sample`) |

> Ground truth is the *planted* values; the block's live output may differ slightly because `refine-tags`
> error-corrects barcodes. For this data the barcodes are well-separated so correction is ~0.

## Validated through the real mitool chain

The block's per-sample pipeline (`parse → emit-panel → refine-tags → tag-stat -u`) reproduced offline:

```bash
JAR=<path to published mitool.jar, e.g. .cache/backend/.../mitool/2.3.1-131-main.*/mitool.jar>
mkdir -p /tmp/fi-dryrun && cd /tmp/fi-dryrun
cp <thisdir>/donor01_R1.fastq.gz input_R1.fastq.gz && cp <thisdir>/donor01_R2.fastq.gz input_R2.fastq.gz
# panel.txt = the tag column of tags.csv (the feature whitelist)
tail -n +2 <thisdir>/tags.csv | cut -d, -f1 > panel.txt
java -jar "$JAR" parse --pattern '^(CELL:N{16})(UMI:N{10})\^(FEATURE:N{15})(R2:*)' --threads 4 'input_{{R}}.fastq.gz' parsed.mic
java -jar "$JAR" refine-tags -t CELL -t 'FEATURE#file:panel.txt' -t UMI parsed.mic refined.mic
java -jar "$JAR" tag-stat -t CELL -t FEATURE -u UMI refined.mic tagstat.tsv
# tagstat.tsv columns: CELL FEATURE count totalWeight unique_UMI  — feed FEATURE→antigen via tags.csv
```

## Edge-case scenarios (`--scenario`)

`python3 generate.py --scenario <name>` (or `all`). Each is self-contained (own `tags.csv`, FASTQs,
metadata, ground truth) and validated through the real mitool chain.

| scenario | tests | expected | via mitool CLI |
|---|---|---|---|
| `errors` | 1 bp errors in ~15% of cell/feature barcodes | see note below | de-novo `refine-tags` corrected **0** |
| `offpanel` | feature barcodes not in `tags.csv` + malformed reads | off-panel features dropped by the inner-join; malformed gone | off-panel kept by mitool, dropped by join; 99.4% parse match |
| `multilane` | two lanes/sample (`fb-pipeline` `keyLength==2` branch) | per-cell totals == single-lane | lane-merge totals == planted ✓ |
| `control` | binders + a ~30% **true non-binder** population (all antigens at control level) | **with the negative control set:** binders → high `specificityScore` (~100) on their dominant antigen; non-binders → ~0 everywhere | block's exact spec formula on planted UMIs: binder median **100**, non-binder median **0** ✓ (ground truth: `expected-specificity.tsv`) |

## Cell-barcode correction — de-novo default, optional 10x whitelist

The **feature/antigen** barcode is always corrected against the panel whitelist (the `tag` column of your
CSV, `refine-tags -t FEATURE#file:panel.txt`): within-Hamming-1 reads snap to a panel barcode, off-panel
reads drop.

The **cell** barcode has two modes (Advanced → "Cell barcode whitelist (10x)"):
- **`None — de-novo`** (default) — clusters observed barcodes; does **not** snap 1 bp errors to a
  reference. In the `errors` scenario this leaves phantom low-count cells (~ the injected error rate ×
  reads), so `scenarios/errors/expected-abundance.tsv` is the *ideal*; the de-novo output shows extras.
- **A 10x built-in** (e.g. `737K-august-2016`) — snaps cells to that real 10x list; off-list barcodes
  drop. This makes cellIds match the VDJ producer by construction (see
  `../../../../docs/dormant-features/cell-whitelist-correction-plan.md`; smoke-tested on real BEAM-T: ~9% off-list tail dropped,
  ~98% of records kept). The `default`/`realistic` fixtures use **random** barcodes → keep this **`None`**
  for them (a whitelist would drop every cell). To exercise the whitelist, use the **`whitelist737k`**
  profile (real 737K barcodes + ambient tail) with whitelist = **`737K-august-2016`** — it keeps the
  `--cells-per-sample` real cells/donor and drops the ~15% ambient off-list tail (de-novo instead keeps
  the ambient as phantom low-count cells).

## Running it in the app (Samples & Data → Feature Integration)

1. Start a backend + build the block (`pnpm build:dev`); the block now uses **published**
   `software-mitool 2.3.1-131-main`, so the in-app mitool step runs on a prebuilt backend (no more #84
   local-override hang).
2. Add **Samples & Data**. Create one sample per donor (`donor01`…`donorNN`, `--samples` of them); add a
   **Fastq** dataset (paired R1/R2, gzipped) and upload each donor's R1/R2. Optionally import
   `samples-metadata.tsv`. (For a hand test use a small `--samples`; the 24-donor default is a lot to
   upload manually.)
3. Add **Feature Integration** → Settings:
   - **Feature-barcode FASTQ** → the dataset from step 2.
   - **Tag → feature CSV** → upload `tags.csv`.
   - **Negative-control feature** → pick `negative_control` (dropdown is now populated from the CSV) to
     add `specificityScore`; leave blank to skip it. For a *discriminating* specificity test (binders
     ~100 vs true non-binders ~0), use the **`control` scenario** dataset — the baseline/realistic cells
     are all binders, so they only exercise on-target-high / off-target-low within each cell, not a true
     non-binder population. Generate it with `python3 generate.py --profile realistic --scenario control`
     (or `--scenario control` for the default profile); check the block's `specificityScore` output
     against `scenarios/control/expected-specificity.tsv` (`cellClass` binder vs nonbinder).
   - Advanced: dominance `0.6`; geometry `16/10/15`; **Cell barcode whitelist = `None — de-novo`**.
4. **Run** → per-cell results table (umiCount / fraction / consensus [+ specificityScore if control set]).
   Spot-check `consensusFeature` against `expected-consensus.tsv`.

### Exploration matrix (panel-swap effects)

Re-upload a different panel CSV and re-run to see whitelist/merge behavior. The older `../panel-swap/panels/`
fixtures are handy here (7/6/3-feature synthetic panels):

| Panel | What it demonstrates |
|---|---|
| `../panel-swap/panels/panel_full.csv` (7 features) | baseline; all features present |
| `../panel-swap/panels/panel_merged.csv` (6) | two barcodes → one feature name (mitool sums them) |
| `../panel-swap/panels/panel_core.csv` (3) | whitelist filtering: dropped antigens' reads vanish → fewer/emptier cells |

Multi-sample behavior (the per-sample axis the next block aggregates on) is exercised directly by the two
donors here, or by `../panel-swap/multisample/` (two samples with different dominant-antigen mixes).

## Tuning

Scale via CLI: `--samples`, `--panel-size`, `--cells-per-sample` (see the Scale table above). Everything
else is constants near the top of `generate.py`: `SEED`, geometry lengths, the real-anchor panel,
per-kind UMI ranges, error/junk rates, `GZIP_LEVEL`. Change and re-run (seeded → reproducible).

## Sources

- [Analyzing BEAM-Ab with Cell Ranger multi](https://www.10xgenomics.com/support/software/cell-ranger/latest/tutorials/cr-tutorial-multi-beam-ab) — feature reference format + the real panel
- [What is Antigen Capture?](https://www.10xgenomics.com/support/software/cell-ranger/latest/getting-started/cr-5p-what-is-antigen-capture) — read = R2, pattern `^(BC)`
- [2k transgenic HEL mouse splenocytes (BEAM-Ab)](https://www.10xgenomics.com/datasets/2k-transgenic-hel-mouse-splenocytes-beam-ab-2-standard) — source dataset for the panel
