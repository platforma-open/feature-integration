"""Generate the GEX (gene-expression) arm of the BEAM-Ab multiomics test — Tier 1.

Builds a synthetic single-cell count matrix per donor that shares the SAME cell barcodes as the
antigen + VDJ arms, so `import-sc-rnaseq-data` → `pl7.app/rna-seq/countMatrix` feeds the OPTIONAL
GEX input of `vdj-multiomic-integration` (per-clonotype expression) and the GEX analysis chain, and
`cell-type-annotation` (CellTypist) can label the cells.

Coherence: each cell's expression program follows its planted antigen class (from the antigen arm's
`expected-consensus.tsv`). Antigen-specific (clear-binder) cells get a plasmablast/plasma-cell
program (MZB1/XBP1/PRDM1/CD38/TNFRSF17 high); ambiguous cells get a naive-B program
(TCL1A/IGHD/IGHM high). All cells are B lineage (a CD19-sorted BEAM experiment), so T/NK/myeloid
markers stay near zero — CellTypist should call B / plasma lineages.

Format (verified against import-sc-rnaseq-data):
- genes-in-rows CSV: first column = gene IDs (real human Ensembl, `^ENSG\\d{11}$` → species=human,
  gene-format=Ensembl auto-detected, no mapping file needed); header = bare-16nt cell barcodes;
  body = integer counts. `detect_orientation` sees an all-numeric body and defaults to genes-in-rows.
- Every gene ID is REAL: markers looked up by symbol, filler sampled, from the pipeline's own asset
  `gex/homo_sapiens_gene_annotations.csv` (the same map cell-type-annotation uses Ensembl→symbol).

Run:  python3 generate_gex.py     (after generate_vdj.py; deterministic, stdlib only; gitignored)
"""

import argparse
import csv
import math
import random
from collections import defaultdict
from pathlib import Path

SEED = 6496
HERE = Path(__file__).resolve().parent
ANTIGEN_DIR = HERE.parent / "feature-integration-synthetic"
GEX_DIR = HERE / "gex"
ANNOT_CSV = GEX_DIR / "homo_sapiens_gene_annotations.csv"
N_FILLER = 300
CLEAR_ANTIGENS = {"SARS-TRI-S_WT", "Anti-Hen_Egg_Lysozyme", "gp120", "H5N1"}

# Marker programs (gene symbols). Means are per (program, cell class): (binder, naive).
PROGRAMS = {
    "housekeeping": (["ACTB", "GAPDH", "B2M", "MALAT1", "TMSB4X", "FTL", "FTH1", "EEF1A1", "HLA-A", "HLA-DRA"], (45, 40)),
    "b_core":       (["MS4A1", "CD19", "CD79A", "CD79B", "CD74", "BANK1", "CD27"], (20, 35)),
    "naive_b":      (["TCL1A", "IGHD", "IGHM"], (3, 42)),
    "plasma":       (["MZB1", "XBP1", "PRDM1", "SDC1", "TNFRSF17", "CD38", "IRF4", "JCHAIN", "IGHG1", "SLAMF7"], (65, 4)),
    "other_lineage":(["CD3D", "CD3E", "CD8A", "IL7R", "NKG7", "GNLY", "LYZ", "CD14", "FCGR3A", "S100A8", "S100A9"], (0.4, 0.4)),
}


def poisson(rng, lam):
    """Small counts via Knuth; gaussian approximation for large lambda."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    el, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= el:
            return k - 1


def load_gene_map():
    """symbol -> Ensembl Id, and a pool of protein_coding Ensembl Ids for filler."""
    if not ANNOT_CSV.exists():
        raise SystemExit(
            f"missing {ANNOT_CSV}\nThis is the pipeline's human gene-annotations asset. Fetch it:\n"
            "  curl -sSL -o /tmp/hs.zip https://bin.pl-open.science/assets/platforma-open/"
            "milaboratories.gene-annotations.homo-sapiens/main/1.1.0.zip && unzip -o /tmp/hs.zip -d "
            f"{GEX_DIR}")
    sym2ens = {}
    protein_coding = []
    with open(ANNOT_CSV, newline="") as fh:
        for row in csv.reader(fh):
            if row[0] == "Ensembl Id":
                continue
            ens, sym, biotype = row[0], row[1], row[3] if len(row) > 3 else ""
            if sym and sym not in sym2ens:
                sym2ens[sym] = ens
            if biotype == "protein_coding":
                protein_coding.append(ens)
    return sym2ens, protein_coding


def read_cell_classes(antigen_dir):
    """{donor: [(cellId, 'binder'|'naive'), ...]} from the antigen ground truth."""
    by_donor = defaultdict(list)
    with open(antigen_dir / "expected-consensus.tsv", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            cls = "binder" if row["planted_consensus"] in CLEAR_ANTIGENS else "naive"
            by_donor[row["sample"]].append((row["cellId"], cls))
    return by_donor


def build_gene_table(rng, sym2ens, protein_coding, n_filler):
    """Ordered [(ensId, program, (binderMean, naiveMean))]; markers first, then filler."""
    used = set()
    genes = []
    missing = []
    for prog, (symbols, means) in PROGRAMS.items():
        for sym in symbols:
            ens = sym2ens.get(sym)
            if not ens:
                missing.append(sym)
                continue
            if ens in used:
                continue
            used.add(ens)
            genes.append((ens, prog, means))
    if missing:
        print(f"  WARNING: markers not found in gene map (skipped): {missing}")
    # filler: real protein_coding genes, low baseline noise; per-gene mean gives clustering some texture
    pool = [e for e in protein_coding if e not in used]
    rng.shuffle(pool)
    for ens in pool[:n_filler]:
        base = rng.uniform(0.5, 4.0)
        genes.append((ens, "filler", (base, base)))
    return genes


def write_counts(path, genes, cells, rng):
    """genes-in-rows CSV: first column = Ensembl Id, header = cell barcodes, body = int counts."""
    barcodes = [c for c, _ in cells]
    classes = {c: cls for c, cls in cells}
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Ensembl Id"] + barcodes)
        for ens, prog, (m_binder, m_naive) in genes:
            rowvals = []
            for bc in barcodes:
                lam = m_binder if classes[bc] == "binder" else m_naive
                rowvals.append(poisson(rng, lam))
            w.writerow([ens] + rowvals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--realistic", action="store_true", help="alias for --profile realistic")
    ap.add_argument(
        "--profile", default="default", choices=["default", "realistic", "whitelist737k"],
        help="build on the matching antigen profile's consensus and write to gex/<profile>/. "
             "'realistic'/'whitelist737k' use ~1000 filler genes (more realistic genes/cell); "
             "whitelist737k = the 737K-august-2016-compliant chain. Default gex/ untouched.",
    )
    args = ap.parse_args()
    profile = "realistic" if args.realistic else args.profile
    subdir = None if profile == "default" else profile
    antigen_dir = ANTIGEN_DIR / subdir if subdir else ANTIGEN_DIR
    out_dir = GEX_DIR / subdir if subdir else GEX_DIR
    n_filler = N_FILLER if profile == "default" else 1000

    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    sym2ens, protein_coding = load_gene_map()
    genes = build_gene_table(rng, sym2ens, protein_coding, n_filler)
    by_donor = read_cell_classes(antigen_dir)

    n_marker = sum(1 for _, p, _ in genes if p != "filler")
    tag = profile
    print(f"Generated GEX arm ({tag}): {len(genes)} genes ({n_marker} real markers + {len(genes) - n_marker} filler)")
    truth_rows = []
    for donor in sorted(by_donor):
        cells = by_donor[donor]
        write_counts(out_dir / f"{donor}_counts.csv", genes, cells, rng)
        nb = sum(1 for _, c in cells if c == "binder")
        print(f"  {donor}: {len(cells)} cells ({nb} binder / {len(cells) - nb} naive) "
              f"-> {out_dir.name}/{donor}_counts.csv ({len(genes)}x{len(cells)} genes-in-rows)")
        truth_rows += [(donor, c, cls) for c, cls in cells]
    with open(out_dir / "truth_cells_gex.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["donor", "cellId", "gexClass"])
        w.writerows(truth_rows)
    print(f"  truth -> {out_dir.name}/truth_cells_gex.csv")


if __name__ == "__main__":
    main()
