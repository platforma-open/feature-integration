"""Per-cell categorical annotation arm.

Emits a synthetic cell-type / cluster annotation keyed by the shared bare-16nt cell barcode, so
vdj-multiomic-integration's ANNOTATION-integration path has a deterministic input that does NOT depend on
running CellTypist over the GEX arm.

Source of cells: the antigen arm's ground truth (`truth/expected-consensus.tsv`). Reading cell ids from
there guarantees the annotation's `cell_id` values are exactly the arm-shared bare 16-mers.

Coherence with the GEX program map (lib/gex.py PROGRAMS): a cell's `planted_consensus` biases its cell
type. A binder or a cross-reactive cell gets a plasma-like type; an ambiguous or non-binder cell gets a
naive-B type. A fixed share of cells is reassigned to a memory-B type so the small vocabulary has all
three terms. The integer `cluster` (0-4) is coherent with the cell type.

Output:  annotations/<donor>.tsv   columns: cell_id  cell_type  cluster

Canonical downstream axis order is [pl7.app/sampleId, pl7.app/sc/cellId] (see README).
"""

import csv
import os
from collections import defaultdict

from .common import ANNOTATION_SEED, new_rng

# Small fixed cell-type vocabulary (loosely the B-lineage programs in gex.py PROGRAMS).
PLASMA = "plasma"
NAIVE_B = "naive_b"
MEMORY_B = "memory_b"

# planted_consensus == "ambiguous" is the naive / non-binder class; any other value is an antigen name
# (a binder) or "crossreactive" -> plasma-like.
NAIVE_CONSENSUS = {"ambiguous"}

# Fraction of cells reassigned to memory-B regardless of their planted class, so the vocabulary and the
# clusters both carry a third population.
MEMORY_FRACTION = 0.15

# Cluster ids per cell type (small ints 0-4), coherent with the type so clusters are separable.
CLUSTERS_BY_TYPE = {
    PLASMA: (0, 1),
    NAIVE_B: (2, 3),
    MEMORY_B: (4,),
}


def _read_cells_by_donor(consensus_tsv):
    """{donor: [(cellId, planted_consensus), ...]} in file order, from the antigen ground truth."""
    by_donor = defaultdict(list)
    with open(consensus_tsv, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            by_donor[row["sample"]].append((row["cellId"], row["planted_consensus"]))
    return by_donor


def _assign(rng, planted_consensus):
    """(cell_type, cluster) for one cell, biased by its planted antigen class. Deterministic given rng."""
    base = NAIVE_B if planted_consensus in NAIVE_CONSENSUS else PLASMA
    cell_type = MEMORY_B if rng.random() < MEMORY_FRACTION else base
    cluster = rng.choice(CLUSTERS_BY_TYPE[cell_type])
    return cell_type, cluster


def write_annotations(run_dir, seed=ANNOTATION_SEED):
    """Emit annotations/<donor>.tsv (cell_id, cell_type, cluster) from run_dir/truth/expected-consensus.tsv.

        Cell ids are the arm-shared bare 16-mers copied verbatim from the antigen truth. Requires the antigen
        arm to have run first."""
    consensus_tsv = os.path.join(run_dir, "truth", "expected-consensus.tsv")
    if not os.path.exists(consensus_tsv):
        raise SystemExit(
            f"missing {consensus_tsv}\n--with-annotations needs the antigen arm's ground truth; run the "
            "antigen arm (or a full run) first so cell ids exist."
        )
    out_dir = os.path.join(run_dir, "annotations")
    os.makedirs(out_dir, exist_ok=True)
    rng = new_rng(seed)
    by_donor = _read_cells_by_donor(consensus_tsv)

    total = 0
    for donor in sorted(by_donor):
        cells = by_donor[donor]
        path = os.path.join(out_dir, f"{donor}.tsv")
        counts = defaultdict(int)
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, delimiter="\t")
            w.writerow(["cell_id", "cell_type", "cluster"])
            for cell_id, planted in cells:
                cell_type, cluster = _assign(rng, planted)
                counts[cell_type] += 1
                w.writerow([cell_id, cell_type, cluster])
        total += len(cells)
        summary = ", ".join(f"{counts[t]} {t}" for t in (PLASMA, NAIVE_B, MEMORY_B) if counts[t])
        print(f"  {donor}: {len(cells)} cells ({summary}) -> annotations/{donor}.tsv")
    print(f"[annotations] {total} cells across {len(by_donor)} donors -> annotations/<donor>.tsv")
