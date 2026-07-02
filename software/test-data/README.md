# Test Data

Data for testing the Feature Integration block. Two kinds live here, with different tracking rules:

- **Automated-test fixtures** — tiny, committed, consumed by the test suites. Never delete.
- **Manual-run datasets** — larger synthetic data for driving the block by hand. Only the recipe
  (generators + docs) is tracked; the generated data is gitignored and regenerated on demand.

The block's own CI workflow fixtures live separately at the block root in `test/assets/`
(`fb_small_R{1,2}.fastq.gz` + `tags.csv`, used by `test/src/wf.test.ts`).

## What's Here

| Directory | Purpose | Tracked | Used by |
|---|---|---|---|
| `feature-synthetic/` | Minimal bed: one mitool `tag-stat` TSV + a tag→feature CSV | **Fully tracked** (data included) | `software/conftest.py` → per-cell-metrics pytest |
| `feature-integration-synthetic/` | Canonical FI-standalone manual bed: realistic 10x BEAM-Ab antigen reads (2 donors), 3 profiles + scenarios | Recipe only | Manual runs; antigen arm of `multiomics-run` |
| `multiomics-run/` | Full BEAM-Ab multiomics manual e2e: GEX + VDJ arms that join the antigen arm above | Recipe only | Manual 3-block e2e |
| `manual-run/` | Superseded antigen-only bed; kept for its panel-swap and multi-sample fixtures | Recipe only | Manual panel-merge exploration |

`feature-synthetic/` is the only manual-looking directory whose data is committed — it is small and
the automated pytest depends on it, so it is a real test fixture, not manual scratch.

## Tracking Policy

For `manual-run/`, `feature-integration-synthetic/`, and `multiomics-run/`, git tracks the
**reproducible recipe** and ignores the generated data:

- **Tracked:** generators (`*.py`), docs (`*.md`), and any non-regenerable harvested input
  (`feature-integration-synthetic/whitelist_cells.txt`, real 737K cell barcodes pulled from a real
  BEAM-T dataset).
- **Gitignored:** everything the generators produce — FASTQs, count/AIRR/expected/truth tables, the
  tag and panel CSVs, and profile/scenario subdirectories.

Regenerate any dataset with its directory's generator (each has a README with the exact command and
what it models). One asset is fetched, not generated: `multiomics-run/gex/homo_sapiens_gene_annotations.csv`
(~21 MB) — `generate_gex.py` prints a `curl` command to fetch it if missing.

## Regenerating

```bash
# antigen arm (default / realistic / whitelist737k profiles — see its README)
cd feature-integration-synthetic && python3 generate.py --profile realistic

# GEX + VDJ arms for the full multiomics run (build on the matching antigen profile)
cd multiomics-run && python3 generate_gex.py --profile realistic && python3 generate_vdj.py --profile realistic
python3 multiomics-run/validate_multiomics.py --profile realistic
```

All generators are stdlib-only and seeded, so a regenerate is reproducible.
