# Test Data

Data for testing the Feature Integration (FI) and VDJ Multiomic Integration (VDJM) blocks, split at the
top level by how it is tracked:

- **`fixtures/`** — tiny, committed fixtures consumed by the automated test suites. Never delete.
- **`manual/`** — synthetic beds for driving the blocks by hand. Only the recipe (one generator + its
  `lib/` modules + docs) is tracked; the generated data is gitignored and rebuilt on demand.

That split is structural, so "is this committed?" is answered by which top directory a file is in:
everything under `manual/` is gitignored except `*.py`, `*.md`, and the harvested
`manual/assets/whitelist_cells.txt`; everything under `fixtures/` is committed in full.

The blocks' own CI workflow fixtures live separately at the block root in `test/assets/`.

## What's Here

| Path | Purpose | Tracked | Used by |
|---|---|---|---|
| `fixtures/per-cell-metrics/` | Minimal bed: one mitool `tag-stat` TSV + a tag→feature CSV | **Fully tracked** (data included) | `software/conftest.py` → per-cell-metrics pytest |
| `manual/` | One generator (`generate.py` + `lib/`) that builds full colocated multiomic runs (antigen + VDJ + GEX arms, `runs/<preset>/`) and antigen-only scenarios (`runs/scenarios/`), plus the FI/VDJM block-and-settings guide | Recipe only | Manual FI + VDJM runs; full multiomic e2e |

`fixtures/per-cell-metrics/` is the only manual-looking bed whose data is committed — it is small and the
automated pytest depends on it, so it is a real test fixture, not manual scratch.

## Regenerating

```bash
python3 manual/generate.py realistic        # full multiomic run into manual/runs/realistic/ + validate
python3 manual/generate.py tiny             # small, fast version for a hand upload
python3 manual/generate.py --scenario errors   # an antigen-only scenario
```

Everything is stdlib-only and seeded, so a regenerate is reproducible. See `manual/README.md` for the
preset table, the per-block settings guide, the scenario catalog, and the tracking policy.
