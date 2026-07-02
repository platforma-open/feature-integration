"""Generate the VDJ (BCR) arm of the BEAM-Ab multiomics manual test — Tier 0.

The ANTIGEN arm already exists (realistic, mitool-validated) at
`../feature-integration-synthetic/` (2 donors, real 10x BEAM-Ab panel, ground-truth consensus).
This script builds a *coherent* single-cell BCR repertoire ON TOP of it: it reads each cell's
planted dominant antigen and groups cells into clonotypes so that a clonotype's cells bind the
same antigen — exactly the biology `vdj-multiomic-integration` is meant to surface (per-clonotype
antigen binding → antibody lead selection).

Output = one AIRR-`airr-sc` rearrangement TSV per donor (import-vdj-data, format "AIRR single
cell", cellKeyMode=direct → cell_id used verbatim). The cell_id is the SAME bare-16nt barcode the
antigen FASTQ carries — the canonical cellId that makes the convergence inner-join line up.

Run:  python3 generate_vdj.py
Deterministic (seeded). stdlib only. Everything here is gitignored.

Tier 0 = VDJ + antigen → vdj-multiomic-integration (feature + linker only; no GEX yet).
"""

import argparse
import csv
import random
from pathlib import Path

SEED = 6496
HERE = Path(__file__).resolve().parent
ANTIGEN_DIR = HERE.parent / "feature-integration-synthetic"
OUT_DIR = HERE / "vdj"

CLEAR_ANTIGENS = {"SARS-TRI-S_WT", "Anti-Hen_Egg_Lysozyme", "gp120", "H5N1"}
DOMINANT_FRACTION = 0.6  # fraction of a (donor, antigen) group that forms the lead clone

# Real IMGT gene names (human BCR). Light chain modeled as kappa for simplicity.
HEAVY_V = ["IGHV1-2", "IGHV1-69", "IGHV3-23", "IGHV3-30", "IGHV4-34", "IGHV4-59", "IGHV5-51"]
HEAVY_J = ["IGHJ3", "IGHJ4", "IGHJ5", "IGHJ6"]
HEAVY_C = ["IGHM", "IGHG1", "IGHG3", "IGHA1"]
KAPPA_V = ["IGKV1-5", "IGKV1-39", "IGKV2-28", "IGKV3-11", "IGKV3-20", "IGKV4-1"]
KAPPA_J = ["IGKJ1", "IGKJ2", "IGKJ3", "IGKJ4", "IGKJ5"]
KAPPA_C = ["IGKC"]

# Standard genetic code; '*' = stop. Sense codons are used for junction generation so every
# synthetic CDR3 is in-frame and productive (no stop).
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


def make_junction(rng: random.Random, n_codons: int) -> tuple[str, str]:
    """A conventional-looking CDR3: starts with Cys (TGT), ends with Trp (TGG), sense codons between."""
    mids = [rng.choice(SENSE_CODONS) for _ in range(n_codons - 2)]
    codons = ["TGT"] + mids + ["TGG"]
    nt = "".join(codons)
    aa = "".join(CODON_TABLE[c] for c in codons)
    return nt, aa


def make_bcr(rng: random.Random) -> dict:
    """One paired heavy+light rearrangement (a clonotype's sequences)."""
    return {
        "IGH": {
            "v": rng.choice(HEAVY_V), "j": rng.choice(HEAVY_J), "c": rng.choice(HEAVY_C),
            **dict(zip(("junction", "junction_aa"), make_junction(rng, rng.randint(12, 16)))),
        },
        "IGK": {
            "v": rng.choice(KAPPA_V), "j": rng.choice(KAPPA_J), "c": rng.choice(KAPPA_C),
            **dict(zip(("junction", "junction_aa"), make_junction(rng, rng.randint(9, 12)))),
        },
    }


def read_cells(antigen_dir) -> dict[str, list[tuple[str, str]]]:
    """{donor: [(cellId, plantedConsensus), ...]} from the antigen arm's ground truth."""
    path = antigen_dir / "expected-consensus.tsv"
    if not path.exists():
        raise SystemExit(f"antigen arm not found: {path}\nMove feature-integration-synthetic into place first.")
    by_donor: dict[str, list[tuple[str, str]]] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            by_donor.setdefault(row["sample"], []).append((row["cellId"], row["planted_consensus"]))
    return by_donor


def build_clones(rng: random.Random, cells: list[tuple[str, str]]) -> list[dict]:
    """Group a donor's cells into clonotypes. Clear-antigen cells → one lead clone (~60%) + singletons,
    all binding that antigen (coherent). Ambiguous cells → singleton clones (no clear target)."""
    by_antigen: dict[str, list[str]] = {}
    for cell_id, consensus in cells:
        by_antigen.setdefault(consensus, []).append(cell_id)

    clones = []
    cidx = 0
    for antigen, members in sorted(by_antigen.items()):
        rng.shuffle(members)
        is_clear = antigen in CLEAR_ANTIGENS
        if is_clear:
            n_lead = max(1, round(DOMINANT_FRACTION * len(members)))
            lead, rest = members[:n_lead], members[n_lead:]
            clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "lead",
                           "cells": lead, "bcr": make_bcr(rng)})
            cidx += 1
            # remaining clear-antigen cells: small clones of 1-3, still binding the same antigen
            i = 0
            while i < len(rest):
                take = rng.randint(1, 3)
                grp = rest[i:i + take]
                clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "minor",
                               "cells": grp, "bcr": make_bcr(rng)})
                cidx += 1
                i += take
        else:
            # ambiguous / control-dominant: each cell its own clone, no clear target
            for cell_id in members:
                clones.append({"id": f"clone{cidx}", "target": antigen, "kind": "background",
                               "cells": [cell_id], "bcr": make_bcr(rng)})
                cidx += 1
    return clones


def write_airr(path: Path, clones: list[dict], rng: random.Random) -> int:
    n_rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(AIRR_HEADER)
        for clone in clones:
            for cell_id in clone["cells"]:
                for locus in ("IGH", "IGK"):
                    chain = clone["bcr"][locus]
                    w.writerow([
                        cell_id, locus, chain["v"], chain["j"], chain["c"],
                        chain["junction"], chain["junction_aa"], "T", rng.randint(5, 60),
                    ])
                    n_rows += 1
    return n_rows


def write_truth(path: Path, all_clones: dict[str, list[dict]]):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["donor", "cloneId", "kind", "targetAntigen", "nCells",
                    "heavyV", "heavyJ", "heavyC", "lightV", "lightJ", "cdr3H_aa", "cdr3L_aa"])
        for donor, clones in all_clones.items():
            for c in clones:
                h, k = c["bcr"]["IGH"], c["bcr"]["IGK"]
                w.writerow([donor, c["id"], c["kind"], c["target"], len(c["cells"]),
                            h["v"], h["j"], h["c"], k["v"], k["j"], h["junction_aa"], k["junction_aa"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--realistic", action="store_true", help="alias for --profile realistic")
    ap.add_argument(
        "--profile", default="default", choices=["default", "realistic", "whitelist737k"],
        help="build on the matching antigen profile's consensus (feature-integration-synthetic/<profile>/) "
             "and write to vdj/<profile>/ — the default vdj/ output is untouched. whitelist737k = the "
             "737K-august-2016-compliant chain.",
    )
    args = ap.parse_args()
    profile = "realistic" if args.realistic else args.profile
    subdir = None if profile == "default" else profile
    antigen_dir = ANTIGEN_DIR / subdir if subdir else ANTIGEN_DIR
    out_dir = OUT_DIR / subdir if subdir else OUT_DIR

    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_donor = read_cells(antigen_dir)

    all_clones = {}
    print(f"Generated VDJ (airr-sc) arm ({profile}) on the antigen ground truth:")
    for donor in sorted(by_donor):
        clones = build_clones(rng, by_donor[donor])
        all_clones[donor] = clones
        n_rows = write_airr(out_dir / f"{donor}_airr_sc.tsv", clones, rng)
        n_cells = sum(len(c["cells"]) for c in clones)
        n_lead = sum(1 for c in clones if c["kind"] == "lead")
        print(f"  {donor}: {n_cells} cells, {len(clones)} clonotypes "
              f"({n_lead} lead) -> {n_rows} contig rows ({out_dir.name}/{donor}_airr_sc.tsv)")
    write_truth(out_dir / "truth_clonotypes.csv", all_clones)
    print(f"  truth -> {out_dir.name}/truth_clonotypes.csv")
    print(f"\nAntigen arm (reused): {antigen_dir}/{{donorA,donorB}}_R{{1,2}}.fastq.gz + tags.csv")


if __name__ == "__main__":
    main()
