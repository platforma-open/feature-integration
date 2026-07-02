"""Generate synthetic BEAM-Ab feature-barcode FASTQ datasets for MANUAL exploration
of the Feature Integration block (not for CI — the tiny CI fixtures live in test/assets).

Run from this directory:  python3 generate.py

Everything is stdlib-only and seeded, so the output is deterministic. Re-run any time to
regenerate. This whole `manual-run/` tree is gitignored (see block .gitignore).

--------------------------------------------------------------------------------------------
What the block expects (10x 5' v2 geometry, confirmed against the block's tag-pattern.lib.tengo
and the 10x Antigen-Capture read structure):

  R1 = CELL(16 nt) + UMI(10 nt)           -> 26 nt
  R2 = FEATURE barcode(15 nt) + remainder -> feature barcode sits at R2 position 0

The pipeline is: mitool parse -> refine-tags (CELL de-novo; FEATURE snapped to the panel
whitelist = the `tag` column of your CSV; off-panel reads dropped) -> tag-stat -t CELL -t
FEATURE -u UMI -> per_cell_metrics.py. Distinct-UMI counts per (cell, feature) drive the
per-cell abundance / fraction / consensus / specificity outputs.

--------------------------------------------------------------------------------------------
Datasets produced (all share one generator so behaviour is consistent):

  main/         ~60 cells, one sample, CLEAN reads (no errors, all barcodes on-panel).
                The baseline "regular" run. PCR duplicates are injected (raw reads >
                distinct UMIs) so you can confirm the metrics use deduplicated unique_UMI.
  errors/       Same ~60-cell profile + 1-bp errors in CELL and FEATURE barcodes + a block
                of off-panel junk reads. Exercises refine-tags de-novo CELL correction, the
                panel-whitelist snap of near-miss FEATURE barcodes, and off-panel dropping.
  multisample/  Two samples with DIFFERENT binding profiles (sample1 Spike-heavy, sample2
                RSV/HA-heavy). Exercises the per-sample axis the next block aggregates on.

  panels/       Three tag->feature CSVs (the panel = the feature-barcode whitelist). Swap
                them on the SAME reads to see the whitelist filter and tag->feature merge:
                  panel_full.csv   7 barcodes -> 7 features (Spike + Spike-v2 distinct)
                  panel_merged.csv same barcodes, Spike + Spike-v2 both -> SARS2-Spike (summed)
                  panel_core.csv   3 barcodes only -> Spike, RSV-F, NEG_CTRL; the rest of the
                                   reads (RSV-G, HA, Ova, Spike-v2) are DROPPED off-panel.

  truth_cells.csv (in main/ and multisample/) — the ground-truth cell kind + intended
                dominant antigen I assigned per cell, so you can eyeball the block's
                `consensusFeature` output against what the data was built to say.
--------------------------------------------------------------------------------------------
"""

import csv
import gzip
import random
from pathlib import Path

SEED = 6496
BASES = "ACGT"
CELL_LEN, UMI_LEN, FEATURE_LEN = 16, 10, 15

HERE = Path(__file__).resolve().parent

# --- Panel: real 10x / TotalSeq-style 15-mers (designed min Hamming distance >= 3). The
#     first six are example barcodes from 10x feature-reference material; bcSpikeB and the
#     off-panel junk barcodes are generated below with an explicit distance filter so the
#     design guarantees (unambiguous single-error correction; junk is un-snappable) hold. ---
PANEL_SEQ = {
    "bcSpikeA": "CGATGCCGGACGATC",
    "bcRSVF": "CCGTCTCACCGATAT",
    "bcRSVG": "CGGCTCACCGCGTCT",
    "bcHA": "CTATCTACCGGCTCG",
    "bcOva": "AGCACGACCTTGGTT",
    "bcCtrl": "GATTGGCTACTCAAT",  # 10x negative-control example barcode
}


def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def rand_seq(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(BASES) for _ in range(n))


def gen_distant(rng: random.Random, existing: list[str], n: int, min_h: int) -> str:
    """A fresh n-mer at Hamming distance >= min_h from every sequence in `existing`."""
    while True:
        cand = rand_seq(rng, n)
        if all(hamming(cand, e) >= min_h for e in existing):
            return cand


def mutate_one(rng: random.Random, seq: str) -> str:
    """Flip exactly one base (a single substitution)."""
    pos = rng.randrange(len(seq))
    alt = rng.choice([b for b in BASES if b != seq[pos]])
    return seq[:pos] + alt + seq[pos + 1 :]


# ------------------------------------------------------------------------------------------
# Per-cell binding profiles. A profile maps a PHYSICAL feature barcode key -> number of
# distinct molecules (unique UMIs) observed for that (cell, barcode). The read renderer turns
# each molecule into 1..4 duplicate read pairs (PCR duplicates) so raw reads > unique_UMI.
# ------------------------------------------------------------------------------------------
ANTIGEN_BCS = ["bcSpikeA", "bcRSVF", "bcRSVG", "bcHA", "bcOva"]


def build_profile(rng: random.Random, kind: str) -> dict[str, int]:
    """Return {barcodeKey: nDistinctUMIs} for one cell of the given kind."""
    if kind.startswith("specific:"):
        target = kind.split(":", 1)[1]
        prof = {target: rng.randint(40, 150)}
        for other in rng.sample([b for b in ANTIGEN_BCS if b != target], k=rng.randint(1, 2)):
            prof[other] = rng.randint(1, 6)  # low background on unrelated antigens
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
        # Binds Spike via BOTH barcodes. With panel_full these split across Spike / Spike-v2
        # (often ambiguous); with panel_merged they sum to one clean SARS2-Spike call.
        prof = {"bcSpikeA": rng.randint(20, 80), "bcSpikeB": rng.randint(20, 80)}
        prof[rng.choice([b for b in ANTIGEN_BCS if b != "bcSpikeA"])] = rng.randint(1, 5)
        prof["bcCtrl"] = rng.randint(0, 3)
        return prof
    raise ValueError(f"unknown kind {kind!r}")


# Intended-dominant label per kind, for the truth CSV (bc key -> readable antigen name in
# panel_full terms). "ambiguous"/"background" have no single intended dominant.
DOMINANT_NAME = {
    "bcSpikeA": "SARS2-Spike",
    "bcSpikeB": "SARS2-Spike-v2",
    "bcRSVF": "RSV-F",
    "bcRSVG": "RSV-G",
    "bcHA": "InfluenzaHA",
    "bcOva": "Ovalbumin",
    "bcCtrl": "NEG_CTRL",
}


def make_cells(rng: random.Random, kinds: list[str]) -> list[dict]:
    """Build cell records: {barcode, kind, profile, intendedDominant}."""
    cells = []
    seen = set()
    for kind in kinds:
        while True:
            bc = rand_seq(rng, CELL_LEN)
            if bc not in seen:
                seen.add(bc)
                break
        profile = build_profile(rng, kind)
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


def write_fastq(
    rng: random.Random,
    out_prefix: Path,
    cells: list[dict],
    all_bc_seq: dict[str, str],
    inject_errors: bool = False,
    junk_reads: int = 0,
    junk_barcodes: list[str] | None = None,
) -> dict:
    """Render cell records into paired R1/R2 fastq.gz. Returns a small stats dict."""
    r1_path = Path(f"{out_prefix}_R1.fastq.gz")
    r2_path = Path(f"{out_prefix}_R2.fastq.gz")
    tail = "TTAATTAATT"  # neutral R2 tail after the 15 nt feature barcode (ignored remainder)
    n_reads = 0
    n_cell_err = 0
    n_feat_err = 0

    # GzipFile(mtime=0) keeps the compressed bytes reproducible across runs.
    with (
        gzip.GzipFile(r1_path, "wb", mtime=0) as f1,
        gzip.GzipFile(r2_path, "wb", mtime=0) as f2,
    ):

        def emit(name: str, r1: str, r2: str):
            nonlocal n_reads
            q1 = "I" * len(r1)
            q2 = "I" * len(r2)
            f1.write(f"@{name}\n{r1}\n+\n{q1}\n".encode())
            f2.write(f"@{name}\n{r2}\n+\n{q2}\n".encode())
            n_reads += 1

        for ci, cell in enumerate(cells):
            for bc_key, n_umis in cell["profile"].items():
                feat_seq = all_bc_seq[bc_key]
                for ui in range(n_umis):
                    umi = rand_seq(rng, UMI_LEN)
                    for di in range(rng.randint(1, 4)):  # PCR duplicates
                        cell_seq = cell["barcode"]
                        f_seq = feat_seq
                        if inject_errors:
                            if rng.random() < 0.15:
                                cell_seq = mutate_one(rng, cell_seq)
                                n_cell_err += 1
                            if rng.random() < 0.10:
                                f_seq = mutate_one(rng, f_seq)  # stays Hamming-1 -> snapped
                                n_feat_err += 1
                        r1 = cell_seq + umi
                        r2 = f_seq + tail
                        emit(f"c{ci}_{bc_key}_u{ui}_d{di}", r1, r2)

        # Off-panel junk: real-looking cells whose feature barcode matches NO panel member
        # (Hamming >= 3 from all), so refine-tags drops them (diversityFilteredByWhitelist).
        if junk_reads and junk_barcodes:
            for j in range(junk_reads):
                cell_seq = rand_seq(rng, CELL_LEN)
                umi = rand_seq(rng, UMI_LEN)
                f_seq = rng.choice(junk_barcodes)
                emit(f"junk{j}", cell_seq + umi, f_seq + tail)

    return {
        "r1": r1_path.name,
        "r2": r2_path.name,
        "reads": n_reads,
        "cell_errors": n_cell_err,
        "feature_errors": n_feat_err,
    }


def write_truth(path: Path, cells: list[dict]):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cellBarcode", "cellKind", "intendedDominant"])
        for c in cells:
            w.writerow([c["barcode"], c["kind"], c["intended"]])


def write_panels(panels_dir: Path, spike_b: str):
    """Three tag->feature CSVs (the feature-barcode whitelist / panel)."""
    S = PANEL_SEQ
    full = [
        (S["bcSpikeA"], "SARS2-Spike"),
        (spike_b, "SARS2-Spike-v2"),
        (S["bcRSVF"], "RSV-F"),
        (S["bcRSVG"], "RSV-G"),
        (S["bcHA"], "InfluenzaHA"),
        (S["bcOva"], "Ovalbumin"),
        (S["bcCtrl"], "NEG_CTRL"),
    ]
    merged = [(bc, "SARS2-Spike" if feat == "SARS2-Spike-v2" else feat) for bc, feat in full]
    core = [
        (S["bcSpikeA"], "SARS2-Spike"),
        (S["bcRSVF"], "RSV-F"),
        (S["bcCtrl"], "NEG_CTRL"),
    ]
    for name, rows in [("panel_full", full), ("panel_merged", merged), ("panel_core", core)]:
        with open(panels_dir / f"{name}.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["tag", "feature"])
            w.writerows(rows)


def main() -> None:
    rng = random.Random(SEED)

    # Extra barcodes generated with a distance filter so the guarantees hold.
    spike_b = gen_distant(rng, list(PANEL_SEQ.values()), FEATURE_LEN, min_h=4)
    junk = [gen_distant(rng, list(PANEL_SEQ.values()) + [spike_b], FEATURE_LEN, min_h=3) for _ in range(3)]

    all_bc_seq = dict(PANEL_SEQ)
    all_bc_seq["bcSpikeB"] = spike_b

    # Sanity: every panel barcode pair is >= 3 apart (unambiguous single-error correction).
    seqs = list(all_bc_seq.values())
    for i in range(len(seqs)):
        for k in range(i + 1, len(seqs)):
            assert hamming(seqs[i], seqs[k]) >= 3, f"panel barcodes too close: {seqs[i]} {seqs[k]}"

    write_panels(HERE / "panels", spike_b)

    # ---- main: ~60 cells, one sample, clean ----
    main_kinds = (
        ["specific:bcSpikeA"] * 8
        + ["specific:bcRSVF"] * 6
        + ["specific:bcRSVG"] * 6
        + ["specific:bcHA"] * 6
        + ["specific:bcOva"] * 4
        + ["crossreactive"] * 8
        + ["ambiguous"] * 8
        + ["background"] * 8
        + ["spike2"] * 6
    )
    main_cells = make_cells(rng, main_kinds)
    main_stats = write_fastq(rng, HERE / "main" / "beam_main", main_cells, all_bc_seq)
    write_truth(HERE / "main" / "truth_cells.csv", main_cells)

    # ---- errors: same profile + 1-bp errors + off-panel junk ----
    err_cells = make_cells(rng, main_kinds)
    err_stats = write_fastq(
        rng, HERE / "errors" / "beam_errors", err_cells, all_bc_seq,
        inject_errors=True, junk_reads=250, junk_barcodes=junk,
    )
    write_truth(HERE / "errors" / "truth_cells.csv", err_cells)

    # ---- multisample: two samples, different profiles ----
    s1_kinds = (
        ["specific:bcSpikeA"] * 12 + ["spike2"] * 6 + ["crossreactive"] * 4
        + ["background"] * 4 + ["ambiguous"] * 4
    )
    s2_kinds = (
        ["specific:bcRSVF"] * 8 + ["specific:bcRSVG"] * 6 + ["specific:bcHA"] * 8
        + ["crossreactive"] * 6 + ["background"] * 6 + ["ambiguous"] * 6
    )
    s1_cells = make_cells(rng, s1_kinds)
    s2_cells = make_cells(rng, s2_kinds)
    s1_stats = write_fastq(rng, HERE / "multisample" / "sample1", s1_cells, all_bc_seq)
    s2_stats = write_fastq(rng, HERE / "multisample" / "sample2", s2_cells, all_bc_seq)
    write_truth(HERE / "multisample" / "truth_cells.csv", s1_cells + s2_cells)

    print("Generated manual-run datasets (seed", SEED, "):")
    print(f"  bcSpikeB (Spike-v2) = {spike_b}")
    print(f"  off-panel junk barcodes = {junk}")
    print(f"  main/        {len(main_cells):3d} cells  {main_stats['reads']:6d} reads")
    print(
        f"  errors/      {len(err_cells):3d} cells  {err_stats['reads']:6d} reads  "
        f"(+{err_stats['cell_errors']} cell-bc errors, +{err_stats['feature_errors']} "
        f"feature-bc errors, +250 off-panel junk reads)"
    )
    print(
        f"  multisample/ s1={len(s1_cells)} cells {s1_stats['reads']} reads ; "
        f"s2={len(s2_cells)} cells {s2_stats['reads']} reads"
    )


if __name__ == "__main__":
    main()
