"""Antigen (feature-barcode / antigen-capture) arm generator.

Emits paired R1/R2 FASTQs for the Feature Integration block plus the ground-truth tables the VDJ/GEX
arms and the validator build on. Scale + cell-barcode source come from an AntigenConfig (replacing the
old module globals), so a run is fully described by its arguments. UMI depth/dup/dominance magnitudes
are calibrated to a real 5k BEAM-T antigen library (see real-data-calibration.md).

Scenarios, each a self-contained dataset targeting ONE untested behaviour that the Python unit tests
(test_per_cell_metrics.py) cannot reach:

  baseline   happy path: clean barcodes, single lane, on-panel only.
  errors     ~15% of reads carry a 1 bp error in the cell OR feature barcode. EXPECT refine-tags to
             correct them, so per-(cell,feature) distinct-UMI counts ~= the baseline truth.
  offpanel   adds off-panel feature barcodes (NOT in tags.csv) + malformed reads. EXPECT them dropped.
  multilane  the same reads split across two lanes (L001/L002). EXPECT lane-merged totals == baseline.
  control    binders + a ~30% TRUE non-binder population. Exercises the negative-control specificity
             path (ground truth in expected-specificity.tsv).
  degraded   QC-visualisation bed: samples degraded to different levels so the block's Quality tag
             (OK/WARN/ALERT) and Read-recovery bar show a full spread.
"""

import csv
import os
from dataclasses import dataclass

from .common import (
    ANTIGEN_SEED,
    CELL_LEN,
    FEAT_LEN,
    R2_FILLER,
    UMI_LEN,
    gen_cells,
    gen_distinct,
    mutate,
    new_rng,
    rand_seq,
    write_fastq_gz,
)


@dataclass
class AntigenConfig:
    samples: list  # donor sample names
    cells_per_sample: int
    barcode_source: str = "random"  # "random" or "whitelist737k"
    assets_dir: str = None  # holds 737K-august-2016.txt / whitelist_cells.txt
    seed: int = ANTIGEN_SEED


def load_whitelist_cells(rng, count, assets_dir):
    """Draw `count` distinct real 737K-august-2016 cell barcodes. Prefers the full 10x inclusion list
    (737K-august-2016.txt, ~737k barcodes, fetched on demand — gitignored); falls back to the small
    harvested pool (whitelist_cells.txt, ~800). Every barcode is a 737K member, so the whitelist737k
    profile can be corrected against the real 10x list without dropping cells."""
    big = os.path.join(assets_dir, "737K-august-2016.txt")
    small = os.path.join(assets_dir, "whitelist_cells.txt")
    path = big if os.path.exists(big) else small
    if not os.path.exists(path):
        raise SystemExit(
            "no cell whitelist found. Fetch the full 10x 737K-august-2016 inclusion list into "
            f"{assets_dir}:\n"
            "  curl -sSL -o 737K-august-2016.txt https://raw.githubusercontent.com/10XGenomics/"
            "supernova/master/tenkit/lib/python/tenkit/barcodes/737K-august-2016.txt"
        )
    with open(path) as f:
        pool = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if len(pool) < count:
        raise SystemExit(
            f"{os.path.basename(path)} has {len(pool)} barcodes; need {count}. Fetch the full "
            "737K-august-2016 list (see load_whitelist_cells) or reduce the sample/cell scale."
        )
    return rng.sample(pool, count)


def add_ambient(rng, panel, reads, frac=0.15):
    """Append ambient reads with random OFF-737K cell barcodes (low count), on-panel features. Models
    the ambient/error barcode tail of a real run: a 737K cell-whitelist drops them, while de-novo keeps
    them as phantom low-count cells. NOT added to the truth tables."""
    n = int(len(reads) * frac)
    base = len(reads)
    feats = panel.barcodes
    for i in range(n):
        cell = rand_seq(rng, CELL_LEN)  # random 16-mer -> effectively off-737K ambient
        fbc = rng.choice(feats)
        umi = rand_seq(rng, UMI_LEN)
        for _ in range(rng.randint(1, 2)):
            reads.append([f"ambient_read{base + i}", cell + umi, fbc + R2_FILLER, 1])
    return reads


def assign_features(rng, panel, nonbinder=False, crossreactive=False):
    """Plant the per-feature distinct-UMI counts for one cell. Returns (per_feature, consensus_label).

    One dominant antigen (high), optional ambiguous second, 0-2 ambient antigens (low), control
    background. `nonbinder=True` (the control scenario) plants a TRUE non-binder: every antigen at
    ~control level. `crossreactive=True` plants a co-dominant pair of two ON-TARGET antigens at
    ~equal UMIs (second at 0.85-1.0x the first): neither passes the dominance threshold alone but their
    on-target sum does, so the block calls the cell "cross-reactive" (not "ambiguous"). Magnitudes are
    calibrated to the real 5k BEAM-T library: dominant ~600 UMIs (right-skewed, low-signal tail),
    near-mono dominance (median ~1.0, p10 ~0.79), tight background (~3 UMIs/cell)."""
    antigen_names = panel.names
    control = panel.control_name
    per_feature = {}
    if nonbinder:
        bg_hi = 3
        for a in antigen_names:
            if rng.random() < 0.6:
                per_feature[a] = rng.randint(1, bg_hi)
        per_feature[control] = rng.randint(1, bg_hi)
        return per_feature, "ambiguous"
    if crossreactive:
        # Co-dominant on-TARGET pair: both must be Type=Target (not the control, not an Off-Target), so
        # the block's dominant call excludes neither and — with two on-targets sharing the signal near
        # 50/50 — lands on "cross-reactive" rather than a single dominant antigen or "ambiguous".
        on_target = [a for a in antigen_names if panel.types.get(a) == "Target"]
        if len(on_target) >= 2:
            first, second = rng.sample(on_target, 2)
            dom_umis = rng.randint(10, 60) if rng.random() < 0.1 else rng.randint(300, 1100)
            per_feature[first] = dom_umis
            # near-equal second keeps both above threshold-share of the on-target sum while denying
            # either a unique-dominant call (each ~50% of the total, below the 0.6 threshold)
            per_feature[second] = max(1, int(dom_umis * rng.uniform(0.85, 1.0)))
            others = [a for a in antigen_names if a not in per_feature]
            rng.shuffle(others)
            bg_hi = 3
            for a in others[: rng.randint(0, 2)]:
                per_feature[a] = rng.randint(1, bg_hi)
            if rng.random() < 0.7:
                per_feature[control] = rng.randint(1, 3)
            return per_feature, "crossreactive"
        # <2 on-target antigens: can't plant a cross-reactive pair — fall through to a normal binder.
    dominant = rng.choice(antigen_names)
    # median ~600, p10 ~20 / p90 ~1500; ~10% of cells are low-signal (real p10 total UMIs ~18)
    dom_umis = rng.randint(10, 60) if rng.random() < 0.1 else rng.randint(300, 1100)
    per_feature[dominant] = dom_umis

    ambiguous = rng.random() < 0.12
    if ambiguous:
        second = rng.choice([a for a in antigen_names if a != dominant])
        # cross-reactive but still below dominant (keeps dominance in the real p10~0.79 tail);
        # cross-reactivity is a BEAM-Ab feature — the BEAM-T sample itself showed ~none
        per_feature[second] = max(1, int(dom_umis * rng.uniform(0.3, 0.9)))

    others = [a for a in antigen_names if a not in per_feature]
    rng.shuffle(others)
    bg_hi = 3  # real background: median ~3, p90 ~9 UMIs/cell total
    for a in others[: rng.randint(0, 2)]:
        per_feature[a] = rng.randint(1, bg_hi)

    if rng.random() < 0.7:
        per_feature[control] = rng.randint(1, 3)

    return per_feature, ("ambiguous" if ambiguous else dominant)


def build_sample(rng, panel, sample, cells, nonbinder_frac=0.0, crossreactive_frac=0.0):
    """Clean per-sample reads (no scenario perturbation). Returns (reads, truth_ab, truth_con,
    truth_class). reads = list of [name, r1, r2, lane(=1)]. The `frac > 0 and ...` short-circuits mean
    both fracs at 0 consume NO extra RNG, so the baselines stay reproducible. A cell is at most one of
    nonbinder / crossreactive (nonbinder wins the roll)."""
    features = panel.features
    reads = []
    truth_ab = []
    truth_con = []
    truth_class = []
    read_no = 0
    for cell in cells:
        is_nb = nonbinder_frac > 0 and rng.random() < nonbinder_frac
        is_cr = (not is_nb) and crossreactive_frac > 0 and rng.random() < crossreactive_frac
        per_feature, con = assign_features(rng, panel, nonbinder=is_nb, crossreactive=is_cr)
        if is_nb:
            cls = "nonbinder"
        elif con == "crossreactive":
            cls = "crossreactive"
        elif con == "ambiguous":
            cls = "ambiguous"
        else:
            cls = "binder"
        truth_class.append((sample, cell, cls, "" if con in ("ambiguous", "crossreactive") else con))
        for feat, k in per_feature.items():
            truth_ab.append((sample, cell, feat, k))
        truth_con.append((sample, cell, con))
        for feat, k in per_feature.items():
            fbcs = features[feat]
            if len(fbcs) == 1:
                fbc = fbcs[0]
                umis = set()
                while len(umis) < k:
                    umis.add(rand_seq(rng, UMI_LEN))
                # sorted(): set iteration order of strings varies per process (PYTHONHASHSEED), which
                # would make the emitted read order non-reproducible. Sorting keeps the output byte-stable.
                for umi in sorted(umis):
                    # real PCR dup: median ~1.3 reads/UMI, p90 ~2
                    dups = 1 if rng.random() < 0.75 else (2 if rng.random() < 0.8 else 3)
                    for _ in range(dups):
                        read_no += 1
                        reads.append([f"{sample}_read{read_no}", cell + umi, fbc + R2_FILLER, 1])
            else:
                # Multi-barcode antigen (only present in a --multibarcode panel; single-barcode runs
                # never take this branch, so they stay byte-identical). combine="all" fires EVERY
                # member barcode at ~k UMIs (AND / dual-probe); combine="sum" splits the k UMIs across
                # the members so the per-feature sum stays ~k. Same UMI/dup shape as the single path.
                mode = panel.combine.get(feat, "sum")
                if mode == "all":
                    shares = [k] * len(fbcs)
                else:
                    base = k // len(fbcs)
                    shares = [base] * len(fbcs)
                    shares[0] += k - base * len(fbcs)
                for fbc, share in zip(fbcs, shares):
                    if share <= 0:
                        continue
                    umis = set()
                    while len(umis) < share:
                        umis.add(rand_seq(rng, UMI_LEN))
                    for umi in sorted(umis):
                        dups = 1 if rng.random() < 0.75 else (2 if rng.random() < 0.8 else 3)
                        for _ in range(dups):
                            read_no += 1
                            reads.append([f"{sample}_read{read_no}", cell + umi, fbc + R2_FILLER, 1])
    return reads, truth_ab, truth_con, truth_class


# QC-visualisation profile: samples degraded to DIFFERENT levels so the block's Quality tag and Read
# recovery bar show a full spread. (sample, matched_frac, panel_assigned_frac, expected_tag).
# matched < 80% or panel-assigned < 50% -> WARN; panel-assigned < 25% -> ALERT (ui/src/results.ts).
DEGRADED_PROFILE = [
    ("donor01_clean", 1.00, 1.00, "OK"),
    ("donor02_lowmatch", 0.65, 1.00, "WARN"),  # 35% of reads fail parse -> matched 65%
    ("donor03_offpanel", 0.95, 0.40, "WARN"),  # 60% of matched reads off-panel -> panel-assigned 40%
    ("donor04_badpanel", 0.90, 0.15, "ALERT"),  # 85% off-panel -> panel-assigned 15%
]


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
    """Append reads with off-panel feature barcodes (not in tags.csv) + some malformed reads."""
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


def convert_offpanel(rng, reads, off_bcs, off_frac):
    """Rewrite `off_frac` of the (structurally valid) reads' feature barcodes to OFF-panel barcodes
    (Hamming >= 5 from every panel entry, so refine-tags drops rather than corrects them)."""
    if off_frac <= 0:
        return reads
    k = min(len(reads), int(len(reads) * off_frac))
    for i in rng.sample(range(len(reads)), k):
        reads[i][2] = rng.choice(off_bcs) + R2_FILLER
    return reads


def add_malformed(rng, reads, matched_frac):
    """Append malformed reads so parse drops them and matched ~= `matched_frac`."""
    if matched_frac >= 1.0:
        return reads
    m = max(0.01, matched_frac)
    n_bad = int(len(reads) * (1 - m) / m)
    base = len(reads)
    for i in range(n_bad):
        if i % 2 == 0:
            reads.append([f"malformed_read{base + i}", rand_seq(rng, 18), rand_seq(rng, FEAT_LEN) + R2_FILLER, 1])
        else:
            reads.append([f"malformed_read{base + i}", rand_seq(rng, 26), rand_seq(rng, 10), 1])
    return reads


# --- writers ---


def write_fastqs(outdir, sample, reads, multilane):
    if not multilane:
        write_fastq_gz(os.path.join(outdir, f"{sample}_R1.fastq.gz"), reads, 1)
        write_fastq_gz(os.path.join(outdir, f"{sample}_R2.fastq.gz"), reads, 2)
    else:
        for lane in sorted({r[3] for r in reads}):
            tag = f"L{lane:03d}"
            lane_reads = [r for r in reads if r[3] == lane]
            write_fastq_gz(os.path.join(outdir, f"{sample}_{tag}_R1.fastq.gz"), lane_reads, 1)
            write_fastq_gz(os.path.join(outdir, f"{sample}_{tag}_R2.fastq.gz"), lane_reads, 2)


def _messify(value, rng):
    """Return a casing/whitespace variant of a panel LABEL, mimicking the real customer panel's B043
    inconsistency (e.g. "Off-Target" -> "Off-target"). Used ONLY for emitted CSV label columns under
    --messy-metadata; it never touches the barcode join keys or the truth tables, so generation stays
    coherent while the block-facing labels carry the mess the normalization tasks must resolve."""
    if rng.random() < 0.5:
        # case variant: lower-case the segment after the last hyphen ("Off-Target" -> "Off-target")
        head, sep, tail = value.rpartition("-")
        return head + sep + tail.lower() if sep else value.lower()
    # whitespace variant: double the first existing space (a label with no space is left unchanged)
    i = value.find(" ")
    return value[:i] + "  " + value[i + 1 :] if i != -1 else value


def _messy_types(panel, rng):
    """Per-antigen Type overrides that GUARANTEE a mixed-case off-target set (the B043 `Off-Target` vs
    `Off-target` bug): the first off-target stays canonical `Off-Target`, the second is forced to
    `Off-target`, any further off-targets get a seeded `_messify` variant. Targets and the control keep
    their canonical Type. Returns {name: type} for the off-targets only (callers fall back to
    panel.types for everything else)."""
    offtargets = [n for n in panel.names if panel.types.get(n) == "Off-Target"]
    override = {}
    for idx, n in enumerate(offtargets):
        if idx == 0:
            override[n] = "Off-Target"  # canonical anchor of the mix
        elif idx == 1:
            override[n] = "Off-target"  # forced lower-case -> the mix is present whenever >= 2 off-targets
        else:
            override[n] = _messify(panel.types[n], rng)
    return override


def write_metadata(shared_dir, panel, samples, multibarcode=False, messy=False):
    # --messy-metadata: inject the real customer panel's inconsistent Type casing into the EMITTED tags.csv
    # values only (the feature-name double space is injected upstream in build_panel). A dedicated
    # constant-seed RNG keeps the mess deterministic without perturbing the read/truth streams (which are
    # already generated by the time write_metadata runs). Off by default -> byte-identical to before.
    type_override = _messy_types(panel, new_rng(ANTIGEN_SEED + 7777)) if messy else {}

    def type_of(name):
        return type_override.get(name, panel.types.get(name, ""))

    with open(os.path.join(shared_dir, "tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        # tag,feature stay first (backward-compatible role mapping); Type/Species/Class mirror the real
        # customer panel so downstream off-target-call and species-grouping have synthetic inputs. A
        # --multibarcode panel adds `combine` (between feature and Type) and emits one row PER member
        # barcode; single-barcode runs keep the exact prior header + one row per feature (byte-stable).
        if multibarcode:
            w.writerow(["tag", "feature", "combine", "Type", "Species", "Class"])
            for name, bcs in panel.features.items():
                for bc in bcs:
                    w.writerow(
                        [
                            bc,
                            name,
                            panel.combine.get(name, "sum"),
                            type_of(name),
                            panel.species.get(name, ""),
                            panel.classes.get(name, ""),
                        ]
                    )
        else:
            w.writerow(["tag", "feature", "Type", "Species", "Class"])
            for name, bcs in panel.features.items():
                w.writerow([bcs[0], name, type_of(name), panel.species.get(name, ""), panel.classes.get(name, "")])
    with open(os.path.join(shared_dir, "feature_reference.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "read", "pattern", "sequence", "feature_type"])
        for name, bcs in panel.features.items():
            for j, bc in enumerate(bcs):
                # per-member id `<feat>_<n>` when a feature has >1 barcode; bare feature name otherwise
                # (so single-barcode feature_reference.csv is byte-identical to before).
                bc_id = f"{name}_{j + 1}" if len(bcs) > 1 else name
                w.writerow([bc_id, name, "R2", "^(BC)", bc, "Antigen Capture"])
    with open(os.path.join(shared_dir, "samples-metadata.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["Sample", "Donor", "Condition"])
        for i, s in enumerate(samples):
            # alternate a simple 2-arm condition so downstream grouping has something to split on
            w.writerow([s, f"Donor {i + 1}", "baseline" if i % 2 == 0 else "stimulated"])


def write_truth(truth_dir, truth_ab, truth_con):
    truth_ab.sort()
    with open(os.path.join(truth_dir, "expected-abundance.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "feature", "planted_distinct_umis"])
        w.writerows(truth_ab)
    truth_con.sort()
    with open(os.path.join(truth_dir, "expected-consensus.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "planted_consensus"])
        w.writerows(truth_con)


def write_specificity(truth_dir, truth_class):
    """Specificity ground truth for the `control` scenario: per-cell binder/non-binder class + intended
    dominant antigen. With a negative control set, EXPECT binder cells -> high specificityScore on their
    dominantAntigen, non-binders low everywhere."""
    truth_class.sort()
    with open(os.path.join(truth_dir, "expected-specificity.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "cellClass", "dominantAntigen"])
        w.writerows(truth_class)


def build(
    cfg, panel, scenario, fastq_dir, shared_dir, truth_dir, crossreactive_frac=0.0, multibarcode=False, messy=False
):
    """Generate one antigen scenario into the given dirs.

    fastq_dir  - R1/R2 FASTQs (+ offpanel-barcodes.txt for the offpanel scenario)
    shared_dir - tags.csv / feature_reference.csv / samples-metadata.tsv (the block uploads)
    truth_dir  - expected-abundance/consensus (+ expected-specificity for control)

    For a colocated preset run these are runs/<preset>/{antigen, ., truth}; for a standalone scenario
    all three point at runs/scenarios/<name>/.
    """
    use_whitelist = cfg.barcode_source == "whitelist737k"
    rng = new_rng(cfg.seed)
    for d in (fastq_dir, shared_dir, truth_dir):
        os.makedirs(d, exist_ok=True)

    samples = cfg.samples
    n_total = len(samples) * cfg.cells_per_sample
    if use_whitelist:
        all_cells = load_whitelist_cells(rng, n_total, cfg.assets_dir)
    else:
        all_cells = gen_cells(rng, n_total)
    cells_by_sample = {
        s: all_cells[i * cfg.cells_per_sample : (i + 1) * cfg.cells_per_sample] for i, s in enumerate(samples)
    }

    # Off-panel barcodes: one shared set across samples, from an independent RNG so it doesn't perturb
    # the per-sample build stream.
    off_bcs = (
        gen_distinct(new_rng(cfg.seed + 99), 3, FEAT_LEN, min_dist=5, avoid=panel.barcodes)
        if scenario == "offpanel"
        else []
    )

    nonbinder_frac = 0.3 if scenario == "control" else 0.0
    all_ab, all_con, all_cls = [], [], []
    total_reads = 0
    for sample in samples:
        cells = cells_by_sample[sample]
        reads, ab, con, cls = build_sample(rng, panel, sample, cells, nonbinder_frac, crossreactive_frac)
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
            reads = add_ambient(rng, panel, reads)  # ambient off-737K junk a whitelist drops

        rng.shuffle(reads)
        write_fastqs(fastq_dir, sample, reads, multilane=(scenario == "multilane"))
        total_reads += len(reads)
        print(f"  {sample}: {len(cells)} cells, {len(reads)} reads")

    write_metadata(shared_dir, panel, samples, multibarcode=multibarcode, messy=messy)
    write_truth(truth_dir, all_ab, all_con)
    if scenario == "control":
        write_specificity(truth_dir, all_cls)
        nb = sum(1 for c in all_cls if c[2] == "nonbinder")
        print(f"  control: {nb} non-binders / {len(all_cls) - nb} binders+ambiguous -> expected-specificity.tsv")
    if scenario == "offpanel" and off_bcs:
        with open(os.path.join(fastq_dir, "offpanel-barcodes.txt"), "w") as f:
            f.write("# feature barcodes injected into R2 that are NOT in tags.csv — the block must drop them\n")
            f.write("\n".join(off_bcs) + "\n")
    print(f"[antigen:{scenario}] {len(samples)} samples, {n_total} cells, {total_reads} reads -> {fastq_dir}")


def build_libraseq(cfg, out_dir):
    """LIBRA-seq / dual-probe fixture: one antigen (BG505) read out by TWO feature barcodes that must
    BOTH fire, alongside a single-barcode antigen (gp120) and a negative control. Exercises Feature
    Barcode Analysis's multi-barcode combine mode 'all' (AND): cells where only one BG505 probe barcode
    fires must NOT be called BG505.

    Antigen-only (no VDJ/GEX arm) — FI is antigen-only, so this alone drives the per-cell antigen call.
    Writes tags.csv WITH a `combine` column (BG505=all, gp120/control=sum). Read geometry is the BEAM
    default (R1 = 16 bp cell + 10 bp UMI; R2 = 15 bp feature at position 0), so the block's default
    preset + de-novo cell whitelist parse it directly.
    """
    rng = new_rng(cfg.seed)
    os.makedirs(out_dir, exist_ok=True)

    # BG505 = dual probe (two barcodes), gp120 = single, negative_control = single. Distinct 15-mers.
    bg505_a, bg505_b, gp120_bc, ctrl_bc = gen_distinct(new_rng(cfg.seed + 7), 4, FEAT_LEN, min_dist=3, avoid=[])
    feature_barcodes = {"BG505": [bg505_a, bg505_b], "gp120": [gp120_bc], "negative_control": [ctrl_bc]}
    combine_mode = {"BG505": "all", "gp120": "sum", "negative_control": "sum"}

    samples = cfg.samples
    n_total = len(samples) * cfg.cells_per_sample
    all_cells = gen_cells(rng, n_total)
    cells_by_sample = {
        s: all_cells[i * cfg.cells_per_sample : (i + 1) * cfg.cells_per_sample] for i, s in enumerate(samples)
    }

    read_no = [0]

    def emit(reads, sample, cell, barcode, k):
        """Emit k distinct-UMI reads (with light PCR dup) carrying `barcode` on R2 for `cell`."""
        umis = set()
        while len(umis) < k:
            umis.add(rand_seq(rng, UMI_LEN))
        for umi in sorted(umis):
            for _ in range(1 if rng.random() < 0.75 else 2):
                read_no[0] += 1
                reads.append([f"{sample}_read{read_no[0]}", cell + umi, barcode + R2_FILLER, 1])

    all_con = []
    total_reads = 0
    for sample in samples:
        reads = []
        for cell in cells_by_sample[sample]:
            roll = rng.random()
            dom = rng.randint(300, 900)
            if roll < 0.50:
                # BG505: BOTH probes fire -> called under AND
                emit(reads, sample, cell, bg505_a, dom)
                emit(reads, sample, cell, bg505_b, max(1, int(dom * rng.uniform(0.8, 1.1))))
                con = "BG505"
            elif roll < 0.68:
                # BG505: only ONE probe fires -> must be DROPPED under AND (the demonstration case)
                emit(reads, sample, cell, rng.choice([bg505_a, bg505_b]), dom)
                con = "BG505_singleprobe"
            elif roll < 0.93:
                emit(reads, sample, cell, gp120_bc, dom)
                con = "gp120"
            else:
                emit(reads, sample, cell, ctrl_bc, rng.randint(2, 5))
                con = "ambiguous"
            if rng.random() < 0.5:  # light control background on ~half the cells
                emit(reads, sample, cell, ctrl_bc, rng.randint(1, 3))
            all_con.append((sample, cell, con))
        rng.shuffle(reads)
        write_fastq_gz(os.path.join(out_dir, f"{sample}_R1.fastq.gz"), reads, 1)
        write_fastq_gz(os.path.join(out_dir, f"{sample}_R2.fastq.gz"), reads, 2)
        total_reads += len(reads)
        print(f"  {sample}: {len(cells_by_sample[sample])} cells, {len(reads)} reads")

    # tags.csv WITH a combine column (BG505=all, others=sum) — the block uploads this.
    with open(os.path.join(out_dir, "tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag", "feature", "combine"])
        for feat, member_bcs in feature_barcodes.items():
            for bc in member_bcs:
                w.writerow([bc, feat, combine_mode[feat]])
    with open(os.path.join(out_dir, "feature_reference.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "read", "pattern", "sequence", "feature_type"])
        for feat, member_bcs in feature_barcodes.items():
            for j, bc in enumerate(member_bcs):
                bc_id = f"{feat}_{j + 1}" if len(member_bcs) > 1 else feat
                w.writerow([bc_id, feat, "R2", "^(BC)", bc, "Antigen Capture"])
    with open(os.path.join(out_dir, "samples-metadata.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["Sample", "Donor", "Condition"])
        for i, s in enumerate(samples):
            w.writerow([s, f"Donor {i + 1}", "baseline" if i % 2 == 0 else "stimulated"])
    all_con.sort()
    with open(os.path.join(out_dir, "expected-consensus.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "planted_consensus"])
        w.writerows(all_con)

    n_single = sum(1 for c in all_con if c[2] == "BG505_singleprobe")
    print(f"[antigen:libraseq] {len(samples)} samples, {n_total} cells, {total_reads} reads -> {out_dir}")
    print(f"  BG505 dual-probe barcodes: {bg505_a} + {bg505_b} (combine=all); gp120 single; control")
    print(f"  {n_single} cells fire only ONE BG505 probe -> must be dropped from BG505 under 'all' mode")


def build_degraded(cfg, panel, out_dir):
    """QC-visualisation fixture (NOT a behavioural assertion): a few samples degraded to different
    levels so the block's Quality tag (OK/WARN/ALERT) and Read-recovery bar show a full spread."""
    rng = new_rng(cfg.seed)
    os.makedirs(out_dir, exist_ok=True)

    cells_per = 400  # small + fast; enough cells for stable fractions
    samples = [p[0] for p in DEGRADED_PROFILE]
    all_cells = gen_cells(rng, len(DEGRADED_PROFILE) * cells_per)
    off_bcs = gen_distinct(new_rng(cfg.seed + 99), 3, FEAT_LEN, min_dist=5, avoid=panel.barcodes)

    for i, (sample, m, p, tag) in enumerate(DEGRADED_PROFILE):
        cells = all_cells[i * cells_per : (i + 1) * cells_per]
        reads, _, _, _ = build_sample(rng, panel, sample, cells)
        reads = convert_offpanel(rng, reads, off_bcs, off_frac=1 - p)
        reads = add_malformed(rng, reads, matched_frac=m)
        rng.shuffle(reads)
        write_fastqs(out_dir, sample, reads, multilane=False)
        print(f"  {sample}: matched~{m:.0%}, panel-assigned~{p:.0%}, {len(reads)} reads -> expect {tag}")

    write_metadata(out_dir, panel, samples)
    print(f"[antigen:degraded] {len(samples)} samples, {cells_per} cells each -> {out_dir}")
