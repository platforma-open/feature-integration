# panel-swap (was manual-run) — superseded

The canonical Feature Integration standalone test bed is now
**`../antigen/`** (real 10x BEAM-Ab panel, `errors`/`multilane`/`offpanel`
scenarios, a `realistic` profile calibrated to real BEAM-T data, and ground truth). Use its README.

This directory is kept only for the **panel-swap exploration** fixtures it still provides, which the
canonical README references:
- `panels/panel_full.csv` (7 features), `panel_merged.csv` (6, two barcodes → one feature),
  `panel_core.csv` (3, whitelist-filtering demo)
- `multisample/` (two samples with different dominant-antigen mixes)

Regenerate with `python3 generate.py` (stdlib, seeded). Data is gitignored.

> Note: the old caveats here are obsolete — the negative-control dropdown is now populated from the CSV
> (pick the control by clicking, no programmatic workaround), an **optional 10x cell-barcode whitelist**
> exists (Advanced Settings; keep it `None` for synthetic data), and the block uses **published** mitool
> (no local-override in-app hang). See the canonical README for current settings.
