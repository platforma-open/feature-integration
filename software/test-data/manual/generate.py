#!/usr/bin/env python3
"""Single entry point for the manual BEAM-Ab test-data generators.

Builds a full, colocated multiomic run (antigen + VDJ + GEX arms) into one folder, or an antigen-only
behavioural scenario. Standard-library only, deterministic (seeded).

  # A full multiomic run (all three arms + shared panel/metadata + truth), then validate it offline:
  python3 generate.py realistic            # 24 donors x 2000 cells x 15-antigen panel + control
  python3 generate.py tiny                 # 2 donors x 80 cells x 4 antigens (fast hand upload)
  python3 generate.py whitelist737k        # realistic + real 737K-compliant cell barcodes

  # One arm only (the others must already exist under the run dir):
  python3 generate.py realistic --arm antigen

  # An antigen-only scenario (written under runs/scenarios/<name>/):
  python3 generate.py --scenario errors
  python3 generate.py --scenario panel-swap
  python3 generate.py --scenario multisample

Output layout (everything under runs/ is gitignored):

  runs/<preset>/                a full multiomic run — one folder, no jumping between arms
    antigen/  donorNN_R{1,2}.fastq.gz
    vdj/      donorNN.tsv
    gex/      donorNN.csv
    tags.csv  feature_reference.csv  samples-metadata.tsv    (the block uploads)
    truth/    expected-*.tsv  truth_clonotypes.csv  truth_cells_gex.csv
  runs/scenarios/<name>/        antigen-only behavioural beds (self-contained)
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import antigen, beam_exact, gex, panelswap, validate, vdj  # noqa: E402
from lib import panel as panel_mod  # noqa: E402
from lib.antigen import AntigenConfig  # noqa: E402

ASSETS_DIR = os.path.join(HERE, "assets")
ANNOT_CSV = os.path.join(ASSETS_DIR, "homo_sapiens_gene_annotations.csv")
RUNS_DIR = os.path.join(HERE, "runs")

# preset -> default scale/calibration/barcode-source (all overridable on the CLI).
PRESETS = {
    "tiny": dict(samples=2, cells=80, panel_size=4, barcode_source="random"),
    "realistic": dict(samples=24, cells=2000, panel_size=15, barcode_source="random"),
    "whitelist737k": dict(samples=24, cells=2000, panel_size=15, barcode_source="whitelist737k"),
}

# Antigen-only scenarios. errors/offpanel/multilane/control run through antigen.build; the rest have
# their own generators. Default scenario scale = small (tiny), overridable with --samples/etc.
ANTIGEN_SCENARIOS = ["errors", "offpanel", "multilane", "control"]
SPECIAL_SCENARIOS = ["degraded", "panel-swap", "multisample", "libraseq"]
ALL_SCENARIOS = ANTIGEN_SCENARIOS + SPECIAL_SCENARIOS


def sample_names(n):
    if n < 1:
        raise SystemExit("--samples must be >= 1")
    return [f"donor{i + 1:02d}" for i in range(n)]


def build_full_run(
    run_dir,
    samples,
    cells,
    panel_size,
    barcode_source,
    arm,
    do_validate,
    offtarget_count=0,
    crossreactive_frac=0.0,
    multibarcode=False,
):
    """Build the requested arm(s) of a colocated multiomic run under run_dir."""
    pnl = panel_mod.build_panel(panel_size, offtarget_count=offtarget_count, multibarcode=multibarcode)
    tags_csv = os.path.join(run_dir, "tags.csv")
    consensus_tsv = os.path.join(run_dir, "truth", "expected-consensus.tsv")

    if arm in ("all", "antigen"):
        cfg = AntigenConfig(
            samples=sample_names(samples), cells_per_sample=cells, barcode_source=barcode_source, assets_dir=ASSETS_DIR
        )
        antigen.build(
            cfg,
            pnl,
            "baseline",
            fastq_dir=os.path.join(run_dir, "antigen"),
            shared_dir=run_dir,
            truth_dir=os.path.join(run_dir, "truth"),
            crossreactive_frac=crossreactive_frac,
            multibarcode=multibarcode,
        )
    if arm in ("all", "vdj"):
        vdj.build(
            tags_csv, consensus_tsv, out_dir=os.path.join(run_dir, "vdj"), truth_dir=os.path.join(run_dir, "truth")
        )
    if arm in ("all", "gex"):
        gex.build(
            tags_csv,
            consensus_tsv,
            out_dir=os.path.join(run_dir, "gex"),
            truth_dir=os.path.join(run_dir, "truth"),
            annot_csv=ANNOT_CSV,
        )

    if do_validate and arm == "all":
        print()
        if not validate.validate(run_dir):
            sys.exit(1)


def build_scenario(
    name, out_dir, samples, cells, panel_size, barcode_source, offtarget_count=0, crossreactive_frac=0.0
):
    """Build one antigen-only scenario bed into out_dir (self-contained: FASTQs + tags.csv + truth)."""
    if name == "panel-swap":
        panelswap.build_panel_swap(out_dir)
        return
    if name == "multisample":
        panelswap.build_multisample(out_dir)
        return
    cfg = AntigenConfig(
        samples=sample_names(samples), cells_per_sample=cells, barcode_source=barcode_source, assets_dir=ASSETS_DIR
    )
    if name == "libraseq":
        antigen.build_libraseq(cfg, out_dir)
        return
    pnl = panel_mod.build_panel(panel_size, offtarget_count=offtarget_count)
    if name == "degraded":
        antigen.build_degraded(cfg, pnl, out_dir)
    else:
        antigen.build(
            cfg,
            pnl,
            name,
            fastq_dir=out_dir,
            shared_dir=out_dir,
            truth_dir=out_dir,
            crossreactive_frac=crossreactive_frac,
        )


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument(
        "preset",
        nargs="?",
        default="realistic",
        choices=list(PRESETS),
        help="which full-run preset to build (default: realistic)",
    )
    ap.add_argument(
        "--arm",
        default="all",
        choices=["all", "antigen", "vdj", "gex"],
        help="build only one arm of the run (default: all)",
    )
    ap.add_argument(
        "--scenario",
        choices=ALL_SCENARIOS,
        help="build an antigen-only scenario into runs/scenarios/<name>/ instead of a full run",
    )
    ap.add_argument("--no-validate", action="store_true", help="skip the offline validator after a full run")
    ap.add_argument(
        "--validate-only",
        action="store_true",
        help="run the offline validator against an existing run and exit (no regeneration)",
    )
    ap.add_argument(
        "--beam",
        action="store_true",
        help="build the BEAM-exact fixture (offset-10 R2 + sample-aware panel) into runs/beam-exact/",
    )
    ap.add_argument("--samples", type=int, help="override donor count")
    ap.add_argument("--cells-per-sample", type=int, help="override cells per donor")
    ap.add_argument("--panel-size", type=int, help="override antigen count (excl. control)")
    ap.add_argument(
        "--offtarget-count",
        type=int,
        default=0,
        help="mark the first N non-control antigens as Off-Target in the panel Type column "
        "(rest -> Target, control -> Decoy)",
    )
    ap.add_argument(
        "--crossreactive-frac",
        type=float,
        default=0.0,
        help="fraction of binder cells planted to bind TWO on-target antigens co-dominantly (~equal "
        "UMIs); these are labeled 'crossreactive' in the truth so the block's cross-reactive call is "
        "testable (default 0.0 -> none, byte-identical to prior runs)",
    )
    ap.add_argument(
        "--multibarcode",
        action="store_true",
        help="map some antigens to MULTIPLE feature barcodes with a per-antigen combine mode "
        "(first antigen -> combine=all, second -> combine=sum, rest single-barcode sum); tags.csv "
        "gains a `combine` column and feature_reference.csv per-member ids (<feat>_1/<feat>_2) so the "
        "FI multi-barcode combine logic is testable in a full run. Off by default (byte-identical)",
    )
    ap.add_argument("--out", help="override the output directory")
    args = ap.parse_args()

    if args.beam:
        run_dir = args.out or os.path.join(RUNS_DIR, "beam-exact")
        cells = args.cells_per_sample or 150
        panel_size = args.panel_size or 12
        print(f"=== beam-exact (2 samples x {cells} cells x {panel_size} antigens, offset-10) -> {run_dir} ===")
        beam_exact.build(
            run_dir,
            cells_per_sample=cells,
            panel_size=panel_size,
            offtarget_count=args.offtarget_count,
            multibarcode=args.multibarcode,
        )
        return

    if args.validate_only:
        run_dir = args.out or os.path.join(RUNS_DIR, args.preset)
        sys.exit(0 if validate.validate(run_dir) else 1)

    if args.scenario:
        # scenarios default to a small scale for hand inspection; --samples/etc override
        samples = args.samples or 2
        cells = args.cells_per_sample or 80
        panel_size = args.panel_size or 4
        out_dir = args.out or os.path.join(RUNS_DIR, "scenarios", args.scenario)
        print(f"=== scenario: {args.scenario} -> {out_dir} ===")
        build_scenario(
            args.scenario,
            out_dir,
            samples,
            cells,
            panel_size,
            "random",
            offtarget_count=args.offtarget_count,
            crossreactive_frac=args.crossreactive_frac,
        )
        return

    p = PRESETS[args.preset]
    samples = args.samples or p["samples"]
    cells = args.cells_per_sample or p["cells"]
    panel_size = args.panel_size or p["panel_size"]
    run_dir = args.out or os.path.join(RUNS_DIR, args.preset)
    print(
        f"=== preset: {args.preset} ({samples} donors x {cells} cells x {panel_size} antigens+control) -> {run_dir} ==="
    )
    build_full_run(
        run_dir,
        samples,
        cells,
        panel_size,
        p["barcode_source"],
        args.arm,
        do_validate=not args.no_validate,
        offtarget_count=args.offtarget_count,
        crossreactive_frac=args.crossreactive_frac,
        multibarcode=args.multibarcode,
    )
    print(
        f"\npanel: {panel_size} antigens + 1 control | samples: {samples} | cells/sample: {cells} "
        f"| preset: {args.preset}"
    )


if __name__ == "__main__":
    main()
