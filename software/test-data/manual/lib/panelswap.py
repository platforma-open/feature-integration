"""Panel-swap scenarios (folded in from the old standalone panel-swap/ bed).

Two antigen-only beds whose point is the tag->feature PANEL CSV, not the reads:

  panel-swap   ONE ~60-cell read set + THREE tag->feature CSVs to swap on it (the feature-barcode
               whitelist). Swapping shows the whitelist filter and the tag->feature merge:
                 panel_full.csv    7 barcodes -> 7 features (Spike + Spike-v2 distinct)
                 panel_merged.csv  same barcodes, Spike + Spike-v2 both -> SARS2-Spike (summed)
                 panel_core.csv    3 barcodes only -> Spike, RSV-F, NEG_CTRL; the rest DROPPED off-panel
               Also emits a 1-bp-error + off-panel-junk variant (beam_errors_*) to exercise refine-tags
               de-novo CELL correction and off-panel dropping.

  multisample  Two samples with DIFFERENT binding profiles (sample1 Spike-heavy, sample2 RSV/HA-heavy),
               exercising the per-sample axis the next block aggregates on.

10x 5' v2 geometry: R1 = CELL(16) + UMI(10), and R2 = FEATURE(15) + remainder. Deterministic, stdlib
only.
"""

import csv
import gzip
import os

from .common import BASES, CELL_LEN, FEAT_LEN, UMI_LEN, new_rng

SEED = 6496

# Real 10x / TotalSeq-style 15-mers (designed min Hamming distance >= 3). bcSpikeB + the off-panel junk
# barcodes are generated with an explicit distance filter.
PANEL_SEQ = {
    "bcSpikeA": "CGATGCCGGACGATC",
    "bcRSVF": "CCGTCTCACCGATAT",
    "bcRSVG": "CGGCTCACCGCGTCT",
    "bcHA": "CTATCTACCGGCTCG",
    "bcOva": "AGCACGACCTTGGTT",
    "bcCtrl": "GATTGGCTACTCAAT",  # 10x negative-control example barcode
}

ANTIGEN_BCS = ["bcSpikeA", "bcRSVF", "bcRSVG", "bcHA", "bcOva"]

# Intended-dominant label per kind, for the truth CSV (bc key -> antigen name in panel_full terms).
DOMINANT_NAME = {
    "bcSpikeA": "SARS2-Spike", "bcSpikeB": "SARS2-Spike-v2", "bcRSVF": "RSV-F", "bcRSVG": "RSV-G",
    "bcHA": "InfluenzaHA", "bcOva": "Ovalbumin", "bcCtrl": "NEG_CTRL",
}


def _hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def _rand_seq(rng, n):
    return "".join(rng.choice(BASES) for _ in range(n))


def _gen_distant(rng, existing, n, min_h):
    """A fresh n-mer at Hamming distance >= min_h from every sequence in `existing`."""
    while True:
        cand = _rand_seq(rng, n)
        if all(_hamming(cand, e) >= min_h for e in existing):
            return cand


def _mutate_one(rng, seq):
    """Flip exactly one base (a single substitution)."""
    pos = rng.randrange(len(seq))
    alt = rng.choice([b for b in BASES if b != seq[pos]])
    return seq[:pos] + alt + seq[pos + 1:]


def _build_profile(rng, kind):
    """Return {barcodeKey: nDistinctUMIs} for one cell of the given kind."""
    if kind.startswith("specific:"):
        target = kind.split(":", 1)[1]
        prof = {target: rng.randint(40, 150)}
        for other in rng.sample([b for b in ANTIGEN_BCS if b != target], k=rng.randint(1, 2)):
            prof[other] = rng.randint(1, 6)
        prof["bcCtrl"] = rng.randint(0, 3)
        return prof
    if kind == "crossreactive":
        a, b = rng.sample(ANTIGEN_BCS, k=2)
        prof = {a: rng.randint(30, 80), b: rng.randint(30, 80)}
        prof["bcCtrl"] = rng.randint(0, 4)
        return prof
    if kind == "ambiguous":
        prof = {a: rng.randint(5, 15) for a in rng.sample(ANTIGEN_BCS, k=3)}
        prof["bcCtrl"] = rng.randint(0, 4)
        return prof
    if kind == "background":
        prof = {"bcCtrl": rng.randint(20, 60)}
        prof[rng.choice(ANTIGEN_BCS)] = rng.randint(0, 5)
        return prof
    if kind == "spike2":
        # Binds Spike via BOTH barcodes. panel_full -> split across Spike / Spike-v2 (often ambiguous);
        # panel_merged -> sum to one clean SARS2-Spike call.
        prof = {"bcSpikeA": rng.randint(20, 80), "bcSpikeB": rng.randint(20, 80)}
        prof[rng.choice([b for b in ANTIGEN_BCS if b != "bcSpikeA"])] = rng.randint(1, 5)
        prof["bcCtrl"] = rng.randint(0, 3)
        return prof
    raise ValueError(f"unknown kind {kind!r}")


def _make_cells(rng, kinds):
    cells = []
    seen = set()
    for kind in kinds:
        while True:
            bc = _rand_seq(rng, CELL_LEN)
            if bc not in seen:
                seen.add(bc)
                break
        profile = _build_profile(rng, kind)
        if kind.startswith("specific:"):
            intended = DOMINANT_NAME[kind.split(":", 1)[1]]
        elif kind == "spike2":
            intended = "SARS2-Spike (via 2 barcodes)"
        elif kind == "background":
            intended = "NEG_CTRL"
        else:
            intended = "ambiguous"
        cells.append({"barcode": bc, "kind": kind, "profile": profile, "intended": intended})
    return cells


def _write_fastq(rng, out_prefix, cells, all_bc_seq, inject_errors=False, junk_reads=0, junk_barcodes=None):
    """Render cell records into paired R1/R2 fastq.gz. Returns a small stats dict."""
    r1_path = f"{out_prefix}_R1.fastq.gz"
    r2_path = f"{out_prefix}_R2.fastq.gz"
    tail = "TTAATTAATT"  # neutral R2 tail after the 15 nt feature barcode (ignored remainder)
    n_reads = 0
    n_cell_err = 0
    n_feat_err = 0

    with (
        gzip.GzipFile(r1_path, "wb", mtime=0) as f1,
        gzip.GzipFile(r2_path, "wb", mtime=0) as f2,
    ):

        def emit(name, r1, r2):
            nonlocal n_reads
            f1.write(f"@{name}\n{r1}\n+\n{'I' * len(r1)}\n".encode())
            f2.write(f"@{name}\n{r2}\n+\n{'I' * len(r2)}\n".encode())
            n_reads += 1

        for ci, cell in enumerate(cells):
            for bc_key, n_umis in cell["profile"].items():
                feat_seq = all_bc_seq[bc_key]
                for ui in range(n_umis):
                    umi = _rand_seq(rng, UMI_LEN)
                    for di in range(rng.randint(1, 4)):  # PCR duplicates
                        cell_seq = cell["barcode"]
                        f_seq = feat_seq
                        if inject_errors:
                            if rng.random() < 0.15:
                                cell_seq = _mutate_one(rng, cell_seq)
                                n_cell_err += 1
                            if rng.random() < 0.10:
                                f_seq = _mutate_one(rng, f_seq)  # stays Hamming-1 -> snapped
                                n_feat_err += 1
                        emit(f"c{ci}_{bc_key}_u{ui}_d{di}", cell_seq + umi, f_seq + tail)

        # Off-panel junk: real-looking cells whose feature barcode matches NO panel member.
        if junk_reads and junk_barcodes:
            for j in range(junk_reads):
                emit(f"junk{j}", _rand_seq(rng, CELL_LEN) + _rand_seq(rng, UMI_LEN),
                     rng.choice(junk_barcodes) + tail)

    return {"reads": n_reads, "cell_errors": n_cell_err, "feature_errors": n_feat_err}


def _write_truth(path, cells):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cellBarcode", "cellKind", "intendedDominant"])
        for c in cells:
            w.writerow([c["barcode"], c["kind"], c["intended"]])


def _write_panels(out_dir, spike_b):
    """The three tag->feature CSVs (the feature-barcode whitelist / panel), written flat into out_dir."""
    S = PANEL_SEQ
    full = [
        (S["bcSpikeA"], "SARS2-Spike"), (spike_b, "SARS2-Spike-v2"), (S["bcRSVF"], "RSV-F"),
        (S["bcRSVG"], "RSV-G"), (S["bcHA"], "InfluenzaHA"), (S["bcOva"], "Ovalbumin"),
        (S["bcCtrl"], "NEG_CTRL"),
    ]
    merged = [(bc, "SARS2-Spike" if feat == "SARS2-Spike-v2" else feat) for bc, feat in full]
    core = [(S["bcSpikeA"], "SARS2-Spike"), (S["bcRSVF"], "RSV-F"), (S["bcCtrl"], "NEG_CTRL")]
    for name, rows in [("panel_full", full), ("panel_merged", merged), ("panel_core", core)]:
        with open(os.path.join(out_dir, f"{name}.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["tag", "feature"])
            w.writerows(rows)


def _setup(rng):
    """Panel + off-panel junk (shared derivation). Returns (all_bc_seq, spike_b, junk)."""
    spike_b = _gen_distant(rng, list(PANEL_SEQ.values()), FEAT_LEN, min_h=4)
    junk = [_gen_distant(rng, list(PANEL_SEQ.values()) + [spike_b], FEAT_LEN, min_h=3) for _ in range(3)]
    all_bc_seq = dict(PANEL_SEQ)
    all_bc_seq["bcSpikeB"] = spike_b
    # Sanity: every panel barcode pair is >= 3 apart (unambiguous single-error correction).
    seqs = list(all_bc_seq.values())
    for i in range(len(seqs)):
        for k in range(i + 1, len(seqs)):
            assert _hamming(seqs[i], seqs[k]) >= 3, f"panel barcodes too close: {seqs[i]} {seqs[k]}"
    return all_bc_seq, spike_b, junk


_MAIN_KINDS = (
    ["specific:bcSpikeA"] * 8 + ["specific:bcRSVF"] * 6 + ["specific:bcRSVG"] * 6
    + ["specific:bcHA"] * 6 + ["specific:bcOva"] * 4 + ["crossreactive"] * 8
    + ["ambiguous"] * 8 + ["background"] * 8 + ["spike2"] * 6
)


def build_panel_swap(out_dir):
    """One clean read set + a 1-bp-error/off-panel variant + the three swappable panel CSVs + truth."""
    os.makedirs(out_dir, exist_ok=True)
    rng = new_rng(SEED)
    all_bc_seq, spike_b, junk = _setup(rng)
    _write_panels(out_dir, spike_b)

    cells = _make_cells(rng, _MAIN_KINDS)
    clean = _write_fastq(rng, os.path.join(out_dir, "beam"), cells, all_bc_seq)
    _write_truth(os.path.join(out_dir, "truth_cells.csv"), cells)

    err_cells = _make_cells(rng, _MAIN_KINDS)
    err = _write_fastq(rng, os.path.join(out_dir, "beam_errors"), err_cells, all_bc_seq,
                       inject_errors=True, junk_reads=250, junk_barcodes=junk)

    print(f"[antigen:panel-swap] Spike-v2 bc={spike_b}; swap panel_full/panel_merged/panel_core on beam_*")
    print(f"  beam         {len(cells):3d} cells  {clean['reads']:6d} reads -> {out_dir}")
    print(f"  beam_errors  {len(err_cells):3d} cells  {err['reads']:6d} reads "
          f"(+{err['cell_errors']} cell-bc / +{err['feature_errors']} feature-bc errors, +250 junk)")


def build_multisample(out_dir):
    """Two samples with different binding profiles (per-sample axis) + panel_full + truth."""
    os.makedirs(out_dir, exist_ok=True)
    rng = new_rng(SEED)
    all_bc_seq, spike_b, _junk = _setup(rng)
    _write_panels(out_dir, spike_b)

    s1_kinds = (["specific:bcSpikeA"] * 12 + ["spike2"] * 6 + ["crossreactive"] * 4
                + ["background"] * 4 + ["ambiguous"] * 4)
    s2_kinds = (["specific:bcRSVF"] * 8 + ["specific:bcRSVG"] * 6 + ["specific:bcHA"] * 8
                + ["crossreactive"] * 6 + ["background"] * 6 + ["ambiguous"] * 6)
    s1 = _make_cells(rng, s1_kinds)
    s2 = _make_cells(rng, s2_kinds)
    st1 = _write_fastq(rng, os.path.join(out_dir, "sample1"), s1, all_bc_seq)
    st2 = _write_fastq(rng, os.path.join(out_dir, "sample2"), s2, all_bc_seq)
    _write_truth(os.path.join(out_dir, "truth_cells.csv"), s1 + s2)
    print(f"[antigen:multisample] s1={len(s1)} cells {st1['reads']} reads ; "
          f"s2={len(s2)} cells {st2['reads']} reads -> {out_dir}")
