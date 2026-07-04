"""Offline viability suite for the BEAM-Ab multiomics manual test (Tier 0: VDJ + antigen).

Proves the synthetic data will actually flow through the pipeline BEFORE any backend run — the #1
failure is the convergence inner-join producing empty output because cell barcodes don't line up
across arms. Runs three things:

  1. Per-arm schema/geometry checks (antigen FASTQ, VDJ AIRR-sc TSV, panel).
  2. Barcode alignment across arms (the load-bearing test).
  3. JOIN SIMULATION — emulate vdj-multiomic-integration end to end, offline:
       - derive per-(cell, antigen) distinct-UMI from the antigen FASTQ (what mitool tag-stat -u +
         feature-integration compute),
       - build the cell->clonotype linker from the VDJ pairing (what import-vdj-data emits),
       - inner-join on cellId, group by clonotype, take the dominant antigen,
       - assert every clear-antigen clonotype's dominant == the planted antigen, and output non-empty.

Run:  python3 validate_multiomics.py    (exit 0 = all pass; stdlib only)
"""

import argparse
import csv
import gzip
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANTIGEN_DIR = HERE.parent / "antigen"
VDJ_DIR = HERE / "vdj"
GEX_DIR = HERE / "gex"
CONTROL_NAME = "negative_control"
CELL_LEN, UMI_LEN, FEATURE_LEN = 16, 10, 15
ENSG_RE = re.compile(r"^ENSG\d{11}$")
# Verified plasma-cell markers (from the pipeline's homo_sapiens gene map): MZB1, XBP1, PRDM1, CD38,
# TNFRSF17. High in antigen-specific (plasmablast) cells, low in naive — the GEX coherence signal.
PLASMA_ENSG = {"ENSG00000170476", "ENSG00000100219", "ENSG00000057657", "ENSG00000004468", "ENSG00000048462"}

results = []  # (ok, label, detail)


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def load_panel():
    tags = {}
    with open(ANTIGEN_DIR / "tags.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            tags[row["tag"]] = row["feature"]
    return tags


def load_planted_consensus():
    cons = {}
    with open(ANTIGEN_DIR / "expected-consensus.tsv", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            cons[(row["sample"], row["cellId"])] = row["planted_consensus"]
    return cons


def read_fastq(path):
    with gzip.open(path, "rt") as f:
        while True:
            h = f.readline()
            if not h:
                break
            seq = f.readline().strip()
            f.readline()
            f.readline()
            yield h[1:].strip(), seq


def derive_antigen_umis(donor, panel):
    """Per-(cell, antigen) distinct-UMI, re-derived from the FASTQ (mirrors tag-stat -u + the block).
    Streams R1/R2 in lockstep instead of materializing them — at cohort scale the FASTQs are millions
    of reads each."""
    g1 = read_fastq(ANTIGEN_DIR / f"{donor}_R1.fastq.gz")
    g2 = read_fastq(ANTIGEN_DIR / f"{donor}_R2.fastq.gz")
    molecules = defaultdict(set)  # (cell, antigen) -> {umi}
    cells = set()
    off_panel = 0
    reads = 0
    geom_ok = True
    for (n1, s1), (n2, s2) in zip(g1, g2):
        reads += 1
        if n1 != n2 or len(s1) != CELL_LEN + UMI_LEN or len(s2) < FEATURE_LEN:
            geom_ok = False
        cell, umi, feat = s1[:CELL_LEN], s1[CELL_LEN:CELL_LEN + UMI_LEN], s2[:FEATURE_LEN]
        cells.add(cell)
        antigen = panel.get(feat)
        if antigen is None:
            off_panel += 1
            continue
        molecules[(cell, antigen)].add(umi)
    # zip stops at the shorter stream; unequal read counts (R1 vs R2) => geometry mismatch
    if next(g1, None) is not None or next(g2, None) is not None:
        geom_ok = False
    umi_counts = {k: len(v) for k, v in molecules.items()}
    return {"cells": cells, "reads": reads, "geom_ok": geom_ok,
            "off_panel": off_panel, "umi_counts": umi_counts}


def load_vdj(donor):
    """Parse the AIRR-sc TSV; return per-cell clone key + schema stats."""
    path = VDJ_DIR / f"{donor}.tsv"
    required = {"cell_id", "junction", "v_call", "j_call", "duplicate_count"}
    per_cell = defaultdict(lambda: {"IGH": [], "IGK": []})
    bad_junction = 0
    bad_count = 0
    bad_gene = 0
    with open(path, newline="") as fh:
        r = csv.DictReader(fh, delimiter="\t")
        header_ok = required.issubset(set(r.fieldnames or []))
        for row in r:
            loc = row["locus"]
            per_cell[row["cell_id"]].setdefault(loc, []).append(row)
            j = row["junction"]
            if any(c not in "ACGT" for c in j) or len(j) % 3 != 0:
                bad_junction += 1
            try:
                if int(row["duplicate_count"]) < 1:
                    bad_count += 1
            except ValueError:
                bad_count += 1
            if loc == "IGH" and not row["v_call"].startswith("IGH"):
                bad_gene += 1
            if loc == "IGK" and not row["v_call"].startswith("IGK"):
                bad_gene += 1
    # clone key per cell: paired heavy+light (v, j, junction)
    clone_key = {}
    unpaired = 0
    for cell, chains in per_cell.items():
        if not chains["IGH"] or not chains["IGK"]:
            unpaired += 1
            continue
        h, k = chains["IGH"][0], chains["IGK"][0]
        clone_key[cell] = (h["v_call"], h["j_call"], h["junction"],
                           k["v_call"], k["j_call"], k["junction"])
    return {"cells": set(per_cell), "clone_key": clone_key, "header_ok": header_ok,
            "bad_junction": bad_junction, "bad_count": bad_count, "bad_gene": bad_gene,
            "unpaired": unpaired}


def load_gex(donor):
    """Parse the genes-in-rows count CSV; return cell set, per-cell plasma-marker mean, validity."""
    with open(GEX_DIR / f"{donor}.csv", newline="") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    barcodes = [b.split("-")[0] for b in header[1:]]  # strip any -N suffix (import-sc-rnaseq does)
    gene_ids = [r[0] for r in rows[1:]]
    ensg_ok = all(ENSG_RE.match(g) for g in gene_ids)
    body_ok = True
    cell_total = {bc: 0 for bc in barcodes}
    plasma_sum = {bc: 0.0 for bc in barcodes}
    plasma_n = 0
    for r in rows[1:]:
        try:
            vals = [int(x) for x in r[1:]]
        except ValueError:
            body_ok = False
            continue
        if any(v < 0 for v in vals):
            body_ok = False
        for bc, v in zip(barcodes, vals):
            cell_total[bc] += v
        if r[0] in PLASMA_ENSG:
            plasma_n += 1
            for bc, v in zip(barcodes, vals):
                plasma_sum[bc] += v
    plasma_mean = {bc: (plasma_sum[bc] / plasma_n if plasma_n else 0.0) for bc in barcodes}
    return {"cells": set(barcodes), "plasma_mean": plasma_mean, "ensg_ok": ensg_ok,
            "body_ok": body_ok, "no_empty_cell": all(t > 0 for t in cell_total.values()),
            "n_genes": len(gene_ids), "plasma_n": plasma_n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--realistic", action="store_true", help="alias for --profile realistic")
    ap.add_argument("--profile", default="default", choices=["default", "realistic", "whitelist737k"],
                    help="validate the given profile chain (antigen/<profile> + "
                         "vdj/<profile> + gex/<profile>)")
    args = ap.parse_args()
    profile = "realistic" if args.realistic else args.profile
    if profile != "default":
        global ANTIGEN_DIR, VDJ_DIR, GEX_DIR
        ANTIGEN_DIR = ANTIGEN_DIR / profile
        VDJ_DIR = VDJ_DIR / profile
        GEX_DIR = GEX_DIR / profile
        print(f"(validating {profile.upper()} profile chain)\n")
    panel = load_panel()
    planted = load_planted_consensus()
    # donors + clear antigens are DERIVED from the data (no hardcoded 2-donor / 4-antigen assumptions)
    donors = sorted({sample for sample, _cell in planted})
    clear_antigens = {f for f in panel.values() if f != CONTROL_NAME}
    print(f"({len(donors)} donors, {len(panel)} panel features, {len(clear_antigens)} clear antigens)\n")

    # Panel sanity: barcodes pairwise Hamming >= 3
    seqs = list(panel)
    min_h = min((hamming(seqs[i], seqs[j]) for i in range(len(seqs)) for j in range(i + 1, len(seqs))),
                default=99)
    check(min_h >= 3, "panel feature barcodes pairwise Hamming >= 3", f"min={min_h}, {len(seqs)} barcodes")
    check(CONTROL_NAME in panel.values(), "panel includes a negative control")

    total_clono = 0
    total_clear = 0
    join_mismatches = []

    for donor in donors:
        print(f"\n{donor}:")
        ag = derive_antigen_umis(donor, panel)
        vdj = load_vdj(donor)

        check(ag["geom_ok"], f"{donor} antigen FASTQ geometry (R1=26, R2>=15, paired)",
              f"{ag['reads']} read pairs")
        check(ag["off_panel"] == 0, f"{donor} all antigen reads on-panel", f"off-panel={ag['off_panel']}")
        check(vdj["header_ok"], f"{donor} VDJ has required AIRR-sc columns")
        check(vdj["bad_junction"] == 0, f"{donor} VDJ junctions valid (ACGT, in-frame)", f"bad={vdj['bad_junction']}")
        check(vdj["bad_gene"] == 0, f"{donor} VDJ v_call locus-consistent", f"bad={vdj['bad_gene']}")
        check(vdj["bad_count"] == 0, f"{donor} VDJ duplicate_count >= 1", f"bad={vdj['bad_count']}")
        check(vdj["unpaired"] == 0, f"{donor} every VDJ cell paired (IGH + IGK)", f"unpaired={vdj['unpaired']}")

        # Barcode alignment: every VDJ cell must have antigen reads (else it drops from the join)
        vdj_only = vdj["cells"] - ag["cells"]
        antigen_only = ag["cells"] - vdj["cells"]
        check(len(vdj_only) == 0, f"{donor} every VDJ cell present in antigen arm",
              f"vdj-only={len(vdj_only)}")
        # informational: cells with antigen but no VDJ would drop from per-clonotype output
        print(f"    (antigen-only cells, no VDJ: {len(antigen_only)})")

        # --- GEX arm (Tier 1) ---
        gex = load_gex(donor)
        check(gex["ensg_ok"], f"{donor} GEX gene IDs valid Ensembl (^ENSG\\d{{11}}$ -> human)",
              f"{gex['n_genes']} genes")
        check(gex["body_ok"], f"{donor} GEX counts are non-negative integers")
        check(gex["no_empty_cell"], f"{donor} GEX has no all-zero cell")
        check(gex["plasma_n"] > 0, f"{donor} GEX plasma markers present", f"{gex['plasma_n']} markers")
        check(len(gex["cells"] - ag["cells"]) == 0, f"{donor} every GEX cell present in antigen arm",
              f"gex-only={len(gex['cells'] - ag['cells'])}")
        check(gex["cells"] == vdj["cells"], f"{donor} GEX and VDJ cover the same cells")

        # JOIN SIMULATION: linker (cell->clone) x antigen UMIs -> per-clonotype dominant antigen;
        # and x GEX -> per-clonotype plasma-marker expression.
        # Index the antigen UMIs by cell first so the join is O(cells), not O(cells x umi_keys).
        ag_by_cell = defaultdict(dict)  # cell -> {antigen: umi}
        for (c, antigen), n in ag["umi_counts"].items():
            ag_by_cell[c][antigen] = n
        clono_umis = defaultdict(lambda: defaultdict(int))  # cloneKey -> antigen -> umi
        clono_cells = defaultdict(list)
        binder_plasma, naive_plasma = [], []
        for cell, ckey in vdj["clone_key"].items():
            clono_cells[ckey].append(cell)
            for antigen, n in ag_by_cell.get(cell, {}).items():
                clono_umis[ckey][antigen] += n

        donor_clono = len(clono_umis)
        total_clono += donor_clono
        clear_ok = 0
        clear_total = 0
        preview = []
        for ckey, cells in clono_cells.items():
            # the clonotype's planted antigen (from its cells; clear only if all agree & clear)
            planted_set = {planted.get((donor, c)) for c in cells}
            umi = clono_umis[ckey]
            dominant = max(umi, key=umi.get) if umi else None
            if len(planted_set) == 1 and next(iter(planted_set)) in clear_antigens:
                target = next(iter(planted_set))
                clear_total += 1
                if dominant == target:
                    clear_ok += 1
                else:
                    join_mismatches.append((donor, target, dominant, len(cells)))
                if len(cells) >= 5:  # preview the lead clones
                    preview.append((target, len(cells), dominant, umi.get(target, 0), umi.get(CONTROL_NAME, 0)))
            # GEX coherence: per-clonotype mean plasma-marker expression, bucketed by class
            clono_plasma = sum(gex["plasma_mean"].get(c, 0.0) for c in cells) / len(cells)
            if len(planted_set) == 1 and next(iter(planted_set)) in clear_antigens:
                binder_plasma.append(clono_plasma)
            elif planted_set == {"ambiguous"}:
                naive_plasma.append(clono_plasma)

        total_clear += clear_total
        check(donor_clono > 0, f"{donor} join produces non-empty per-clonotype output",
              f"{donor_clono} clonotypes")
        check(clear_ok == clear_total, f"{donor} every clear-antigen clonotype's dominant == planted antigen",
              f"{clear_ok}/{clear_total}")
        bp = sum(binder_plasma) / len(binder_plasma) if binder_plasma else 0.0
        npl = sum(naive_plasma) / len(naive_plasma) if naive_plasma else 0.0
        check(bp > npl, f"{donor} GEX plasma expression higher in binder than naive/ambiguous clonotypes",
              f"binder={bp:.1f} vs naive={npl:.1f}")
        for target, n, dom, on, ctrl in sorted(preview, reverse=True)[:6]:
            print(f"    lead clone n={n:2d}  planted={target:22s} -> join dominant={dom:22s} "
                  f"(target UMI={on}, control UMI={ctrl})")

    print("\n" + "=" * 70)
    check(total_clono > 0, "overall: convergence join non-empty", f"{total_clono} clonotypes total")
    check(len(join_mismatches) == 0, "overall: no clear-clonotype antigen mismatches",
          f"{total_clear} clear clonotypes checked")
    n_fail = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{'ALL PASS' if n_fail == 0 else str(n_fail) + ' CHECK(S) FAILED'} "
          f"({len(results) - n_fail}/{len(results)})")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
