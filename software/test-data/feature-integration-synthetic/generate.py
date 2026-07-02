#!/usr/bin/env python3
"""Generate realistic synthetic feature-barcode (antigen-capture) FASTQs for MANUAL testing of the
Feature Integration block. Standard library only (no numpy). Deterministic (seeded).

Geometry matches the block defaults (10x 5' v2):
  R1 = CELL(16) + UMI(10)          -> 26 bp
  R2 = feature barcode(15) + filler -> 25 bp   (block reads first 15 bp as CELLFB; rest is R2:* ignored)

Antigen panel is the REAL 10x BEAM-Ab panel from the public "2k transgenic HEL mouse splenocytes"
example dataset (4 antigens + 1 negative control).

Scenarios (--scenario), each a self-contained dataset targeting ONE untested behavior. The consensus
and specificity *math* is already covered by the Python unit tests (test_per_cell_metrics.py); these
fixtures exist to exercise the mitool + integration layer that the unit tests cannot reach:

  baseline   happy path: clean barcodes, single lane, on-panel only. Written to this directory.
  errors     ~15% of reads carry a 1 bp error in the cell OR feature barcode. EXPECT refine-tags to
             correct them, so the per-(cell,feature) distinct-UMI counts ~= the baseline truth.
  offpanel   adds reads with off-panel feature barcodes (NOT in tags.csv) + a few malformed reads.
             EXPECT the off-panel barcodes to be dropped by the tags.csv inner join, and malformed
             reads dropped at parse -> output contains only the 5 panel features.
  multilane  the same reads split across two lanes (L001/L002). EXPECT lane-merged per-cell totals to
             equal the single-lane baseline (exercises the fb-pipeline keyLength==2 branch).
  control    binders + a ~30% TRUE non-binder population (all antigens at control level). Exercises the
             negative-control SPECIFICITY path: run the block with the negative control set and EXPECT
             binder cells to score high specificityScore on their dominant antigen, non-binders low
             everywhere. Ground truth in expected-specificity.tsv.

Non-baseline scenarios are written to scenarios/<name>/. Each carries the same ground-truth tables
(planted, panel-only) — for errors/offpanel/multilane the EXPECTED block output equals that truth, so
the scenario is a behavioral assertion: "this perturbation must not change the result."

Run:  python3 generate.py [--scenario baseline|errors|offpanel|multilane|all]
"""

import argparse
import csv
import gzip
import os

import random as _random

SEED = 20260629
HERE = os.path.dirname(os.path.abspath(__file__))

CELL_LEN = 16
UMI_LEN = 10
FEAT_LEN = 15
R2_FILLER = "CAACTGGTAC"  # fixed 10 bp after the feature barcode; captured by R2:* and ignored
QUAL_CHAR = "I"  # Phred 40

SAMPLES = ["donorA", "donorB"]
CELLS_PER_SAMPLE = 80

# Real 10x BEAM-Ab antigen panel (4 antigens + negative control), 15 bp, read R2, pattern ^(BC).
# https://www.10xgenomics.com/datasets/2k-transgenic-hel-mouse-splenocytes-beam-ab-2-standard
FEATURE_PANEL = [
    ("SARS-TRI-S_WT", "CGATGCCGGACGATC"),
    ("Anti-Hen_Egg_Lysozyme", "CCGTCTCACCGATAT"),
    ("gp120", "GATTGGCTACTCAAT"),
    ("H5N1", "CGGCTCACCGCGTCT"),
]
CONTROL_NAME = "negative_control"
CONTROL_BC = "CTATCTACCGGCTCG"
ANTIGEN_NAMES = [n for n, _ in FEATURE_PANEL]
FEATURES = {n: bc for n, bc in FEATURE_PANEL}
FEATURES[CONTROL_NAME] = CONTROL_BC
assert all(len(bc) == FEAT_LEN for bc in FEATURES.values()), "feature barcodes must be 15 bp"

BASES = "ACGT"


def rand_seq(rng, n):
    return "".join(rng.choice(BASES) for _ in range(n))


def hamming(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def gen_distinct(rng, count, length, min_dist, avoid=()):
    """Generate `count` sequences pairwise >= min_dist apart and >= min_dist from every `avoid`."""
    out = []
    guard = 0
    while len(out) < count:
        guard += 1
        if guard > count * 10000:
            raise RuntimeError("could not generate enough distinct barcodes; relax min_dist")
        cand = rand_seq(rng, length)
        if all(hamming(cand, e) >= min_dist for e in list(out) + list(avoid)):
            out.append(cand)
    return out


def mutate(rng, seq, n_subs=1):
    """Return `seq` with `n_subs` single-base substitutions at distinct positions."""
    s = list(seq)
    for p in rng.sample(range(len(s)), n_subs):
        s[p] = rng.choice([b for b in BASES if b != s[p]])
    return "".join(s)


def load_whitelist_cells(rng, count):
    """Draw `count` distinct real 737K-august-2016-compliant cell barcodes from whitelist_cells.txt
    (harvested from a real 5' v2 BEAM-T run via `refine-tags -t CELL#builtin:737K-august-2016` →
    `tag-stat -t CELL`, so every barcode is a 737K member). Lets the whitelist737k profile be corrected
    against the real 10x list without dropping cells."""
    path = os.path.join(HERE, "whitelist_cells.txt")
    if not os.path.exists(path):
        raise SystemExit(f"missing {path} — the harvested 737K-compliant cell-barcode pool")
    with open(path) as f:
        pool = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if len(pool) < count:
        raise SystemExit(f"whitelist_cells.txt has {len(pool)} barcodes; need {count}")
    return rng.sample(pool, count)


def add_ambient(rng, reads, frac=0.15):
    """Append ambient reads with random OFF-737K cell barcodes (low count), on-panel features. Models the
    ambient/error barcode tail of a real run: a 737K cell-whitelist drops them (as real Cell Ranger/mixcr
    do), while de-novo keeps them as phantom low-count cells. NOT added to the truth tables — the truth is
    the real cells only, so a whitelisted run reproduces it and a de-novo run shows the extra phantoms."""
    n = int(len(reads) * frac)
    base = len(reads)
    feats = list(FEATURES.values())
    for i in range(n):
        cell = rand_seq(rng, CELL_LEN)  # random 16-mer -> effectively off-737K ambient
        fbc = rng.choice(feats)
        umi = rand_seq(rng, UMI_LEN)
        for _ in range(rng.randint(1, 2)):
            reads.append([f"ambient_read{base + i}", cell + umi, fbc + R2_FILLER, 1])
    return reads


def fq_record(name, seq):
    return f"@{name}\n{seq}\n+\n{QUAL_CHAR * len(seq)}\n"


def assign_features(rng, realistic=False, nonbinder=False):
    """Plant the per-feature distinct-UMI counts for one cell. Returns (per_feature, consensus_label).

    One dominant antigen (high), optional ambiguous second, 0-2 ambient antigens (low), control
    background. Identical logic across all scenarios so the ground truth is shared.

    `nonbinder=True` (used by the `control` scenario) plants a TRUE non-binder: no antigen above
    background — every antigen sits at ~control level. With a negative control set, the block should
    assign LOW specificity to every feature (nothing beats the control) and no consensus (nothing passes
    the dominance threshold). This is the "true negative" population a specificity test needs.

    `realistic=True` calibrates the magnitudes to the real 5k BEAM-T antigen library measured
    2026-07-01 (see real-data-calibration.md): dominant ~600 UMIs (right-skewed, low-signal tail),
    near-mono dominance (median ~1.0, p10 ~0.79), tight background (~3 UMIs/cell). The DEFAULT branch
    is left byte-for-byte unchanged so the committed baseline fixtures are unaffected."""
    per_feature = {}
    if nonbinder:
        bg_hi = 3 if realistic else 4
        for a in ANTIGEN_NAMES:
            if rng.random() < 0.6:
                per_feature[a] = rng.randint(1, bg_hi)
        per_feature[CONTROL_NAME] = rng.randint(1, bg_hi)
        return per_feature, "ambiguous"
    dominant = rng.choice(ANTIGEN_NAMES)
    if realistic:
        # median ~600, p10 ~20 / p90 ~1500; ~10% of cells are low-signal (real p10 total UMIs ~18)
        dom_umis = rng.randint(10, 60) if rng.random() < 0.1 else rng.randint(300, 1100)
    else:
        dom_umis = rng.randint(8, 30)
    per_feature[dominant] = dom_umis

    ambiguous = rng.random() < 0.12
    if ambiguous:
        second = rng.choice([a for a in ANTIGEN_NAMES if a != dominant])
        if realistic:
            # cross-reactive but still below dominant (keeps dominance in the real p10~0.79 tail);
            # cross-reactivity is a BEAM-Ab feature — the BEAM-T sample itself showed ~none
            per_feature[second] = max(1, int(dom_umis * rng.uniform(0.3, 0.9)))
        else:
            per_feature[second] = max(1, dom_umis + rng.randint(-2, 2))

    others = [a for a in ANTIGEN_NAMES if a not in per_feature]
    rng.shuffle(others)
    bg_hi = 3 if realistic else 4  # real background: median ~3, p90 ~9 UMIs/cell total
    for a in others[:rng.randint(0, 2)]:
        per_feature[a] = rng.randint(1, bg_hi)

    if rng.random() < 0.7:
        per_feature[CONTROL_NAME] = rng.randint(1, 3)

    return per_feature, ("ambiguous" if ambiguous else dominant)


def build_sample(rng, sample, cells, realistic=False, nonbinder_frac=0.0):
    """Clean per-sample reads (no scenario perturbation). Returns (reads, truth_ab, truth_con, truth_class).
    reads = list of [name, r1, r2, lane(=1)]. truth_class = per-cell (sample, cell, class, dominant) with
    class in {binder, ambiguous, nonbinder}. `nonbinder_frac`>0 (the control scenario) makes that fraction
    of cells true non-binders. The `nonbinder_frac > 0 and ...` short-circuit means frac=0 consumes NO
    extra RNG, so the default/realistic/whitelist737k baselines stay byte-identical."""
    reads = []
    truth_ab = []
    truth_con = []
    truth_class = []
    read_no = 0
    for cell in cells:
        is_nb = nonbinder_frac > 0 and rng.random() < nonbinder_frac
        per_feature, con = assign_features(rng, realistic, nonbinder=is_nb)
        cls = "nonbinder" if is_nb else ("ambiguous" if con == "ambiguous" else "binder")
        truth_class.append((sample, cell, cls, "" if con == "ambiguous" else con))
        for feat, k in per_feature.items():
            truth_ab.append((sample, cell, feat, k))
        truth_con.append((sample, cell, con))
        for feat, k in per_feature.items():
            fbc = FEATURES[feat]
            umis = set()
            while len(umis) < k:
                umis.add(rand_seq(rng, UMI_LEN))
            # sorted(): set iteration order of strings varies per process (PYTHONHASHSEED), which would
            # make the emitted read order / numbering non-reproducible. The reads are identical either
            # way (mitool aggregates by key), but sorting makes the fixture byte-stable.
            for umi in sorted(umis):
                # real PCR dup: median ~1.3 reads/UMI, p90 ~2 (vs the flat 1-4 default)
                dups = (1 if rng.random() < 0.75 else (2 if rng.random() < 0.8 else 3)) if realistic else rng.randint(1, 4)
                for _ in range(dups):
                    read_no += 1
                    reads.append([f"{sample}_read{read_no}", cell + umi, fbc + R2_FILLER, 1])
    return reads, truth_ab, truth_con, truth_class


# --- scenario perturbations (applied AFTER the clean build, so the baseline stream is untouched) ---


def perturb_errors(rng, reads, frac=0.15):
    """Inject a 1 bp error into the cell OR feature barcode of `frac` of reads."""
    for r in reads:
        if rng.random() < frac:
            if rng.random() < 0.5:
                r[1] = mutate(rng, r[1][:CELL_LEN]) + r[1][CELL_LEN:]  # cell barcode
            else:
                r[2] = mutate(rng, r[2][:FEAT_LEN]) + r[2][FEAT_LEN:]  # feature barcode
    return reads


def perturb_offpanel(rng, reads, cells, off_bcs, frac=0.12, n_malformed=40):
    """Append reads with off-panel feature barcodes (not in tags.csv) + some malformed reads.
    off_bcs is shared across samples so it can be recorded once."""
    n_extra = int(len(reads) * frac)
    base = len(reads)
    for i in range(n_extra):
        cell = rng.choice(cells)
        fbc = rng.choice(off_bcs)
        umi = rand_seq(rng, UMI_LEN)
        for _ in range(rng.randint(1, 3)):
            reads.append([f"offpanel_read{base + i}", cell + umi, fbc + R2_FILLER, 1])
    # malformed: R1 too short for CELL+UMI, or R2 too short for the 15 bp feature -> parse drops them
    for i in range(n_malformed):
        if i % 2 == 0:
            reads.append([f"malformed_read{i}", rand_seq(rng, 18), rand_seq(rng, FEAT_LEN) + R2_FILLER, 1])
        else:
            reads.append([f"malformed_read{i}", rand_seq(rng, 26), rand_seq(rng, 10), 1])
    return reads


def assign_lanes(rng, reads, n_lanes=2):
    for r in reads:
        r[3] = rng.randint(1, n_lanes)
    return reads


# --- writers ---


def _write_gz(path, text):
    # Deterministic gzip: no embedded mtime or filename, so a re-run with the same seed produces
    # byte-identical fixtures.
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(text.encode())


def write_fastqs(outdir, sample, reads, multilane):
    def join(idx, subset):
        return "".join(fq_record(r[0], r[idx]) for r in subset)

    if not multilane:
        _write_gz(os.path.join(outdir, f"{sample}_R1.fastq.gz"), join(1, reads))
        _write_gz(os.path.join(outdir, f"{sample}_R2.fastq.gz"), join(2, reads))
    else:
        for lane in sorted({r[3] for r in reads}):
            tag = f"L{lane:03d}"
            lane_reads = [r for r in reads if r[3] == lane]
            _write_gz(os.path.join(outdir, f"{sample}_{tag}_R1.fastq.gz"), join(1, lane_reads))
            _write_gz(os.path.join(outdir, f"{sample}_{tag}_R2.fastq.gz"), join(2, lane_reads))


def write_metadata(outdir):
    with open(os.path.join(outdir, "tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag", "feature"])
        for name, bc in FEATURES.items():
            w.writerow([bc, name])
    with open(os.path.join(outdir, "feature_reference.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "read", "pattern", "sequence", "feature_type"])
        for name, bc in FEATURES.items():
            w.writerow([name, name, "R2", "^(BC)", bc, "Antigen Capture"])
    with open(os.path.join(outdir, "samples-metadata.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["Sample", "Donor", "Condition"])
        w.writerow(["donorA", "Donor A", "baseline"])
        w.writerow(["donorB", "Donor B", "stimulated"])


def write_truth(outdir, truth_ab, truth_con):
    truth_ab.sort()
    with open(os.path.join(outdir, "expected-abundance.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "feature", "planted_distinct_umis"])
        w.writerows(truth_ab)
    truth_con.sort()
    with open(os.path.join(outdir, "expected-consensus.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "planted_consensus"])
        w.writerows(truth_con)


def write_specificity(outdir, truth_class):
    """Specificity ground truth for the `control` scenario: per-cell binder/non-binder class + intended
    dominant antigen. With a negative control set, EXPECT: binder cells → high specificityScore on their
    dominantAntigen (and low elsewhere); nonbinder/ambiguous cells → low specificityScore everywhere."""
    truth_class.sort()
    with open(os.path.join(outdir, "expected-specificity.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "cellClass", "dominantAntigen"])
        w.writerows(truth_class)


def generate(scenario, profile="default"):
    """Generate one scenario. Baseline RNG order (seed -> gen cells -> per-sample build+shuffle) is
    preserved exactly for the `default` `baseline`, so its files stay byte-identical. `realistic` and
    `whitelist737k` write to their own dir so the default fixtures are untouched. whitelist737k = realistic
    depths + real 737K-compliant cell barcodes + an ambient off-list read tail (baseline only)."""
    realistic = profile in ("realistic", "whitelist737k")
    use_whitelist = profile == "whitelist737k"
    rng = _random.Random(SEED)
    root = os.path.join(HERE, profile) if profile != "default" else HERE
    outdir = root if scenario == "baseline" else os.path.join(root, "scenarios", scenario)
    os.makedirs(outdir, exist_ok=True)

    if use_whitelist:
        all_cells = load_whitelist_cells(rng, len(SAMPLES) * CELLS_PER_SAMPLE)
    else:
        all_cells = gen_distinct(rng, len(SAMPLES) * CELLS_PER_SAMPLE, CELL_LEN, min_dist=4)
    cells_by_sample = {
        s: all_cells[i * CELLS_PER_SAMPLE:(i + 1) * CELLS_PER_SAMPLE] for i, s in enumerate(SAMPLES)
    }

    # Off-panel barcodes: one shared set across samples, from an independent RNG so it doesn't perturb
    # the per-sample build stream.
    off_bcs = (
        gen_distinct(_random.Random(SEED + 99), 3, FEAT_LEN, min_dist=5, avoid=list(FEATURES.values()))
        if scenario == "offpanel" else []
    )

    nonbinder_frac = 0.3 if scenario == "control" else 0.0
    all_ab, all_con, all_cls = [], [], []
    for sample in SAMPLES:
        cells = cells_by_sample[sample]
        reads, ab, con, cls = build_sample(rng, sample, cells, realistic, nonbinder_frac)
        all_ab += ab
        all_con += con
        all_cls += cls

        if scenario == "errors":
            reads = perturb_errors(rng, reads)
        elif scenario == "offpanel":
            reads = perturb_offpanel(rng, reads, cells, off_bcs)
        elif scenario == "multilane":
            reads = assign_lanes(rng, reads)
        if use_whitelist:
            reads = add_ambient(rng, reads)  # ambient off-737K junk a whitelist drops, de-novo keeps

        rng.shuffle(reads)
        write_fastqs(outdir, sample, reads, multilane=(scenario == "multilane"))
        print(f"  {sample}: {len(cells)} cells, {len(reads)} reads")

    write_metadata(outdir)
    write_truth(outdir, all_ab, all_con)
    if scenario == "control":
        write_specificity(outdir, all_cls)
        nb = sum(1 for c in all_cls if c[2] == "nonbinder")
        print(f"  control: {nb} non-binders / {len(all_cls) - nb} binders+ambiguous -> expected-specificity.tsv")
    if scenario == "offpanel" and off_bcs:
        with open(os.path.join(outdir, "offpanel-barcodes.txt"), "w") as f:
            f.write("# feature barcodes injected into R2 that are NOT in tags.csv — the block must drop them\n")
            f.write("\n".join(off_bcs) + "\n")
    print(f"[{scenario}] -> {outdir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenario",
        default="baseline",
        choices=["baseline", "errors", "offpanel", "multilane", "control", "all"],
    )
    ap.add_argument(
        "--profile", default="default", choices=["default", "realistic", "whitelist737k"],
        help="'realistic' calibrates UMI depth/dup/dominance to the real BEAM-T library "
             "(real-data-calibration.md) and writes to realistic/. 'whitelist737k' = realistic depths + "
             "real 737K-august-2016-compliant cell barcodes (whitelist_cells.txt) + an ambient off-list "
             "read tail, written to whitelist737k/ (run the block with cell whitelist = 737K-august-2016). "
             "The default fixtures are left untouched.",
    )
    args = ap.parse_args()
    scenarios = ["baseline", "errors", "offpanel", "multilane", "control"] if args.scenario == "all" else [args.scenario]
    if args.profile == "whitelist737k":
        scenarios = ["baseline"]  # the 737K profile is the coherent multiomics antigen arm (baseline only)
    for s in scenarios:
        generate(s, args.profile)
    print("features:", FEATURES, "| control:", CONTROL_NAME, "| profile:", args.profile)


if __name__ == "__main__":
    main()
