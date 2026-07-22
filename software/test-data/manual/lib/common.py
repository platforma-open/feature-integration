"""Shared primitives for the manual test-data generators (antigen / VDJ / GEX arms).

Standard library only, deterministic (seeded). Read geometry matches the Feature Integration
block defaults (10x 5' v2):

  R1 = CELL(16) + UMI(10)           -> 26 bp
  R2 = feature barcode(15) + filler -> 25 bp   (block reads first 15 bp as the feature)
"""

import gzip
import random as _random

# Per-arm seeds (kept distinct so each arm is independently reproducible).
ANTIGEN_SEED = 20260629
VDJ_SEED = 6496
GEX_SEED = 6496
ANNOTATION_SEED = 20260721

CELL_LEN = 16
UMI_LEN = 10
FEAT_LEN = 15
R2_FILLER = "CAACTGGTAC"  # fixed 10 bp after the feature barcode; captured by R2:* and ignored
QUAL_CHAR = "I"  # Phred 40
GZIP_LEVEL = 6  # level 6 (not 9): ~half the time at these volumes, marginally larger files
CONTROL_NAME = "negative_control"

BASES = "ACGT"


def rand_seq(rng, n):
    return "".join(rng.choice(BASES) for _ in range(n))


def hamming(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def gen_distinct(rng, count, length, min_dist, avoid=()):
    """Generate `count` sequences pairwise >= min_dist apart and >= min_dist from every `avoid`.
    O(count^2) — fine for the small panel; NOT used for cell barcodes (see gen_cells)."""
    out = []
    guard = 0
    while len(out) < count:
        guard += 1
        if guard > count * 10000:
            raise RuntimeError("could not generate enough distinct barcodes; relax min_dist")
        cand = rand_seq(rng, length)
        if all(hamming(cand, e) >= min_dist for e in list(out) + list(avoid)):
            out.append(cand)
    return out


def gen_cells(rng, count):
    """Distinct random 16-mer cell barcodes, O(count) via a set (the panel's gen_distinct is O(n^2)
    and does not scale to tens of thousands of cells). Hamming spacing is NOT enforced: at cohort
    scale a 1 bp error colliding with a *different* real barcode is astronomically unlikely
    (~count * 48 / 4^16), so the `errors` scenario's clean-correction guarantee still holds, and
    real barcodes are Hamming-close anyway."""
    seen = set()
    out = []
    while len(out) < count:
        c = rand_seq(rng, CELL_LEN)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def mutate(rng, seq, n_subs=1):
    """Return `seq` with `n_subs` single-base substitutions at distinct positions."""
    s = list(seq)
    for p in rng.sample(range(len(s)), n_subs):
        s[p] = rng.choice([b for b in BASES if b != s[p]])
    return "".join(s)


def fq_record(name, seq):
    return f"@{name}\n{seq}\n+\n{QUAL_CHAR * len(seq)}\n"


def write_gz(path, text):
    # Deterministic gzip: no embedded mtime or filename, so a re-run with the same seed produces
    # byte-identical output.
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=GZIP_LEVEL) as gz:
        gz.write(text.encode())


def write_fastq_gz(path, reads, idx):
    """Stream FASTQ records straight into gzip (never materialize the whole file as one string — at
    cohort scale that is hundreds of MB per file). `idx` selects the R1 (1) or R2 (2) column of each
    read tuple [name, r1, r2, lane]."""
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=GZIP_LEVEL) as gz:
        for r in reads:
            gz.write(fq_record(r[0], r[idx]).encode())


def new_rng(seed):
    return _random.Random(seed)
