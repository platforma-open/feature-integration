"""GEX (gene-expression) arm generator.

Builds a synthetic single-cell count matrix per donor sharing the SAME cell barcodes as the antigen and
VDJ arms, so import-sc-rnaseq-data -> pl7.app/rna-seq/countMatrix feeds the OPTIONAL GEX input of
vdj-multiomic-integration and cell-type-annotation (CellTypist).

Coherence: each cell's expression program follows its planted antigen class, from the antigen arm's
expected-consensus.tsv. Clear-binder cells get a plasmablast/plasma program, with
MZB1/XBP1/PRDM1/CD38/TNFRSF17 high. Ambiguous cells get a naive-B program, with TCL1A/IGHD/IGHM high.
All cells are B lineage, since this is a CD19-sorted BEAM experiment.

Format, verified against import-sc-rnaseq-data: genes-in-rows CSV. The first column is real human
Ensembl IDs (`^ENSG\\d{11}$` -> species=human, gene-format=Ensembl auto-detected), the header is
bare-16nt cell barcodes, and the body is integer counts. import-sc-rnaseq-data's detect_orientation
TRANSPOSES a matrix whenever cells outnumber genes, so build() keeps the gene count strictly above the
largest per-donor cell count. Otherwise CellTypist reads barcodes as gene names and fails with "No
features overlap with the model".
"""

import csv
import math
import os
from collections import defaultdict

from .common import GEX_SEED, new_rng
from .panel import load_clear_antigens

# Marker programs (gene symbols). Means are per (program, cell class): (binder, naive).
PROGRAMS = {
    "housekeeping": (["ACTB", "GAPDH", "B2M", "MALAT1", "TMSB4X", "FTL", "FTH1", "EEF1A1", "HLA-A", "HLA-DRA"], (45, 40)),  # noqa: E501
    "b_core":       (["MS4A1", "CD19", "CD79A", "CD79B", "CD74", "BANK1", "CD27"], (20, 35)),
    "naive_b":      (["TCL1A", "IGHD", "IGHM"], (3, 42)),
    "plasma":       (["MZB1", "XBP1", "PRDM1", "SDC1", "TNFRSF17", "CD38", "IRF4", "JCHAIN", "IGHG1", "SLAMF7"], (65, 4)),  # noqa: E501
    "other_lineage":(["CD3D", "CD3E", "CD8A", "IL7R", "NKG7", "GNLY", "LYZ", "CD14", "FCGR3A", "S100A8", "S100A9"], (0.4, 0.4)),  # noqa: E501
}


def poisson(rng, lam):
    """Small counts via Knuth. Gaussian approximation for large lambda."""
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


def load_gene_map(annot_csv):
    """symbol -> Ensembl Id, and a pool of protein_coding Ensembl Ids for filler."""
    if not os.path.exists(annot_csv):
        annot_dir = os.path.dirname(annot_csv)
        raise SystemExit(
            f"missing {annot_csv}\nThis is the pipeline's human gene-annotations asset. Fetch it:\n"
            "  curl -sSL -o /tmp/hs.zip https://bin.pl-open.science/assets/platforma-open/"
            "milaboratories.gene-annotations.homo-sapiens/main/1.1.0.zip && unzip -o /tmp/hs.zip -d "
            f"{annot_dir}")
    sym2ens = {}
    protein_coding = []
    with open(annot_csv, newline="") as fh:
        for row in csv.reader(fh):
            if row[0] == "Ensembl Id":
                continue
            ens, sym, biotype = row[0], row[1], row[3] if len(row) > 3 else ""
            if sym and sym not in sym2ens:
                sym2ens[sym] = ens
            if biotype == "protein_coding":
                protein_coding.append(ens)
    return sym2ens, protein_coding


def read_cell_classes(consensus_tsv, clear_antigens):
    """{donor: [(cellId, 'binder'|'naive'), ...]} from the antigen ground truth."""
    by_donor = defaultdict(list)
    with open(consensus_tsv, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            cls = "binder" if row["planted_consensus"] in clear_antigens else "naive"
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
    # filler: real protein_coding genes, low baseline noise. Per-gene mean gives clustering texture
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


def build(tags_csv, consensus_tsv, out_dir, truth_dir, annot_csv, n_filler=1000, seed=GEX_SEED):
    """Build the GEX arm from the antigen arm's ground truth. Writes out_dir/<donor>.csv (genes-in-rows)
    + truth_dir/truth_cells_gex.csv. The bare donor id (no `_counts` suffix) keeps the shared sampleId."""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(truth_dir, exist_ok=True)
    rng = new_rng(seed)
    clear = load_clear_antigens(tags_csv)
    by_donor = read_cell_classes(consensus_tsv, clear)

    # Keep the gene count strictly above the largest per-donor cell count so import-sc-rnaseq-data
    # detects genes-in-rows (see the module docstring / the transpose guard).
    max_cells = max((len(cells) for cells in by_donor.values()), default=0)
    n_filler = max(n_filler, max_cells + 200)

    sym2ens, protein_coding = load_gene_map(annot_csv)
    genes = build_gene_table(rng, sym2ens, protein_coding, n_filler)
    if len(genes) <= max_cells:
        raise SystemExit(
            f"gene count {len(genes)} must exceed max per-donor cell count {max_cells} so "
            "import-sc-rnaseq-data detects genes-in-rows (see module docstring)")

    n_marker = sum(1 for _, p, _ in genes if p != "filler")
    print(f"[gex] {len(genes)} genes ({n_marker} real markers + {len(genes) - n_marker} filler)")
    truth_rows = []
    for donor in sorted(by_donor):
        cells = by_donor[donor]
        write_counts(os.path.join(out_dir, f"{donor}.csv"), genes, cells, rng)
        nb = sum(1 for _, c in cells if c == "binder")
        print(f"  {donor}: {len(cells)} cells ({nb} binder / {len(cells) - nb} naive) "
              f"-> gex/{donor}.csv ({len(genes)}x{len(cells)} genes-in-rows)")
        truth_rows += [(donor, c, cls) for c, cls in cells]
    with open(os.path.join(truth_dir, "truth_cells_gex.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["donor", "cellId", "gexClass"])
        w.writerows(truth_rows)
    print("  truth -> truth/truth_cells_gex.csv")
