"""Regenerate the synthetic verdict fixture bed.

Run from this directory:  python generate.py

Stdlib only, and the only random thing is the barcode alphabet soup: every count, name, sample and
set membership below is written out by hand, because each one is load-bearing against a threshold and
a generated number would be load-bearing against nothing. The seed is passed to `random.Random`
rather than seeding the module, so the bed regenerates byte-identically and a second generator
running in the same process cannot disturb this one.

Everything here is invented. The repository is public: no real barcode sequence, antigen name or
sample identifier may appear. Barcodes are drawn from ACGT, antigens are `AgNN`, samples are `SNN`.

The thresholds the counts are chosen against, all shipped defaults in `verdict.py`:

  floor 4                  a reading below this is zeroed before anything else runs
  bound cutoff 75           on `specificity_score`, which is a beta function and not a ratio
  high-reference line 100   a comparator at or above this is flagged as an observation

Against a comparator of 6 the score is 0.0001 at a count of 8, 3.1 at 50, 7.2 at 60 and 100 at 500.
Against a comparator of 60 it is 0.1 at 500 and 100 at 5000. That is why 8 means *not bound*, 500
means *bound* only while the comparator stays at 6, and 5000 is the count that survives the higher
comparator of the two-control panel.
"""

import random

SEED = 20260817
BARCODE_LENGTH = 12

# Slot names, not sequences. The tests never hard-code a sequence; they recover each barcode by the
# role it plays in the panel (two names, two barcodes under one name, declared here and read there),
# so a regenerated bed with different sequences still exercises the same shapes.
ANTIGEN_SLOTS = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7"]
CONTROL_SLOTS = ["R0", "R1"]

# (sample, antigen name, barcode slot). Panels of 3, 4, 4 and 5 tags across four samples.
#
#   A0, A1, A3, A4 each recur under two different names -- the case that makes name-keyed identity
#       wrong, and the reason the pipeline keys on the barcode.
#   A2 recurs under ONE name, so a test can tell "recurs" from "recurs inconsistently".
#   A6 and A7 both carry Ag07: one antigen on two barcodes, read by the highest member.
#   A5 is declared by S03 alone and (see COUNTS) read in S02 alone, which is the only way both
#       directions of the panel-versus-reads check can be seen to run per sample rather than
#       globally: a global check would let S03's declaration excuse the reading in S02.
PANEL = [
    ("S01", "Ag01", "A0"),
    ("S01", "Ag02", "A1"),
    ("S01", "Ag03", "A2"),
    ("S02", "Ag11", "A0"),
    ("S02", "Ag02", "A1"),
    ("S02", "Ag04", "A3"),
    ("S02", "Ag05", "A4"),
    ("S03", "Ag01", "A0"),
    ("S03", "Ag03", "A2"),
    ("S03", "Ag14", "A3"),
    ("S03", "Ag06", "A5"),
    ("S04", "Ag11", "A0"),
    ("S04", "Ag12", "A1"),
    ("S04", "Ag15", "A4"),
    ("S04", "Ag07", "A6"),
    ("S04", "Ag07", "A7"),
]

SAMPLES = ["S01", "S02", "S03", "S04"]

# The comparator rows the two reference beds add, on every sample: a comparator declared in one
# sample and not another is discarded by `consistent_properties` rather than honoured, so a tag is a
# comparator everywhere or nowhere.
CONTROL_NAMES = {"R0": "Ctrl1", "R1": "Ctrl2"}

# (sample, cell, barcode slot, umiCount).
#
# R0 reads 6 in every cell but c11, where it reads 400. The bed is run with --gate-threshold 100, so
# c11 is set aside by the admissibility gate and every identity its set was offered reads *unreliable*.
# That is the bed's only per-cell source of the fourth state, so lowering c11's 400 costs it.
#
# It used to read 1 instead, which fell below a thin-reference line that routed the cell to
# *unreliable*. `count-becomes-a-state` deleted that branch -- no published line separates a thin
# comparator from a usable one -- so the state now comes from the gate, which is a declared parameter.
#
# R1 reads 60, above R0 everywhere it appears, so the two-control panel's comparator is 60 and not 6.
# 60 also sits below the high-reference observation line of 100, keeping that measurement quiet.
# c11 has no R1 row, so it stays impossible to compare on the two-control panel too.
#
# 8 is the *not bound* count: above the floor of 4, so the reading survives to be compared, and
# 0.0001 against a comparator of 6, so it is compared and fails. A count of 2 would be zeroed by the
# floor and would read *not bound* for the wrong reason.
# 500 is the *bound* count against a comparator of 6 (score 100) and a *not bound* count against 60
# (score 0.1) -- that difference is what the two-control bed measures.
# 5000 stays bound against either comparator.
COUNTS = [
    # -- S01, set K01: the three-tag panel, so five of the eight identities were never asked.
    ("S01", "c01", "R0", 6),
    ("S01", "c01", "R1", 60),
    ("S01", "c01", "A0", 5000),
    ("S01", "c01", "A1", 8),
    ("S01", "c01", "A2", 5000),
    ("S01", "c02", "R0", 6),
    ("S01", "c02", "R1", 60),
    ("S01", "c02", "A0", 5000),
    ("S01", "c02", "A1", 8),
    ("S01", "c02", "A2", 5000),
    ("S01", "c03", "R0", 6),
    ("S01", "c03", "R1", 60),
    ("S01", "c03", "A0", 5000),
    ("S01", "c03", "A1", 8),
    # c03 has no A2 row: a cell that was asked and read nothing. It is comparable, so it votes
    # *not bound* against the two cells that bound A2, and the majority still says bound.
    # -- S02, set K02.
    ("S02", "c04", "R0", 6),
    ("S02", "c04", "R1", 60),
    ("S02", "c04", "A0", 500),
    ("S02", "c04", "A1", 8),
    ("S02", "c04", "A3", 500),
    ("S02", "c04", "A4", 8),
    # A5 is read here and declared only by S03. The reading is real and the panel still says S02 was
    # never asked, which is the whole reason the mismatch table has to travel with the answer.
    ("S02", "c04", "A5", 500),
    ("S02", "c05", "R0", 6),
    ("S02", "c05", "R1", 60),
    ("S02", "c05", "A0", 500),
    ("S02", "c05", "A1", 8),
    ("S02", "c05", "A3", 500),
    ("S02", "c05", "A4", 8),
    ("S02", "c06", "R0", 6),
    ("S02", "c06", "R1", 60),
    ("S02", "c06", "A0", 500),
    ("S02", "c06", "A1", 8),
    # 2 is below the floor of 4 and is zeroed, so this is the bed's floored reading and c06 votes
    # *not bound* on A3 while c04 and c05 bind it.
    ("S02", "c06", "A3", 2),
    ("S02", "c06", "A4", 8),
    # -- S03 and S04 together form set K03, so its offered set is the union of two panels and
    #    nothing in it reads *never asked*.
    ("S03", "c07", "R0", 6),
    ("S03", "c07", "R1", 60),
    ("S03", "c07", "A0", 500),
    ("S03", "c07", "A2", 500),
    ("S03", "c07", "A3", 8),
    # No A5 row in S03 at all, though S03 is the only sample that declares it: the other direction
    # of the same check. Both S03 cells were offered A5 and read nothing, so K03 reads *not bound*
    # at A5 -- not *never asked*, which is the regression this shape exists to catch.
    # c08's comparator reads 1: below the floor of 4, but the floor spares a DECLARED comparator, so
    # it survives to be compared (0.96 against 8, 100 against 500 -- the same states a 6 gives). Read
    # without a declaration it is floored like any other count, which is what makes the exemption
    # observable. It used to be c11 that carried this, before c11 was raised to 400 for the gate.
    ("S03", "c08", "R0", 1),
    ("S03", "c08", "R1", 60),
    ("S03", "c08", "A0", 500),
    ("S03", "c08", "A2", 500),
    ("S03", "c08", "A3", 8),
    ("S04", "c09", "R0", 6),
    ("S04", "c09", "R1", 60),
    ("S04", "c09", "A0", 500),
    ("S04", "c09", "A1", 8),
    ("S04", "c09", "A4", 500),
    # A6 and A7 are the two barcodes of Ag07, and the two S04 cells of K03 bind opposite ones. Read
    # per barcode each splits its set one to one and reads *unreliable* on the tie; read as one
    # antigen by the highest member, both cells bind Ag07 and the set reads *bound*.
    ("S04", "c09", "A6", 500),
    ("S04", "c09", "A7", 8),
    ("S04", "c10", "R0", 6),
    ("S04", "c10", "R1", 60),
    ("S04", "c10", "A0", 500),
    ("S04", "c10", "A1", 8),
    ("S04", "c10", "A4", 500),
    ("S04", "c10", "A6", 8),
    ("S04", "c10", "A7", 500),
    # -- S04, set K04: one cell, comparator high enough for the gate to set it aside.
    ("S04", "c11", "R0", 400),
    ("S04", "c11", "A0", 500),
    ("S04", "c11", "A1", 8),
    ("S04", "c11", "A4", 500),
    ("S04", "c11", "A6", 8),
    ("S04", "c11", "A7", 8),
]

# (sample, cell, set). K03 spans two samples on purpose; K04 is a singleton, which many real
# clonotypes are.
LINKER = [
    ("S01", "c01", "K01"),
    ("S01", "c02", "K01"),
    ("S01", "c03", "K01"),
    ("S02", "c04", "K02"),
    ("S02", "c05", "K02"),
    ("S02", "c06", "K02"),
    ("S03", "c07", "K03"),
    ("S03", "c08", "K03"),
    ("S04", "c09", "K03"),
    ("S04", "c10", "K03"),
    ("S04", "c11", "K04"),
]


def barcodes() -> dict[str, str]:
    """A distinct ACGT sequence per slot, in slot order, from the fixed seed."""
    rng = random.Random(SEED)
    assigned: dict[str, str] = {}
    used: set[str] = set()
    for slot in ANTIGEN_SLOTS + CONTROL_SLOTS:
        while True:
            seq = "".join(rng.choice("ACGT") for _ in range(BARCODE_LENGTH))
            if seq not in used:
                break
        used.add(seq)
        assigned[slot] = seq
    return assigned


def write_panel(path: str, seq: dict[str, str], controls: list[str]) -> None:
    with open(path, "w") as f:
        f.write("Samples,Name,Sequence,Type\n")
        for sample, name, slot in PANEL:
            f.write(f"{sample},{name},{seq[slot]},Target\n")
        for sample in SAMPLES:
            for slot in controls:
                f.write(f"{sample},{CONTROL_NAMES[slot]},{seq[slot]},Control\n")


# --- the two shapes real panel files arrive in --------------------------------------------------
#
# Projections of the same slots, samples and names as the panels above, so counts.csv and linker.csv
# apply to them unchanged and the three panels differ only in the shape of the declaration. Both
# shapes were observed in use at one account, at the same time, on two of its projects.
#
# NEITHER carries a value meaning "comparator", and that is the point of them. In both, the negative
# control is one antigen the scientist points at by name in the interface -- so a run over either
# resolves to the panel's own readings, and `--reference-values` has nothing correct to name. The
# `Type` column of the wide shape declares what a member is TO THE QUESTION (a target, an off-target),
# which is a different axis from what a count is read against. Naming `Off-Target` as the comparator
# does not merely mis-set a baseline: reference tags are held out of the identity universe, so every
# off-target stops being asked about at all -- the question an off-target exists to pose is deleted.

# A per-slot catalogue id, 1:1 with the sequence. Real files carry both, and a reader who points the
# barcode role at the catalogue id instead of the sequence joins to nothing.
CATALOGUE_IDS = {slot: f"T{100 + i:04d}" for i, slot in enumerate(ANTIGEN_SLOTS + CONTROL_SLOTS)}

# Four distinct values, two of which are one channel spelled two ways -- so grouping on this column
# splits one channel in two.
CHANNELS = {
    "A0": "PE", "A1": "PE",
    "A2": "APC", "A3": "APC",
    "A4": "PE Dazzle", "A5": "PE Dazzle",
    "A6": "PE-Dazzle 5120", "A7": "PE-Dazzle 5120",
    "R0": "APC", "R1": "APC",
}

# One value on every row: a declared column that carries no information at all. Grouping on it puts
# every tag in one identity, which is legal and useless.
RESIDUES = "ECD protein"

# What each member is to the question. No "Control" anywhere, deliberately.
TYPES = {
    "A0": "Target (Primary)", "A1": "Target (Primary)",
    "A2": "Off-Target", "A3": "Target (Secondary)",
    "A4": "Target (Secondary)", "A5": "Off-Target",
    "A6": "Target (Primary)", "A7": "Target (Primary)",
    "R0": "Off-Target", "R1": "Off-Target",
}

# Case variants, because the observed file carried six values that were three roles. Two failure
# modes, kept separate so a test can tell them apart:
#
#   A0 reads "Target (Primary)" in S01/S03 and "Target (primary)" in S02/S04. One barcode, two
#   values, so the property is dropped for that tag entirely -- it ends up with no role at all.
#
#   A5 reads "Off-target" everywhere it appears. Self-consistent, so it keeps its role, but it no
#   longer matches A2's "Off-Target" -- so selecting one role value silently misses the other.
LOWERCASED_IN = {"A0": {"S02", "S04"}}
ALWAYS_LOWERCASED = {"A5"}


def _typed(slot: str, sample: str) -> str:
    role = TYPES[slot]
    if slot in ALWAYS_LOWERCASED or sample in LOWERCASED_IN.get(slot, set()):
        # Lowercase only the parenthesised qualifier or the word after the hyphen, which is how the
        # observed variants differed -- not a blanket .lower().
        return role.replace("(P", "(p").replace("(S", "(s").replace("-Target", "-target")
    return role


def write_panel_narrow(path: str, seq: dict[str, str]) -> None:
    """Three columns and no fourth: sample, barcode, antigen name.

    The declared control is present as an ordinary antigen row, exactly as it is in the observed file
    -- nothing in the table says it is the control.
    """
    with open(path, "w") as f:
        f.write("Sample,Sequence,Antigen\n")
        for sample, name, slot in PANEL:
            f.write(f"{sample},{seq[slot]},{name}\n")
        for sample in SAMPLES:
            f.write(f"{sample},{seq['R0']},{CONTROL_NAMES['R0']}\n")


def write_panel_wide(path: str, seq: dict[str, str]) -> None:
    """Seven columns: sample, name, catalogue id, barcode, channel, a constant, and the role."""
    with open(path, "w") as f:
        f.write("Samples,Name,Barcode,Sequence,Channel,Residues,Type\n")
        for sample, name, slot in PANEL:
            f.write(
                f"{sample},{name},{CATALOGUE_IDS[slot]},{seq[slot]},"
                f"{CHANNELS[slot]},{RESIDUES},{_typed(slot, sample)}\n"
            )
        for sample in SAMPLES:
            slot = "R0"
            f.write(
                f"{sample},{CONTROL_NAMES[slot]},{CATALOGUE_IDS[slot]},{seq[slot]},"
                f"{CHANNELS[slot]},{RESIDUES},{_typed(slot, sample)}\n"
            )


def main() -> None:
    seq = barcodes()
    write_panel("panel.csv", seq, [])
    write_panel("panel_with_reference.csv", seq, ["R0"])
    write_panel("panel_multi_reference.csv", seq, ["R0", "R1"])
    write_panel_narrow("panel_narrow.csv", seq)
    write_panel_wide("panel_wide.csv", seq)

    with open("counts.csv", "w") as f:
        f.write("sampleId,cellId,tag,umiCount\n")
        for sample, cell, slot, umi in COUNTS:
            f.write(f"{sample},{cell},{seq[slot]},{umi}\n")

    with open("linker.csv", "w") as f:
        f.write("sampleId,cellId,setId\n")
        for sample, cell, set_id in LINKER:
            f.write(f"{sample},{cell},{set_id}\n")


if __name__ == "__main__":
    main()
