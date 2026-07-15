"""BEAM-exact fixture: a small multiomic run whose SHAPE matches real-world BEAM antibody-barcode
libraries, so the full Feature Barcode Profiling -> VDJ Multiomic Integration -> Lead Selection chain can
be exercised FAST on a local backend instead of multi-GB reference FASTQs.

Two things distinguish this from the generic `realistic`/`multisample` presets, both modelled on
production BEAM libraries:

  1. R2 read geometry has a 10 bp OFFSET before the feature barcode:
         R1 = CELL(16) + UMI(10)
         R2 = OFFSET(10) + FEATURE(15) + tail        (real pattern: ^N{10}(FEATURE:N{15})(R2:*))
     The generic generators put the feature at R2 position 0; the real BEAM libraries carry a 10 bp
     lead-in, which is why the block runs with the `generic-fb-umi` preset, not `tenx-beam`.

  2. A genuinely SAMPLE-AWARE panel CSV (Sample,Sequence,Protein): each sample has its own antigen
     panel, and a couple of barcode SEQUENCES are REUSED across samples mapped to DIFFERENT proteins.
     That mirrors real multi-sample BEAM panels: the same 15-mer means one antigen in one sample's panel
     and a different one in another's. Without the Sample column the tag CSV then has one barcode on two rows with
     different proteins, which trips Feature Barcode Profiling's duplicate-barcode guard; WITH the Sample
     column the workflow filters the CSV per sample and each barcode is unique again.

Colocated with a coherent AIRR single-cell VDJ arm (reusing lib.vdj) so clonotypes bind their sample's
antigens and the convergence [sampleId, cellId] join lines up. Deterministic, standard-library only.
"""

import csv
import os

from . import vdj
from .common import (
    CONTROL_NAME,
    FEAT_LEN,
    R2_FILLER,
    UMI_LEN,
    gen_cells,
    gen_distinct,
    new_rng,
    rand_seq,
    write_fastq_gz,
)

BEAM_SEED = 20260714
R2_TAIL = "TTAATTAATT"  # neutral remainder after the feature barcode (captured by R2:* and ignored)


def build_sample_panels(rng, per_sample_size, n_shared_collisions):
    """Two per-sample antigen panels that SHARE `n_shared_collisions` barcode sequences under DIFFERENT
    protein names (the sample-aware twist), plus a control barcode common to both.

    Returns (panels, control_bc) where panels = {sample: [(protein, barcode), ...]} (control included).
    """
    total = per_sample_size * 2  # distinct barcodes across both panels before collisions are folded in
    seqs = gen_distinct(rng, total, FEAT_LEN, min_dist=3)
    control_bc = gen_distinct(rng, 1, FEAT_LEN, min_dist=3, avoid=seqs)[0]

    s1_seqs = seqs[:per_sample_size]
    s2_seqs = seqs[per_sample_size:]

    # Protein names carry the sample's panel identity: each sample's panel labels its own antigens.
    s1 = [(f"Panel A Antigen {i + 1}", bc) for i, bc in enumerate(s1_seqs)]
    s2 = [(f"Panel B Antigen {i + 1}", bc) for i, bc in enumerate(s2_seqs)]

    # Fold in the collisions: reuse the FIRST `n_shared_collisions` barcodes of sample1 in sample2, but
    # keep sample2's own protein name for them -> same 15-mer, two different proteins across samples.
    for i in range(n_shared_collisions):
        proto_name, _ = s2[i]
        s2[i] = (proto_name, s1_seqs[i])  # sample2 protein, sample1's barcode

    ctrl = (CONTROL_NAME, control_bc)
    return {"donor01": s1 + [ctrl], "donor02": s2 + [ctrl]}, control_bc


def assign_features(rng, antigens, control_name):
    """Plant per-antigen distinct-UMI counts for one cell: one dominant antigen (high), an optional
    ambiguous second, 0-2 low-count ambient antigens, light control background. Same magnitude shape as
    lib.antigen (calibrated to a typical 5k-cell BEAM library). Returns (per_feature, consensus_label)."""
    dominant = rng.choice(antigens)
    dom_umis = rng.randint(10, 60) if rng.random() < 0.1 else rng.randint(300, 1100)
    per_feature = {dominant: dom_umis}

    ambiguous = rng.random() < 0.12
    if ambiguous:
        second = rng.choice([a for a in antigens if a != dominant])
        per_feature[second] = max(1, int(dom_umis * rng.uniform(0.3, 0.9)))

    others = [a for a in antigens if a not in per_feature]
    rng.shuffle(others)
    for a in others[: rng.randint(0, 2)]:
        per_feature[a] = rng.randint(1, 3)

    if rng.random() < 0.7:
        per_feature[control_name] = rng.randint(1, 3)

    return per_feature, ("ambiguous" if ambiguous else dominant)


def build(run_dir, cells_per_sample=150, panel_size=12, n_shared_collisions=2, do_vdj=True):
    """Generate the BEAM-exact run under run_dir: antigen FASTQs (offset-10 R2), a sample-aware tag CSV,
    the coherent AIRR VDJ arm, and truth tables."""
    antigen_dir = os.path.join(run_dir, "antigen")
    vdj_dir = os.path.join(run_dir, "vdj")
    truth_dir = os.path.join(run_dir, "truth")
    for d in (antigen_dir, truth_dir):
        os.makedirs(d, exist_ok=True)

    rng = new_rng(BEAM_SEED)
    panels, _control_bc = build_sample_panels(rng, panel_size, n_shared_collisions)
    samples = ["donor01", "donor02"]

    all_cells = gen_cells(rng, len(samples) * cells_per_sample)
    cells_by_sample = {s: all_cells[i * cells_per_sample : (i + 1) * cells_per_sample] for i, s in enumerate(samples)}

    consensus_rows = []  # (sample, cellId, planted_consensus) — drives the VDJ arm's clonotype coherence
    csv_rows = []  # (Sample, Sequence, Protein) — the sample-aware tag CSV the block uploads
    for sample in samples:
        panel = panels[sample]  # [(protein, barcode), ...] incl. control
        by_protein = {name: bc for name, bc in panel}
        for name, bc in panel:
            csv_rows.append((sample, bc, name))
        antigen_names = [name for name, _ in panel if name != CONTROL_NAME]

        reads = []
        read_no = 0
        for cell in cells_by_sample[sample]:
            per_feature, con = assign_features(rng, antigen_names, CONTROL_NAME)
            consensus_rows.append((sample, cell, con))
            for feat, k in per_feature.items():
                bc = by_protein[feat]
                umis = set()
                while len(umis) < k:
                    umis.add(rand_seq(rng, UMI_LEN))
                for umi in sorted(umis):
                    dups = 1 if rng.random() < 0.75 else (2 if rng.random() < 0.8 else 3)
                    for _ in range(dups):
                        read_no += 1
                        # OFFSET-10 geometry: R2 = 10 bp lead-in + 15 bp feature + tail (feature at pos 10).
                        r2 = R2_FILLER + bc + R2_TAIL
                        reads.append([f"{sample}_read{read_no}", cell + umi, r2, 1])
        rng.shuffle(reads)
        write_fastq_gz(os.path.join(antigen_dir, f"{sample}_R1.fastq.gz"), reads, 1)
        write_fastq_gz(os.path.join(antigen_dir, f"{sample}_R2.fastq.gz"), reads, 2)
        print(f"  {sample}: {len(cells_by_sample[sample])} cells, {len(reads)} reads")

    # Sample-aware tag CSV (the block uploads this; sampleColumn=Sample makes each barcode unique/sample).
    tags_csv = os.path.join(run_dir, "tags.csv")
    with open(tags_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Sample", "Sequence", "Protein"])
        w.writerows(csv_rows)

    # Consensus truth (also the VDJ arm's input).
    consensus_tsv = os.path.join(truth_dir, "expected-consensus.tsv")
    consensus_rows.sort()
    with open(consensus_tsv, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "cellId", "planted_consensus"])
        w.writerows(consensus_rows)

    # vdj.build needs a `feature`-column CSV for load_clear_antigens; write a plain flat view for it.
    vdj_tags = os.path.join(run_dir, "_vdj_tags.csv")
    with open(vdj_tags, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag", "feature"])
        for _sample, seq, protein in csv_rows:
            w.writerow([seq, protein])

    if do_vdj:
        vdj.build(vdj_tags, consensus_tsv, out_dir=vdj_dir, truth_dir=truth_dir)

    n_collide = n_shared_collisions
    print(
        f"[beam-exact] {len(samples)} samples x {cells_per_sample} cells x {panel_size} antigens "
        f"(+control), {n_collide} cross-sample barcode collisions -> {run_dir}"
    )
    print(f"  tag CSV: {tags_csv} (Sample,Sequence,Protein — sample-aware)")
    print("  R2 geometry: OFFSET(10) + FEATURE(15) + tail  (pattern ^N{10}(FEATURE:N{15})(R2:*))")
