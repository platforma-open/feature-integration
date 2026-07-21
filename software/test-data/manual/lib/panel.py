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

# Per-antigen "Class" vocabulary, matching the real customer panel's Class column. The real anchors
# carry a biologically meaningful class; synthetic antigens default to "synthetic"; the negative
# control is "control".
ANCHOR_CLASS = {
    "SARS-TRI-S_WT": "viral",
    "Anti-Hen_Egg_Lysozyme": "enzyme",
    "gp120": "viral",
    "H5N1": "viral",
}
# Species alternate across the panel so downstream species-grouping has a Human/Cyno split.
SPECIES_CYCLE = ("Human", "Cyno")


def classify_antigens(names, offtarget_count):
    """Classify an ORDERED list of non-control antigen names into the real customer panel's per-antigen
    Type / Species / Class. The first `offtarget_count` -> Off-Target; the rest -> Target; species
    alternate Human/Cyno; class from ANCHOR_CLASS (real anchors) else "synthetic".

    Single source of the classification + offtarget-count validation rule, shared by `build_panel` and
    the beam-exact path so the two never drift. The negative control is NOT included — callers add it
    (Decoy / "" / control). Raises SystemExit if `offtarget_count` is out of range.

    Returns (types, species, classes), each a dict keyed by antigen name."""
    if not 0 <= offtarget_count <= len(names):
        raise SystemExit(
            f"--offtarget-count must be between 0 and the antigen count ({len(names)}); got {offtarget_count}"
        )
    types, species, classes = {}, {}, {}
    for i, n in enumerate(names):
        types[n] = "Off-Target" if i < offtarget_count else "Target"
        species[n] = SPECIES_CYCLE[i % len(SPECIES_CYCLE)]
        classes[n] = ANCHOR_CLASS.get(n, "synthetic")
    return types, species, classes


class Panel:
    """An antigen panel: ordered antigen names + a name -> 15 bp barcode map that INCLUDES the
    negative control. Replaces the old module-level ANTIGEN_NAMES / FEATURES globals so a generator
    run is fully described by its arguments.

    Carries the real customer panel's per-antigen metadata — `types` (Target/Off-Target/Decoy),
    `species` (Human/Cyno/""), `classes` (viral/enzyme/synthetic/control) — each a dict keyed by
    feature name and INCLUDING the control. They default to sensible values so `Panel(names, feats)`
    stays valid for any legacy construction.

    A feature may map to a LIST of barcodes (multi-barcode antigens); a bare `str` is coerced to a
    1-element list so single-barcode construction stays backward-compatible. `combine` (feature ->
    "sum" | "all") says how the block reads the members: "sum" adds the per-barcode UMIs, "all" (AND)
    requires every member barcode to fire. Defaults to "sum" for every feature."""

    def __init__(
        self, names, features, control_name=CONTROL_NAME, types=None, species=None, classes=None, combine=None
    ):
        self.names = names  # antigen names, excluding the control
        # {name: [barcode, ...]}, INCLUDING the control. A bare str coerces to a 1-element list.
        self.features = {n: ([v] if isinstance(v, str) else list(v)) for n, v in features.items()}
        self.control_name = control_name
        self.types = types if types is not None else {n: ("Decoy" if n == control_name else "Target") for n in features}
        self.species = species if species is not None else {n: "" for n in features}
        self.classes = (
            classes
            if classes is not None
            else {n: ("control" if n == control_name else ANCHOR_CLASS.get(n, "synthetic")) for n in features}
        )
        self.combine = combine if combine is not None else {n: "sum" for n in features}

    @property
    def barcodes(self):
        return [bc for bcs in self.features.values() for bc in bcs]


def build_panel(panel_size, seed=common.ANTIGEN_SEED, offtarget_count=0, multibarcode=False):
    """Build a Panel of `panel_size` antigens + 1 control. The first min(panel_size, 4) antigens are
    the real 10x anchors; the rest are synthetic 15-mers (Hamming >= 3 from each other and the anchors
    + control). Uses an independent RNG so the panel is identical regardless of sample/cell scale.

    Per-antigen Type/Species/Class match the real customer panel shape: the control -> Decoy/control;
    the first `offtarget_count` antigens -> Off-Target; the rest -> Target; species alternate
    Human/Cyno; class from ANCHOR_CLASS (real anchors) else "synthetic".

    With `multibarcode=True` the first antigen gets a SECOND barcode read out under combine="all"
    (AND — both members must fire) and the second antigen a second barcode under combine="sum" (the
    per-barcode UMIs add up); every other antigen stays single-barcode "sum". This is the shared-path
    analogue of the libraseq fixture, so the FI multi-barcode combine logic is exercisable inside a
    full multiomic run. The extra barcodes come from the panel RNG and are ONLY drawn in this branch,
    so a default (single-barcode) panel is byte-identical to before."""
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
    feats = {n: [bc] for n, bc in antigens}
    feats[CONTROL_NAME] = [CONTROL_BC]
    assert all(len(bc) == FEAT_LEN for bcs in feats.values() for bc in bcs), "feature barcodes must be 15 bp"

    types, species, classes = classify_antigens(names, offtarget_count)
    types[CONTROL_NAME] = "Decoy"
    species[CONTROL_NAME] = ""
    classes[CONTROL_NAME] = "control"

    combine = None
    if multibarcode:
        if len(names) < 2:
            raise SystemExit(f"--multibarcode needs a panel of >= 2 antigens; got {len(names)}")
        avoid = [bc for bcs in feats.values() for bc in bcs]
        extra_bc = gen_distinct(prng, 2, FEAT_LEN, min_dist=3, avoid=avoid)
        feats[names[0]].append(extra_bc[0])  # first antigen -> 2 barcodes, AND
        feats[names[1]].append(extra_bc[1])  # second antigen -> 2 barcodes, summed
        combine = {n: "sum" for n in feats}
        combine[names[0]] = "all"

    return Panel(names, feats, types=types, species=species, classes=classes, combine=combine)


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
