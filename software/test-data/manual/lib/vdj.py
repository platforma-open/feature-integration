"""VDJ (BCR, single-cell) arm generator.

Builds a *coherent* single-cell BCR repertoire ON TOP of the antigen arm: it reads each cell's planted
dominant antigen (from the antigen arm's expected-consensus.tsv) and groups cells into clonotypes so a
clonotype's cells bind the same antigen — exactly the biology vdj-multiomic-integration surfaces
(per-clonotype antigen binding -> antibody lead selection).

Output = one AIRR-`airr-sc` rearrangement TSV per donor (import-vdj-data, format "AIRR single cell",
cellKeyMode=direct -> cell_id used verbatim). cell_id is the SAME bare-16nt barcode the antigen FASTQ
carries — the canonical cellId the convergence inner-join lines up on.
"""

import csv
import os

from .common import VDJ_SEED, new_rng
from .panel import load_clear_antigens

DOMINANT_FRACTION = 0.6  # fraction of a (donor, antigen) group that forms the lead clone

# Real IMGT gene names (human BCR). Light chain modeled as kappa for simplicity.
HEAVY_V = ["IGHV1-2", "IGHV1-69", "IGHV3-23", "IGHV3-30", "IGHV4-34", "IGHV4-59", "IGHV5-51"]
HEAVY_J = ["IGHJ3", "IGHJ4", "IGHJ5", "IGHJ6"]
HEAVY_C = ["IGHM", "IGHG1", "IGHG3", "IGHA1"]
KAPPA_V = ["IGKV1-5", "IGKV1-39", "IGKV2-28", "IGKV3-11", "IGKV3-20", "IGKV4-1"]
KAPPA_J = ["IGKJ1", "IGKJ2", "IGKJ3", "IGKJ4", "IGKJ5"]
KAPPA_C = ["IGKC"]

# Standard genetic code; '*' = stop. Sense codons are used for junction generation so every synthetic
# CDR3 is in-frame and productive (no stop).
CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T", "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}
SENSE_CODONS = [c for c, aa in CODON_TABLE.items() if aa != "*"]

AIRR_HEADER = [
    "cell_id", "locus", "v_call", "j_call", "c_call",
    "junction", "junction_aa", "productive", "duplicate_count",
]


def read_cells(consensus_tsv):
    """{donor: [(cellId, plantedConsensus), ...]} from the antigen arm's ground truth."""
    if not os.path.exists(consensus_tsv):
        raise SystemExit(f"antigen arm not found: {consensus_tsv}\nGenerate the antigen arm first.")
    by_donor = {}
    with open(consensus_tsv, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            by_donor.setdefault(row["sample"], []).append((row["cellId"], row["planted_consensus"]))
    return by_donor


def make_junction(rng, n_codons):
    """A conventional-looking CDR3: starts with Cys (TGT), ends with Trp (TGG), sense codons between."""
    mids = [rng.choice(SENSE_CODONS) for _ in range(n_codons - 2)]
    codons = ["TGT"] + mids + ["TGG"]
    nt = "".join(codons)
    aa = "".join(CODON_TABLE[c] for c in codons)
    return nt, aa


def make_bcr(rng, heavy_only=False):
    """One rearrangement's sequences: paired heavy+light by default, heavy-only (IGH, no light chain
    — the customer's VHH single-domain antibody) when heavy_only is set."""
    bcr = {
        "IGH": {
            "v": rng.choice(HEAVY_V), "j": rng.choice(HEAVY_J), "c": rng.choice(HEAVY_C),
            **dict(zip(("junction", "junction_aa"), make_junction(rng, rng.randint(12, 16)))),
        },
    }
    if not heavy_only:
        bcr["IGK"] = {
            "v": rng.choice(KAPPA_V), "j": rng.choice(KAPPA_J), "c": rng.choice(KAPPA_C),
            **dict(zip(("junction", "junction_aa"), make_junction(rng, rng.randint(9, 12)))),
        }
    return bcr


def build_clones(rng, cells, clear_antigens, heavy_only=False):
    """Group a donor's cells into clonotypes. Clear-antigen cells -> one lead clone (~60%) + singletons,
    all binding that antigen (coherent). Ambiguous cells -> singleton clones (no clear target)."""
    by_antigen = {}
    for cell_id, consensus in cells:
        by_antigen.setdefault(consensus, []).append(cell_id)

    clones = []
    cidx = 0
    for antigen, members in sorted(by_antigen.items()):
        rng.shuffle(members)
        is_clear = antigen in clear_antigens
        if is_clear:
            n_lead = max(1, round(DOMINANT_FRACTION * len(members)))
            lead, rest = members[:n_lead], members[n_lead:]
            clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "lead",
                           "cells": lead, "bcr": make_bcr(rng, heavy_only)})
            cidx += 1
            # remaining clear-antigen cells: small clones of 1-3, still binding the same antigen
            i = 0
            while i < len(rest):
                take = rng.randint(1, 3)
                grp = rest[i:i + take]
                clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "minor",
                               "cells": grp, "bcr": make_bcr(rng, heavy_only)})
                cidx += 1
                i += take
        else:
            # ambiguous / control-dominant: each cell its own clone, no clear target
            for cell_id in members:
                clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "background",
                               "cells": [cell_id], "bcr": make_bcr(rng, heavy_only)})
                cidx += 1
    return clones


def write_airr(path, clones, rng, heavy_only=False):
    loci = ("IGH",) if heavy_only else ("IGH", "IGK")
    n_rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(AIRR_HEADER)
        for clone in clones:
            for cell_id in clone["cells"]:
                for locus in loci:
                    chain = clone["bcr"][locus]
                    w.writerow([
                        cell_id, locus, chain["v"], chain["j"], chain["c"],
                        chain["junction"], chain["junction_aa"], "T", rng.randint(5, 60),
                    ])
                    n_rows += 1
    return n_rows


def write_truth(path, all_clones, heavy_only=False):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["donor", "cloneId", "kind", "targetAntigen", "nCells",
                    "heavyV", "heavyJ", "heavyC", "lightV", "lightJ", "cdr3H_aa", "cdr3L_aa"])
        for donor, clones in all_clones.items():
            for c in clones:
                h = c["bcr"]["IGH"]
                k = c["bcr"].get("IGK") if heavy_only else c["bcr"]["IGK"]
                if k is None:
                    w.writerow([donor, c["id"], c["kind"], c["target"], len(c["cells"]),
                                h["v"], h["j"], h["c"], "", "", h["junction_aa"], ""])
                else:
                    w.writerow([donor, c["id"], c["kind"], c["target"], len(c["cells"]),
                                h["v"], h["j"], h["c"], k["v"], k["j"], h["junction_aa"], k["junction_aa"]])


def build(tags_csv, consensus_tsv, out_dir, truth_dir, seed=VDJ_SEED, heavy_only=False):
    """Build the VDJ arm from the antigen arm's ground truth. Writes out_dir/<donor>.tsv (AIRR-sc) +
    truth_dir/truth_clonotypes.csv. The filename stem is the bare donor id so Samples & Data mints ONE
    shared sampleId across all three arms — a per-library suffix would fork the donor into separate
    samples and the convergence [sampleId,cellId] join would then match nothing.

    heavy_only=True emits HEAVY-CHAIN-ONLY (IGH, no IGK) rearrangements — the customer's VHH
    single-domain antibody — so the heavy-only end-to-end path is reproducible synthetically. Each cell
    keeps the SAME bare-16nt cell_id it carries in the antigen arm (the convergence join key)."""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(truth_dir, exist_ok=True)
    rng = new_rng(seed)
    by_donor = read_cells(consensus_tsv)
    clear = load_clear_antigens(tags_csv)

    all_clones = {}
    print(f"[vdj] airr-sc arm on the antigen ground truth "
          f"({len(by_donor)} donors, {len(clear)} clear antigens{', heavy-only' if heavy_only else ''}):")
    for donor in sorted(by_donor):
        clones = build_clones(rng, by_donor[donor], clear, heavy_only)
        all_clones[donor] = clones
        n_rows = write_airr(os.path.join(out_dir, f"{donor}.tsv"), clones, rng, heavy_only)
        n_cells = sum(len(c["cells"]) for c in clones)
        n_lead = sum(1 for c in clones if c["kind"] == "lead")
        print(f"  {donor}: {n_cells} cells, {len(clones)} clonotypes "
              f"({n_lead} lead) -> {n_rows} contig rows (vdj/{donor}.tsv)")
    write_truth(os.path.join(truth_dir, "truth_clonotypes.csv"), all_clones, heavy_only)
    print("  truth -> truth/truth_clonotypes.csv")
