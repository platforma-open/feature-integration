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
    — the shape a VHH single-domain antibody library produces) when heavy_only is set."""
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


def _clone_sizes(n, mean_size=25, singleton_cell_frac=0.10, alpha=0.9, tail_cycle=None):
    """Clone sizes for `n` cells of one (donor, antigen) group, shaped like an IMMUNIZED, ANTIGEN-SORTED
    repertoire — which is the only kind this bed's blocks are pointed at.

    HOW MUCH EXPANSION IS A MEASURED QUESTION, AND THE ANSWER DEPENDS ON THE LIBRARY. Read this before
    changing a default here.

    The argument from first principles says expansion should be everywhere: immunization drives a
    germinal-centre response, sorting for antigen-positive cells enriches the expanded families, and
    expansion is the signal being looked for. That argument is why this function replaced an earlier
    power-law version whose tail made ~97% of clonotypes singletons.

    Real in-vivo BEAM libraries do not agree with it. Characterised 2026-08-21: 4,549 IGHeavy
    clonotypes over 4,773 cells with paired chains, and 3,707 over 3,716 — about 1.05 cells per
    clonotype, essentially all singletons. The public figures that supported the expanded shape came
    from libraries that cannot carry the argument: two 10x BEAM-T runs holding deliberately expanded
    spike-in populations, a transgenic monoclonal control, and one literature ratio formed by dividing a
    FILTERED antigen-labelled clone count by a FULL cell count.

    So both shapes are real and neither is the default for every run. `mean_size`,
    `singleton_cell_frac` and `tail_cycle` carry the difference; the regime tables in realpanel.py set
    them. At shallow depth a clonotype's verdict DOES rest on one cell almost everywhere, which is a
    fact about the data rather than a defect in the bed — the per-clonotype agreement rules go
    unexercised because that real data does not exercise them either.

    Two compartments, because a real sorted sample has both:
      - an EXPANDED compartment holding `1 - singleton_cell_frac` of the cells, split into clones whose
        sizes follow `i**-alpha` and average `mean_size`. A heavy head — the leads — and a graded tail.
      - a SMALL-CLONE tail holding the rest: mostly one-cell clonotypes, mixed with 2-4 cell ones. The
        tail is NOT all singletons on purpose — a distribution that jumps from a wall of 1s straight to
        clones of 7+ has a hole in it where real data is dense, and that hole is visible the moment
        anyone sorts a clonotype table by cell count.
    Singletons stay a large share of CLONOTYPES and a small share of CELLS, which is what the real
    distribution looks like.

    Sizes sum to exactly `n` (largest-remainder apportionment): a size list that does not account for
    every cell drops or duplicates cells, and the cross-arm join then loses them silently."""
    if n <= 0:
        return []
    n_single = int(round(n * singleton_cell_frac))
    n_exp = n - n_single
    if n_exp < 2:
        return _tail_sizes(n, tail_cycle)

    k = max(1, int(round(n_exp / max(2.0, mean_size))))
    weights = [(i + 1) ** -alpha for i in range(k)]
    total_w = sum(weights)
    raw = [n_exp * w / total_w for w in weights]
    sizes = [int(x) for x in raw]
    # Largest-remainder: hand the rounding shortfall to the clones with the biggest fractional parts, so
    # the list sums to n_exp exactly without a correction loop that can stall against the min-size floor.
    short = n_exp - sum(sizes)
    for i in sorted(range(k), key=lambda i: -(raw[i] - sizes[i]))[:short]:
        sizes[i] += 1
    # A clone in the expanded compartment holds at least 2 cells. Fold anything smaller into the head so
    # the total is preserved. (A 1-cell clone belongs to the singleton tail, which is counted separately.)
    runts = sum(x for x in sizes if x < 2)
    sizes = [x for x in sizes if x >= 2]
    if not sizes:
        sizes = [n_exp]
    elif runts:
        sizes[0] += runts

    return sizes + _tail_sizes(n_single, tail_cycle)


# Sizes for the small-clone tail, by position. Mostly 1s with 2s, 3s and a 4 mixed through: 22 cells
# across 15 clonotypes, so two thirds of the tail's clonotypes are singletons and the rest fill the
# 2-4 band. Positional rather than drawn, so the tail is reproducible without touching the caller's RNG.
TAIL_CYCLE = (1, 1, 1, 2, 1, 1, 1, 3, 1, 2, 1, 1, 4, 1, 2)

# The tail real in-vivo libraries measure: 21 cells across 20 clonotypes, so 1.05 cells per
# clonotype. TAIL_CYCLE averages 1.47 and is therefore a FLOOR no combination of `mean_size` and
# `singleton_cell_frac` can get under — which is why this exists as a separate cycle rather than as
# another parameter setting.
TAIL_CYCLE_SPARSE = (1,) * 19 + (2,)


def _tail_sizes(budget, cycle=None):
    """Clone sizes summing to exactly `budget`, following `cycle` and truncating the last clone to fit."""
    cycle = cycle or TAIL_CYCLE
    out, left, i = [], budget, 0
    while left > 0:
        size = min(cycle[i % len(cycle)], left)
        out.append(size)
        left -= size
        i += 1
    return out


def build_clones(rng, cells, clear_antigens, heavy_only=False, clonal_profile="lead",
                 mean_size=25, singleton_cell_frac=0.10, tail_cycle=None):
    """Group a donor's cells into clonotypes. Clear-antigen cells -> one lead clone (~60%) + singletons,
    all binding that antigen (coherent). Ambiguous cells -> singleton clones (no clear target).

    `clonal_profile="immunized"` replaces the one-lead-clone split with the size distribution an
    immunized, antigen-sorted repertoire has (see _clone_sizes). The default "lead" is the original
    behaviour, so every existing preset stays byte-identical."""
    by_antigen = {}
    for cell_id, consensus in cells:
        by_antigen.setdefault(consensus, []).append(cell_id)

    clones = []
    cidx = 0
    for antigen, members in sorted(by_antigen.items()):
        rng.shuffle(members)
        # "crossreactive" is not a panel feature name, so it is not in `clear_antigens` — but a
        # cross-reactive clone is a real and interesting lead, and leaving those cells as singletons
        # means no CLONOTYPE is ever cross-reactive with more than one cell agreeing.
        is_clear = antigen in clear_antigens or antigen == "crossreactive"
        if is_clear and clonal_profile == "immunized":
            for size in _clone_sizes(len(members), mean_size, singleton_cell_frac,
                                     tail_cycle=tail_cycle):
                grp, members = members[:size], members[size:]
                kind = "expanded" if size >= 10 else ("minor" if size > 1 else "singleton")
                clones.append({"id": f"clone{cidx}", "target": antigen, "kind": kind,
                               "cells": grp, "bcr": make_bcr(rng, heavy_only)})
                cidx += 1
        elif is_clear:
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


def write_airr(path, clones, rng, heavy_only=False, unpaired_frac=0.0):
    """Write one donor's AIRR single-cell rows.

    `unpaired_frac` is the share of cells that emit their HEAVY chain only, standing in for the cells a
    real run recovers one chain from. It is not cosmetic: in the two measured libraries the clonotypes
    dropped for want of a pair OUTNUMBERED the paired ones (7,732 against 4,549; 23,127 against 3,707),
    and a bed where every cell pairs perfectly never exercises the drop. Heavy is the chain kept because
    heavy is the chain a VHH library has.

    Ignored when `heavy_only` is set — there is no pair to break."""
    loci = ("IGH",) if heavy_only else ("IGH", "IGK")
    n_rows = 0
    n_unpaired = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(AIRR_HEADER)
        for clone in clones:
            for cell_id in clone["cells"]:
                cell_loci = loci
                if not heavy_only and unpaired_frac > 0 and rng.random() < unpaired_frac:
                    cell_loci = ("IGH",)
                    n_unpaired += 1
                for locus in cell_loci:
                    chain = clone["bcr"][locus]
                    w.writerow([
                        cell_id, locus, chain["v"], chain["j"], chain["c"],
                        chain["junction"], chain["junction_aa"], "T", rng.randint(5, 60),
                    ])
                    n_rows += 1
    return n_rows, n_unpaired


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


def build(tags_csv, consensus_tsv, out_dir, truth_dir, seed=VDJ_SEED, heavy_only=False,
          clonal_profile="lead", mean_size=25, singleton_cell_frac=0.10, unpaired_frac=0.0,
          tail_cycle=None):
    """Build the VDJ arm from the antigen arm's ground truth. Writes out_dir/<donor>.tsv (AIRR-sc) +
    truth_dir/truth_clonotypes.csv. The filename stem is the bare donor id so Samples & Data mints ONE
    shared sampleId across all three arms — a per-library suffix would fork the donor into separate
    samples and the convergence [sampleId,cellId] join would then match nothing.

    heavy_only=True emits HEAVY-CHAIN-ONLY (IGH, no IGK) rearrangements — the shape a VHH single-domain
    antibody library produces — so the heavy-only end-to-end path is reproducible synthetically. Each cell
    keeps the SAME bare-16nt cell_id it carries in the antigen arm (the convergence join key)."""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(truth_dir, exist_ok=True)
    rng = new_rng(seed)
    by_donor = read_cells(consensus_tsv)
    clear = load_clear_antigens(tags_csv)

    all_clones = {}
    print(f"[vdj] airr-sc arm on the antigen ground truth "
          f"({len(by_donor)} donors, {len(clear)} clear antigens, {clonal_profile} clonality"
          f"{', heavy-only' if heavy_only else ''}):")
    for donor in sorted(by_donor):
        clones = build_clones(rng, by_donor[donor], clear, heavy_only, clonal_profile,
                              mean_size, singleton_cell_frac, tail_cycle)
        all_clones[donor] = clones
        n_rows, n_unpaired = write_airr(os.path.join(out_dir, f"{donor}.tsv"), clones, rng, heavy_only,
                                        unpaired_frac)
        n_cells = sum(len(c["cells"]) for c in clones)
        n_lead = sum(1 for c in clones if c["kind"] in ("lead", "expanded"))
        per_clone = n_cells / max(1, len(clones))
        unp = f", {n_unpaired} heavy-only" if n_unpaired else ""
        print(f"  {donor}: {n_cells} cells, {len(clones)} clonotypes "
              f"({n_lead} lead, {per_clone:.2f} cells/clonotype{unp}) -> {n_rows} contig rows "
              f"(vdj/{donor}.tsv)")
    write_truth(os.path.join(truth_dir, "truth_clonotypes.csv"), all_clones, heavy_only)
    print("  truth -> truth/truth_clonotypes.csv")
