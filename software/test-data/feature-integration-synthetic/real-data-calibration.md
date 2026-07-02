# Real-data calibration — synthetic BEAM vs a real 5k BEAM-T reference

Provenance + empirical basis for the `realistic` generator profile. Gitignored (lives with the data).

## Source
- `/Users/paulnewling/Desktop/Data/5k_BEAM-T_Human_A0201_B0702_PBMC_5pv2_Multiplex_fastqs.tar` (12 GB, 10x public).
- **BEAM-T** (pMHC-multimer / TCR) — our synthetic is **BEAM-Ab** (BCR). Only *technical shapes* are
  borrowed (read geometry, barcode error, UMI depth/duplication, panel separation); the biology
  (cell types, antigen semantics, clone structure) is not.
- Measured 2026-07-01: one lane (6 M reads) per library, streamed; raw reads never persisted.

## Read geometry — confirms ours
R1 = **26 nt** (16 CB + 10 UMI), R2 = **90 nt**, all three libraries. (Our R1 matches; real R2 is 90 nt
but the block only reads the first 15 nt as the feature, so our 25 nt R2 is fine.)

## Antigen-capture shapes — measured vs synthetic
| Metric | Real BEAM-T | Default profile | **Realistic profile** (verified) |
|---|---|---|---|
| Dominant-feature UMIs/cell | median **632** (p10 18, p90 1490) | 8–30 | median **629** (18–1081) |
| Dominance fraction | median **1.00** (p10 0.79) | ~0.75 | median **0.99** (p10 0.70) |
| Background UMIs/cell | median **3** (p90 9) | higher | median **4** |
| PCR dup (reads/UMI) | median **1.3** (p90 2.0) | 1–4 | **1.30** |
| Reads/cell (antigen) | median **860** | ~60 | **~863** |
| Features detected/cell | median 3 (p90 4) | 3–4 | ~3–4 |

## Panel barcodes — ours are authentic
Top real R2 15-mers are the **same standard 10x Antigen-Capture barcodes we use**: `GATTGGCTACTCAAT`
(90.2%), `CGGCTCACCGCGTCT` (4.9%), `CTATCTACCGGCTCG` (1.3%) + `CATGTCTACGTTAAG` (1.1%, one we don't
have). Pairwise Hamming among panel barcodes = **min 8** (our `≥3` design floor is safe). ~1–2% of
reads are Hamming-1 variants of the dominant barcode (feature-barcode seq errors → refine-tags snaps back).

## Cross-library barcode overlap — validates the linking design
Top-5000 cell-barcode sets: **antigen∩gex 80%, antigen∩vdj 67%, gex∩vdj 69%, all-three 67%**. The same
16 nt barcode links the three libraries — and overlap is **partial** (~67–80%, not 100%), i.e. real
per-arm dropout. Our convergence inner-join is built for exactly this (cells missing from an arm drop).

## GEX depth — already on the right path
~900 distinct UMIs/cell/lane (≈1800 across both lanes). Our synthetic GEX totals ~1800 counts/cell —
**matches**. (Genes/cell needs alignment; literature ~1–3.5k for 5′ PBMC.) The realistic profile bumps
genes 341→~1000 for more realistic genes/cell; UMI depth is unchanged (it was correct).

## Cell-barcode error / correction structure
440k distinct raw barcodes / 6 M reads; top-5000 = **81.5%** of reads (≈ the 5k real cells). **50% of
distinct barcodes are singletons**; **~18.5% of reads are ambient/error** (not a real cell); **only
~14% of singleton junk is Hamming-1 of a real barcode** (→ correctable), the rest is ambient. So real
junk is *ambient-dominated*, not 1-bp-error-dominated.

## What was applied — the `realistic` profile (defaults untouched)
| Generator | Flag | Output | Change |
|---|---|---|---|
| `feature-integration-synthetic/generate.py` | `--profile realistic` | `realistic/` | UMI depth ↑, dup ↓, dominance ↑, background ↓ |
| `multiomics-run/generate_vdj.py` | `--realistic` | `vdj/realistic/` | reads realistic antigen consensus |
| `multiomics-run/generate_gex.py` | `--realistic` | `gex/realistic/` | 1000 genes (depth already matched) |
| `multiomics-run/validate_multiomics.py` | `--realistic` | — | validates the realistic chain (**38/38**) |

Build the realistic multiomics chain:
```bash
# antigen (in feature-integration-synthetic/)
python3 generate.py --profile realistic          # + optionally --scenario all
# arms (in multiomics-run/)
python3 generate_vdj.py --realistic && python3 generate_gex.py --realistic
python3 validate_multiomics.py --realistic        # 38/38
```

## Recommendations NOT auto-applied (future, if wanted)
- **Ambient/error scenario:** make the `errors` fixture ambient-dominated — add a heavy tail of random
  non-cell barcodes (~18% of reads), only ~14% of them Hamming-1 of a real cell. The current `errors`
  scenario over-weights clean 1-bp errors.
- **Cross-library dropout scenario:** drop ~15–30% of cells per arm so triple-overlap ≈ 67% (tests the
  inner-join drop). The default keeps 100% overlap for a clean, maximal join.
- **Dynamic range:** one antigen was 90% of the whole BEAM-T library. BEAM-Ab discovery is more even, so
  we keep a spread — but a "single-dominant-antigen" scenario would mirror BEAM-T.
- Optionally add `CATGTCTACGTTAAG` to widen the panel to 5 antigens + control.
