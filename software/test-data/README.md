# Test Data

Data for testing the Feature Integration block, split at the top level by how it is tracked:

- **`fixtures/`** — tiny, committed fixtures consumed by the automated test suites. Never delete.
- **`manual/`** — larger synthetic beds for driving the block by hand. Only the recipe
  (generators + docs) is tracked; the generated data is gitignored and regenerated on demand.

That split is structural, so "is this committed?" is answered by which top directory a file is in:
everything under `manual/` is gitignored except `*.py`, `*.md`, and the harvested
`manual/antigen/whitelist_cells.txt`; everything under `fixtures/` is committed in full.

The block's own CI workflow fixtures live separately at the block root in `test/assets/`
(`fb_small_R{1,2}.fastq.gz` + `tags.csv`, used by `test/src/wf.test.ts`).

## What's Here

| Path | Purpose | Tracked | Used by |
|---|---|---|---|
| `fixtures/per-cell-metrics/` | Minimal bed: one mitool `tag-stat` TSV + a tag→feature CSV | **Fully tracked** (data included) | `software/conftest.py` → per-cell-metrics pytest |
| `manual/antigen/` | Canonical FI-standalone bed: realistic 10x BEAM-Ab antigen reads at parameterized scale (default 24 donors × 2000 cells × 64-antigen panel + control), 3 profiles + scenarios | Recipe only | Manual runs; antigen arm of `manual/multiomics/` |
| `manual/multiomics/` | Full BEAM-Ab multiomics manual e2e: GEX + VDJ arms that join the antigen arm above | Recipe only | Manual 3-block e2e |
| `manual/panel-swap/` | Superseded antigen-only bed (was `manual-run/`); kept for its panel-swap and multi-sample fixtures | Recipe only | Manual panel-merge exploration |

`fixtures/per-cell-metrics/` is the only manual-looking bed whose data is committed — it is small and
the automated pytest depends on it, so it is a real test fixture, not manual scratch.

## Tracking Policy

For everything under `manual/`, git tracks the **reproducible recipe** and ignores the generated data:

- **Tracked:** generators (`*.py`), docs (`*.md`), and any non-regenerable harvested input
  (`manual/antigen/whitelist_cells.txt`, real 737K cell barcodes pulled from a real BEAM-T dataset).
- **Gitignored:** everything the generators produce — FASTQs, count/AIRR/expected/truth tables, the
  tag and panel CSVs, and profile/scenario subdirectories.

Regenerate any dataset with its directory's generator (each has a README with the exact command and
what it models). Two assets are fetched, not generated (both gitignored, with a `curl` hint printed if
missing): `manual/multiomics/gex/homo_sapiens_gene_annotations.csv` (~21 MB, human gene map) and
`manual/antigen/737K-august-2016.txt` (~12 MB, the full 10x cell-barcode inclusion list — the
`whitelist737k` cell pool at scale).

## Regenerating

Run from this `test-data/` directory (each generator resolves paths relative to its own location, so
the working directory does not matter):

```bash
# antigen arm (default / realistic / whitelist737k profiles — see its README)
python3 manual/antigen/generate.py --profile realistic

# GEX + VDJ arms for the full multiomics run (build on the matching antigen profile)
python3 manual/multiomics/generate_gex.py --profile realistic
python3 manual/multiomics/generate_vdj.py --profile realistic
python3 manual/multiomics/validate_multiomics.py --profile realistic
```

All generators are stdlib-only and seeded, so a regenerate is reproducible.
