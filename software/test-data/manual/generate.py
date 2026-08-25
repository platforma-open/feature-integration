#!/usr/bin/env python3
"""Single entry point for the manual BEAM-Ab test-data generators.

Builds a full, colocated multiomic run (antigen + VDJ + GEX arms) into one folder, or an antigen-only
behavioural scenario. Standard-library only, deterministic and seeded.

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

  runs/<preset>/                a full multiomic run -- one folder, no jumping between arms
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

from lib import annotations, antigen, beam_exact, gex, panelswap, realpanel, validate, vdj  # noqa: E402
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

# Antigen-only scenarios. errors/offpanel/multilane/control run through antigen.build. The rest have
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
    heavy_only=False,
    with_annotations=False,
    messy=False,
):
    """Build the requested arm(s) of a colocated multiomic run under run_dir."""
    pnl = panel_mod.build_panel(panel_size, offtarget_count=offtarget_count, multibarcode=multibarcode, messy=messy)
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
            messy=messy,
        )
        # After the antigen arm so its cell ids exist. Off by default (no annotations/ dir).
        if with_annotations:
            annotations.write_annotations(run_dir)
    if arm in ("all", "vdj"):
        vdj.build(
            tags_csv,
            consensus_tsv,
            out_dir=os.path.join(run_dir, "vdj"),
            truth_dir=os.path.join(run_dir, "truth"),
            heavy_only=heavy_only,
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
    ap.add_argument(
        "--heavy-only",
        action="store_true",
        help="emit a HEAVY-CHAIN-ONLY (IGH, no IGK) VDJ arm — the shape a VHH single-domain antibody "
        "library produces — so the heavy-only end-to-end path is reproducible; applies to the vdj and all "
        "arms. Each cell keeps its shared bare-16nt cell_id. Off by default (paired IGH+IGK)",
    )
    ap.add_argument(
        "--with-annotations",
        action="store_true",
        help="also emit a per-cell categorical annotation (annotations/donorNN.tsv: cell_id, cell_type, "
        "cluster) keyed by the shared bare-16nt cell barcode, biased by the planted antigen class. This "
        "feeds vdj-multiomic-integration's annotation-integration path (an alternative to GEX -> "
        "cell-type-annotation). Off by default (no annotations/ dir, byte-identical to prior runs)",
    )
    ap.add_argument(
        "--messy-metadata",
        action="store_true",
        help="inject the inconsistent casing/whitespace real panels carry into the EMITTED panel "
        "metadata: the Type column carries a mixed-case off-target set (both 'Off-Target' and "
        "'Off-target') and one antigen name gains a stray double space. Reproduces that problem so a "
        "mixed-case panel is available to exercise the block's case-sensitive off-target matching — the "
        "user must select each casing present (whitespace is trimmed, casing is not folded). Messy LABELS "
        "only — the barcode joins and truth tables stay coherent. Applies to the full-run panel; off by "
        "default (byte-identical to prior runs)",
    )
    ap.add_argument(
        "--real-panel",
        metavar="CSV",
        help="build a cohort-scale run against a REAL, externally-supplied wide panel CSV "
        "(sample / name / barcode / role columns) instead of a synthesized panel. The panel's own "
        "samples, antigen names and feature barcodes drive the run; every cell is planted at one of "
        "eight named reading tiers (strong -> noise) so good, medium and bad readings are all present "
        "in stated proportions. Writes into runs/real-panel/ by default. The panel file is COPIED into "
        "the run, and everything under runs/ is gitignored — a confidential panel can drive a run "
        "without any of it entering the repository",
    )
    ap.add_argument("--panel-sample-col", default=None, help="real-panel: the sample column (default: Samples)")
    ap.add_argument("--panel-name-col", default=None, help="real-panel: the antigen-name column (default: Name)")
    ap.add_argument("--panel-seq-col", default=None, help="real-panel: the barcode-sequence column (default: Sequence)")
    ap.add_argument("--panel-role-col", default=None, help="real-panel: the role column (default: Type)")
    ap.add_argument(
        "--target-roles",
        default=",".join(realpanel.DEFAULT_TARGET_ROLES),
        help="real-panel: comma-separated role prefixes meaning on-target (default: target)",
    )
    ap.add_argument(
        "--offtarget-roles",
        default=",".join(realpanel.DEFAULT_OFFTARGET_ROLES),
        help="real-panel: comma-separated role prefixes meaning off-target (default: off-target,offtarget,off target)",
    )
    ap.add_argument(
        "--offset",
        type=int,
        default=10,
        help="real-panel: bp of lead-in before the feature barcode in R2. 10 = the real BEAM geometry "
        "(default); 0 = feature at position 0",
    )
    ap.add_argument(
        "--library-quality",
        default="mixed",
        choices=list(realpanel.QUALITY_PROFILES),
        help="real-panel: how per-sample LIBRARY quality is dealt out — uniform (all clean), mixed "
        "(clean/good/fair/poor, default) or spread (forces an OK/WARN/ALERT span)",
    )
    ap.add_argument(
        "--clonal-profile",
        default=None,
        choices=["immunized", "lead"],
        help="real-panel: VDJ clone-size distribution — immunized (default: the shape an immunized, "
        "antigen-sorted repertoire has — an expanded head holding most of the CELLS plus a singleton "
        "tail) or lead (one clone holding 60%% of an antigen's cells, the small-fixture shape)",
    )
    ap.add_argument(
        "--clonal-mean-size",
        type=float,
        default=None,
        help="real-panel: mean cells per clone in the EXPANDED compartment (default 25). Lower it for a "
        "more diverse, less expanded repertoire; raise it for a few very large lead clones",
    )
    ap.add_argument(
        "--clonal-singleton-cell-frac",
        type=float,
        default=None,
        help="real-panel: share of a group's CELLS left as one-cell clonotypes (default 0.10). These stay "
        "a large share of clonotypes and a small share of cells, which is the real shape; raise it toward "
        "an unsorted/naive repertoire, lower it for a heavily sorted one",
    )
    ap.add_argument(
        "--regime",
        default="deep",
        choices=list(realpanel.REGIMES),
        help="real-panel: which MEASURED calibration to generate against. `deep` (default) is the "
        "public 10x BEAM shape — ~33 reads per UMI, ~200 antigen UMIs per called cell, near-mono "
        "dominance; it reproduces every run made before 2026-08-21 byte for byte. `shallow` is the "
        "shape real in-vivo BEAM libraries measure — 2.7-5.8 reads per UMI, a median of 7 UMIs "
        "across barcodes clearing the floor, dominance ~0.44, the raw barcode universe instead of "
        "called cells, and unfiltered antigen aggregates. Every flag below overrides the regime it "
        "came from",
    )
    ap.add_argument(
        "--ambient-barcode-ratio",
        type=float,
        default=None,
        help="real-panel: size the raw BARCODE UNIVERSE at this multiple of the real cell count "
        "(shallow default 100). The block applies no cell calling and the live configuration sets no "
        "whitelist, so what it reports as `cells detected` is this universe — 1.37M barcodes with a "
        "median of ONE UMI each. 0 keeps the old behaviour, where the ambient barcode count follows "
        "from the ambient read share alone",
    )
    ap.add_argument(
        "--aggregates",
        type=int,
        default=None,
        help="real-panel: number of ANTIGEN-AGGREGATE barcodes to plant (shallow default 5). Protein "
        "clumps produce droplets with enormous UMI counts; Cell Ranger removes them before cell "
        "calling and this block does not. 0 plants none",
    )
    ap.add_argument(
        "--aggregate-umi-share",
        type=float,
        default=None,
        help="real-panel: share of the finished library's UMIs the aggregates hold (shallow default "
        "0.59, measured). At that share aggregates outnumber real signal UMIs, which is why the "
        "measured per-cell depth is starved despite large libraries",
    )
    ap.add_argument(
        "--reads-per-umi",
        type=float,
        default=None,
        dest="dup_mean",
        help="real-panel: mean reads per distinct UMI. Drives FASTQ size and nothing the block "
        "concludes, since the reading rule counts UMIs. deep ~1.3, shallow 4.0 (measured 2.7-5.8)",
    )
    ap.add_argument(
        "--unpaired-frac",
        type=float,
        default=None,
        help="real-panel: share of cells emitting the HEAVY chain only (shallow default 0.35). In the "
        "measured libraries the clonotypes dropped for want of a pair outnumbered the paired ones",
    )
    ap.add_argument(
        "--baseline-tag",
        default=None,
        help="real-panel: name ONE tag (antigen name or 15 bp sequence) as the baseline the verdict "
        "simulation reads against. The block refuses a panel declaring several, and its panel rung needs "
        "at least 25 tags, so a small per-sample panel can otherwise reach no comparator at all and "
        "every reading comes back unreliable. Cells in samples that do not offer the named tag still "
        "have no comparator — the baseline is global by tag, the panel is per sample",
    )
    ap.add_argument(
        "--panel-shape",
        default="auto",
        choices=["auto", "wide", "narrow"],
        help="real-panel: wide declares a role column; narrow declares none (sample / antigen / "
        "sequence) and role is inferred from the antigen NAME. Both shapes are live in production "
        "on different projects. Default auto-detects from the header",
    )
    ap.add_argument(
        "--control-feature",
        default=None,
        help="real-panel: name one member as the comparator, whatever the panel says. Mirrors the "
        "block, where a user picks a control from a dropdown of antigen names — the only route a "
        "narrow panel has to a declared comparator",
    )
    ap.add_argument(
        "--barcode-source",
        default=None,
        choices=["whitelist737k", "random"],
        help="real-panel: cell-barcode source (default: whitelist737k, so the 737K cell whitelist "
        "setting is usable and cellIds match a real VDJ producer)",
    )
    ap.add_argument("--out", help="override the output directory")
    args = ap.parse_args()

    if args.real_panel:
        run_dir = args.out or os.path.join(RUNS_DIR, "real-panel")
        cells = args.cells_per_sample or 6000
        columns = {
            k: v
            for k, v in (
                ("sample", args.panel_sample_col),
                ("name", args.panel_name_col),
                ("sequence", args.panel_seq_col),
                ("role", args.panel_role_col),
            )
            if v
        }
        roles = tuple(r.strip().lower() for r in args.target_roles.split(",") if r.strip())
        off_roles = tuple(r.strip().lower() for r in args.offtarget_roles.split(",") if r.strip())
        print(f"=== real panel: {args.real_panel} ({cells} cells/sample) -> {run_dir} ===")
        if args.validate_only:
            sys.exit(0 if realpanel.validate(run_dir, columns=columns, regime=args.regime,
                                         baseline_tag=args.baseline_tag, target_roles=roles,
                                         offtarget_roles=off_roles) else 1)
        info = realpanel.build(
            run_dir,
            args.real_panel,
            cells_per_sample=cells,
            barcode_source=args.barcode_source or "whitelist737k",
            assets_dir=ASSETS_DIR,
            columns=columns,
            target_roles=roles,
            offtarget_roles=off_roles,
            offset=args.offset,
            quality_profile=args.library_quality,
            arm=args.arm,
            regime=args.regime,
            clonal_profile=args.clonal_profile,
            clonal_mean_size=args.clonal_mean_size,
            clonal_singleton_cell_frac=args.clonal_singleton_cell_frac,
            ambient_barcode_ratio=args.ambient_barcode_ratio,
            aggregates=args.aggregates,
            aggregate_umi_share=args.aggregate_umi_share,
            dup_mean=args.dup_mean,
            unpaired_frac=args.unpaired_frac,
            panel_shape=args.panel_shape,
            control_feature=args.control_feature,
        )
        # A V(D)J-only rebuild leaves the reads, the panel and the tiers exactly as they were, so the
        # report still describes the run. Rewriting it from a partial build would only replace its read
        # count with a zero.
        if args.arm == "all":
            realpanel.write_run_report(run_dir, info, args.real_panel, args.library_quality,
                                       baseline_tag=args.baseline_tag)
            print(f"  settings -> {os.path.join(run_dir, 'RUN.md')}")
        if not args.no_validate:
            sample_check = info["samples"][0] if info["samples"] else None
            if not realpanel.validate(run_dir, columns=columns, sample_check=sample_check,
                                      regime=args.regime, baseline_tag=args.baseline_tag,
                                      target_roles=roles, offtarget_roles=off_roles):
                sys.exit(1)
        return

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
        # scenarios default to a small scale for hand inspection. --samples and friends override it.
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
        heavy_only=args.heavy_only,
        with_annotations=args.with_annotations,
        messy=args.messy_metadata,
    )
    print(
        f"\npanel: {panel_size} antigens + 1 control | samples: {samples} | cells/sample: {cells} "
        f"| preset: {args.preset}"
    )


if __name__ == "__main__":
    main()
