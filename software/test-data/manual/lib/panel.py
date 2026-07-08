"""The antigen/feature panel — the feature-barcode whitelist the Feature Integration block snaps
reads to. The first (up to) 4 barcodes are the REAL 10x BEAM-Ab panel from the public "2k transgenic
HEL mouse splenocytes" dataset; the rest are synthesized as distinct 15-mers (Hamming >= 3 from each
other and the anchors) so the panel scales to any size while keeping authentic anchors.

https://www.10xgenomics.com/datasets/2k-transgenic-hel-mouse-splenocytes-beam-ab-2-standard
"""

import csv
import os

from . import common
from .common import CONTROL_NAME, FEAT_LEN, gen_distinct, new_rng

# Real 10x BEAM-Ab antigen anchors (4 antigens), 15 bp, read R2, pattern ^(BC).
REAL_ANTIGENS = [
    ("SARS-TRI-S_WT", "CGATGCCGGACGATC"),
    ("Anti-Hen_Egg_Lysozyme", "CCGTCTCACCGATAT"),
    ("gp120", "GATTGGCTACTCAAT"),
    ("H5N1", "CGGCTCACCGCGTCT"),
]
CONTROL_BC = "CTATCTACCGGCTCG"


class Panel:
    """An antigen panel: ordered antigen names + a name -> 15 bp barcode map that INCLUDES the
    negative control. Replaces the old module-level ANTIGEN_NAMES / FEATURES globals so a generator
    run is fully described by its arguments."""

    def __init__(self, names, features, control_name=CONTROL_NAME):
        self.names = names            # antigen names, excluding the control
        self.features = features      # {name: barcode}, INCLUDING the control
        self.control_name = control_name

    @property
    def barcodes(self):
        return list(self.features.values())


def build_panel(panel_size, seed=common.ANTIGEN_SEED):
    """Build a Panel of `panel_size` antigens + 1 control. The first min(panel_size, 4) antigens are
    the real 10x anchors; the rest are synthetic 15-mers (Hamming >= 3 from each other and the anchors
    + control). Uses an independent RNG so the panel is identical regardless of sample/cell scale."""
    if panel_size < 1:
        raise SystemExit("panel size must be >= 1")
    prng = new_rng(seed + 7)
    antigens = list(REAL_ANTIGENS[:panel_size])
    n_real = len(antigens)
    if panel_size > n_real:
        existing = [bc for _, bc in antigens] + [CONTROL_BC]
        extra = gen_distinct(prng, panel_size - n_real, FEAT_LEN, min_dist=3, avoid=existing)
        for i, bc in enumerate(extra):
            antigens.append((f"antigen_{n_real + i + 1:03d}", bc))
    names = [n for n, _ in antigens]
    feats = {n: bc for n, bc in antigens}
    feats[CONTROL_NAME] = CONTROL_BC
    assert all(len(bc) == FEAT_LEN for bc in feats.values()), "feature barcodes must be 15 bp"
    return Panel(names, feats)


def load_clear_antigens(tags_csv):
    """Clear (real) antigens = every feature in a panel CSV (tags.csv) except the negative control.
    Panel-derived so it scales with the panel size instead of a hardcoded set. The VDJ and GEX arms
    both build coherence on top of this, so the definition lives here in one place."""
    if not os.path.exists(tags_csv):
        raise SystemExit(f"panel not found: {tags_csv}\nGenerate the antigen arm first.")
    names = set()
    with open(tags_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            names.add(row["feature"])
    return {n for n in names if n != CONTROL_NAME}
