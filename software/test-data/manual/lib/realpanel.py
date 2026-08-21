"""A run built on a REAL, externally-supplied panel file, at cohort scale, with a deliberate spread of
reading quality.

Everything the other presets synthesize — sample names, antigen names, feature barcodes, per-antigen
roles — this path READS from a panel CSV whose path is given on the command line. Nothing about that
panel is baked in here: this module carries column-name defaults for the wide panel shape and nothing
else, so a real (and possibly confidential) panel can drive a run without any of it entering the
repository. The panel is copied verbatim into the run directory, which is gitignored like every other
generated artefact.

Three things this path does that no other preset does:

  1. **Per-sample panels.** A wide panel declares, per row, which sample offers which antigen, so each
     sample has its own panel and a barcode SEQUENCE may carry a different antigen name in a different
     sample. That is not a defect to be normalised away — it is tag-inventory reuse, and the block's
     `sampleColumn` exists for it. Reads are generated per sample against that sample's own panel, so
     the reuse is real in the data and not only in the CSV.

  2. **No declared comparator.** A real panel's role column names what a member is TO THE QUESTION
     (target, off-target) and carries no value meaning "negative control". So this path plants NO
     control feature. Background lives on the panel's own members, which is what forces the run down
     the panel-reference (or no-reference) path rather than the declared-reference one.

  3. **A reading-quality mix.** Every cell is planted at one of eight named tiers, from a clean strong
     binder down to pure noise, chosen so each tier lands in a KNOWN verdict state — bound / not bound /
     unreliable — and the tier is written to the truth table. A run therefore carries good, medium and
     bad readings together, in stated proportions, instead of one uniform signal strength.

Read geometry is the real BEAM one — R2 = 10 bp lead-in + 15 bp feature + tail — because these are
antigen-capture barcodes read out by that chemistry. `--offset 0` puts the feature at position 0 for
the generic geometry instead.

Deterministic and standard-library only, like the rest of this bed.
"""

import csv
import os
import shutil
import statistics as st

from . import vdj
from .common import (
    CELL_LEN,
    FEAT_LEN,
    R2_FILLER,
    UMI_LEN,
    gen_cells,
    gen_distinct,
    mutate,
    new_rng,
    rand_seq,
    write_fastq_gz,
)

REALPANEL_SEED = 20260820
# Share of reads carrying a 1 bp error in the FEATURE barcode. A real library measured 1-2% of
# reads as Hamming-1 variants of the dominant barcode, which tag refinement corrects back.
SEQ_ERROR_FRAC = 0.015
R2_TAIL = "TTAATTAATT"  # neutral remainder after the feature barcode (captured by R2:* and ignored)

# Default column names for the wide panel shape: sample, antigen name, catalogue id, barcode sequence,
# detection channel, a constant, role. Generic headers for a generic shape — override any of them on the
# command line when a panel spells them differently.
DEFAULT_COLUMNS = {
    "sample": "Samples",
    "name": "Name",
    "sequence": "Sequence",
    "role": "Type",
}

# Which role values mean "this member is the question" vs "this member is the comparator the question is
# read against". Matched case-insensitively on the leading word so `Target (Primary)`, `Target
# (Secondary)` and a bare `Target` all read as on-target. A panel using other words needs
# --target-roles / --offtarget-roles.
DEFAULT_TARGET_ROLES = ("target",)
DEFAULT_OFFTARGET_ROLES = ("off-target", "offtarget", "off target")

# The NARROW panel shape: sample, antigen name, barcode sequence — and no role column at all. This is
# the shape the production in-vivo project actually uploads, so it is not an edge case. Role is
# carried in the antigen NAME instead, and the comparator is chosen by naming one member.
NARROW_COLUMNS = {
    "sample": "Sample",
    "name": "Antigen",
    "sequence": "Sequence",
}

# Generic role words looked for in an antigen NAME when the panel declares no role column. These are
# industry words, not any panel's own vocabulary: nothing from a real panel belongs in this file.
# Matched case-insensitively as substrings, because a name carries them mid-string ("... (high OT
# risk)") rather than as a leading token the way a role COLUMN does.
NAME_OFFTARGET_HINTS = (
    "off-target", "off target", "offtarget", "ot risk", "high ot",
    "homology", "decoy", "irrelevant", "unrelated", "negative control", "neg ctrl",
)


# --- reading-quality tiers ------------------------------------------------------------------------
#
# (name, weight, doc). The weights are the shares of a sample's cells. Magnitudes are the real
# 5k-cell BEAM library's: dominant ~600 distinct UMIs (p10 ~18, p90 ~1490), near-mono dominance
# (median ~1.0, p10 ~0.79), background ~3 UMIs/cell. Each tier is chosen to land in a known verdict
# state given the block's defaults (count floor 4, a specificity cutoff in the 90s), so the truth
# table's `tier` column is an expectation and not a label.
TIERS = [
    ("strong", 0.28, "a clean high-count binder on one target; background at the floor -> bound"),
    ("good", 0.20, "a solid binder, an order less signal than strong -> bound"),
    ("medium", 0.16, "moderate signal with a real second reading -> lands on both sides of the line"),
    ("weak", 0.11, "a real reading clear of the count floor but far below the line -> not bound"),
    ("noise", 0.09, "every member within a couple of UMIs of nothing -> floored, not bound"),
    ("crossreactive", 0.06, "two on-target members co-dominant -> neither alone dominant"),
    ("offtarget", 0.06, "the dominant reading is an OFF-target member -> a high comparator, no lead"),
    ("gated", 0.04, "comparator reads as high as the target -> set aside by the admissibility gate"),
]
TIER_NAMES = [t[0] for t in TIERS]


# --- the two measured regimes --------------------------------------------------------------------
#
# Two calibrations exist, both measured, and they disagree by more than an order of magnitude. Which
# one a bed should carry is a question about which library it stands in for.
#
#   deep    - the public 10x BEAM runs. Antigen libraries sequenced to ~97% saturation: ~33 reads per
#             recovered UMI, a median of 200 antigen UMIs per called cell, near-mono dominance
#             (median 0.995), and cell CALLING applied before anything is reported.
#   shallow - real in-vivo BEAM libraries, measured from a production in-vivo deployment on
#             2026-08-21. 2.7-5.8 reads per distinct UMI; a median of 7 UMIs across the barcodes that
#             clear a floor of 4; dominance around 0.44; no cell calling and no whitelist, so the raw
#             barcode universe is what the block sees; and unfiltered antigen aggregates holding most
#             of the library.
#
# `deep` is kept because it reproduces every run made before 2026-08-21 byte for byte. `shallow` is
# the one that stands in for real production data. Neither is right on its own: a bed carrying only `deep`
# tests a regime real in-vivo data never occupies, and a bed carrying only `shallow` cannot show the
# block reaching a confident answer at all.
#
# MAGNITUDES entries are the inclusive (lo, hi) UMI range a tier plants on its dominant member.
MAGNITUDES_DEEP = {
    "strong": (500, 1400),
    "good": (150, 500),
    "medium": (60, 200),
    "weak": (15, 45),
    "noise": (1, 4),
    "cross": (300, 1100),
    "offtarget": (300, 1000),
    "gated_target": (100, 400),
    "gated_ref": (400, 1200),
}
# Scaled to the measured shallow distribution: among barcodes clearing a floor of 4 the per-barcode
# totals run p25 5, median 7, p75 11, p90 17, p99 201. So every tier but the top sits in single or
# low double digits, and `strong` carries the p99 tail rather than the bulk.
MAGNITUDES_SHALLOW = {
    "strong": (40, 150),
    "good": (12, 35),
    "medium": (6, 12),
    "weak": (4, 8),
    "noise": (1, 3),
    "cross": (6, 20),
    "offtarget": (6, 20),
    "gated_target": (4, 12),
    "gated_ref": (12, 40),
}

# Tier weights. `deep` makes a clean binder the common case; `shallow` makes a sub-floor reading the
# common case, which is what 63-80% of real barcodes measured as.
TIERS_SHALLOW = [
    ("strong", 0.05, "the p99 tail: a real binder at tens to hundreds of UMIs -> bound"),
    ("good", 0.10, "a reading clear of the floor with some margin -> bound or on the line"),
    ("medium", 0.20, "single-digit UMIs against a comparable background -> on the line"),
    ("weak", 0.30, "barely clear of the count floor -> not bound"),
    ("noise", 0.25, "every member sub-floor -> floored, nothing to answer with"),
    ("crossreactive", 0.05, "two members co-dominant at single-digit counts"),
    ("offtarget", 0.03, "an off-target member is the dominant reading"),
    ("gated", 0.02, "the comparator reads above the target"),
]

# _background() parameters. At shallow depth background is not a floor UNDER the signal, it is
# COMPARABLE TO it: a dominant of 7 with one UMI on each of five other members gives a dominance
# fraction of 0.44, which is the measured median. That is counting noise rather than promiscuity, and
# reproducing it is the whole point of the regime.
BACKGROUND_DEEP = {"offtarget_hi": 6, "target_hi": 3, "offtarget_p": 0.85, "target_p": 0.35}
BACKGROUND_SHALLOW = {"offtarget_hi": 3, "target_hi": 3, "offtarget_p": 0.70, "target_p": 0.65}

REGIMES = {
    "deep": {
        "tiers": TIERS,
        "magnitudes": MAGNITUDES_DEEP,
        "background": BACKGROUND_DEEP,
        # None selects the original three-branch duplication draw (mean ~1.3 reads/UMI). Kept as its
        # own branch, not a special case of the geometric one, so the RNG call sequence is unchanged
        # and pre-2026-08-21 runs still reproduce byte for byte.
        "dup_mean": None,
        "ambient_frac": 0.18,
        # 0 leaves the ambient barcode COUNT following from the read share alone, as it always did.
        "ambient_barcode_ratio": 0.0,
        "aggregates": 0,
        "aggregate_umi_share": 0.0,
        "clonal_profile": "immunized",
        "clonal_mean_size": 25,
        "clonal_singleton_cell_frac": 0.10,
        "clonal_tail_cycle": None,
        "unpaired_frac": 0.0,
        "seq_error_frac": SEQ_ERROR_FRAC,
    },
    "shallow": {
        "tiers": TIERS_SHALLOW,
        "magnitudes": MAGNITUDES_SHALLOW,
        "background": BACKGROUND_SHALLOW,
        # 2.7 and 5.8 reads per distinct UMI measured across the two measured libraries; 4 sits
        # between them. Drawn geometrically, which the capped three-branch draw cannot reach.
        "dup_mean": 4.0,
        "ambient_frac": 0.25,
        # ~700k raw barcodes against a few thousand real cells. This is the single largest divergence
        # from the old bed: the block reports "cells detected" off the raw universe, so the universe
        # IS the QC number a user reads.
        "ambient_barcode_ratio": 100.0,
        # Five barcodes held 58.9% of one library's antigen UMIs; the largest held 18.3% by itself.
        "aggregates": 5,
        "aggregate_umi_share": 0.59,
        # 4,549 IGHeavy clonotypes over 4,773 cells with paired chains = 1.05 cells per clonotype.
        # Expressed through the existing knobs: 90% of cells left as singletons, the rest in clones
        # averaging 3, which lands at ~1.07.
        "clonal_profile": "immunized",
        "clonal_mean_size": 2,
        "clonal_singleton_cell_frac": 0.97,
        "clonal_tail_cycle": vdj.TAIL_CYCLE_SPARSE,
        # Clonotypes dropped for want of a pair outnumbered paired ones in both libraries.
        "unpaired_frac": 0.35,
        # "Fraction unrecognized antigen" measured at 4.22% on the public run, against the 1.5% this
        # bed used.
        "seq_error_frac": 0.042,
    },
}


class SamplePanel:
    """One sample's panel: ordered member names, name -> 15 bp barcode, and each member's role.

    `targets` / `offtargets` split the members by the role column. Both may be empty — a panel is
    whatever the file says it is — and every planter below degrades to the next-best tier rather than
    failing when a sample cannot support the one it was asked for."""

    def __init__(self, sample, members, target_roles, offtarget_roles):
        self.sample = sample
        self.names = [m["name"] for m in members]
        self.barcode = {m["name"]: m["sequence"] for m in members}
        self.role = {m["name"]: m["role"] for m in members}
        self.rows = members  # the panel's own rows, verbatim, for the run report
        self.targets = [n for n in self.names if _role_in(self.role[n], target_roles)]
        self.offtargets = [n for n in self.names if _role_in(self.role[n], offtarget_roles)]

    @property
    def barcodes(self):
        return [self.barcode[n] for n in self.names]


def _role_in(value, words):
    """True when a role value starts with one of `words` (case- and space-insensitive). Matching on the
    leading word is what lets `Target (Primary)` and `Target (Secondary)` both read as on-target while
    staying two distinct values in the panel — which is what they are, and what the block groups on."""
    v = " ".join((value or "").split()).lower()
    return any(v == w or v.startswith(w) for w in words)


def _infer_role_from_name(name, control_feature=None):
    """Role for a member the panel gave no role column for.

    A narrow panel still carries role information — it is in the antigen name. `control_feature`, when
    given, names the one member serving as the comparator and wins outright; that mirrors the block,
    where the user picks a control by name from a dropdown of antigen names. Otherwise the name is
    searched for the generic off-target words in NAME_OFFTARGET_HINTS.

    A member matching nothing comes back on-target, which is the safe default: mistaking a target for a
    comparator would silently move the line every reading is judged against."""
    if control_feature and name.strip().lower() == control_feature.strip().lower():
        return "Off-Target"
    low = name.lower()
    return "Off-Target" if any(h in low for h in NAME_OFFTARGET_HINTS) else "Target"


def detect_panel_shape(csv_path, columns=None):
    """"wide" if the panel declares a role column, else "narrow". Reads only the header."""
    cols = dict(DEFAULT_COLUMNS)
    cols.update(columns or {})
    if not os.path.exists(csv_path):
        raise SystemExit(f"panel file not found: {csv_path}")
    with open(csv_path, newline="") as fh:
        header = csv.DictReader(fh).fieldnames or []
    return "wide" if cols["role"] in header else "narrow"


def load_panel(csv_path, columns=None, target_roles=DEFAULT_TARGET_ROLES,
               offtarget_roles=DEFAULT_OFFTARGET_ROLES, shape="auto", control_feature=None):
    """Read a panel CSV into {sample: SamplePanel}, in file order. Handles both shapes seen in use.

    WIDE declares a role column and this reads it. NARROW declares none — sample, antigen, sequence and
    nothing else — and role is then inferred from the antigen name, or from `control_feature` where one
    is named. Both shapes are live in production on different projects, so neither is the
    exception: a loader that only reads a role column models the wrong half of real production work.

    Validates only what generation cannot proceed without: the named columns exist, every sequence is
    15 bp, and no sequence appears twice within one sample (which would be a genuine duplicate the
    block's own guard rejects). Sequence reuse ACROSS samples is left alone — it is the point of the
    per-sample keying, not an error."""
    if shape == "auto":
        shape = detect_panel_shape(csv_path, columns)
    if shape not in ("wide", "narrow"):
        raise SystemExit(f"unknown panel shape {shape!r}; expected wide, narrow or auto")
    cols = dict(DEFAULT_COLUMNS if shape == "wide" else NARROW_COLUMNS)
    cols.update(columns or {})
    if not os.path.exists(csv_path):
        raise SystemExit(f"panel file not found: {csv_path}")
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        needed = ["sample", "name", "sequence"] + (["role"] if shape == "wide" else [])
        missing = [cols[k] for k in needed if cols[k] not in header]
        if missing:
            raise SystemExit(
                f"{csv_path} has no column(s) {missing}; its columns are {header}. "
                "Name the right ones with --panel-sample-col / --panel-name-col / --panel-seq-col / "
                "--panel-role-col."
            )
        by_sample = {}
        order = []
        for lineno, row in enumerate(reader, start=2):
            sample = (row[cols["sample"]] or "").strip()
            name = " ".join((row[cols["name"]] or "").split())
            seq = (row[cols["sequence"]] or "").strip().upper()
            role = ((row[cols["role"]] or "").strip() if shape == "wide"
                    else _infer_role_from_name(name, control_feature))
            if not sample or not name or not seq:
                raise SystemExit(f"{csv_path}:{lineno}: sample, name and sequence must all be present")
            if len(seq) != FEAT_LEN:
                raise SystemExit(f"{csv_path}:{lineno}: sequence {seq!r} is {len(seq)} bp, expected {FEAT_LEN}")
            if sample not in by_sample:
                by_sample[sample] = []
                order.append(sample)
            if any(m["sequence"] == seq for m in by_sample[sample]):
                raise SystemExit(
                    f"{csv_path}:{lineno}: sequence {seq} appears twice in sample {sample!r} — a real "
                    "duplicate, which the block rejects. Fix the panel."
                )
            by_sample[sample].append({"name": name, "sequence": seq, "role": role, "row": row})
    if not by_sample:
        raise SystemExit(f"{csv_path} has no data rows")
    return {s: SamplePanel(s, by_sample[s], target_roles, offtarget_roles) for s in order}


def load_wide_panel(csv_path, columns=None, target_roles=DEFAULT_TARGET_ROLES,
                    offtarget_roles=DEFAULT_OFFTARGET_ROLES):
    """Wide-shape loader. Kept as the name the tests and earlier callers use; `load_panel` is the one
    that handles both shapes."""
    return load_panel(csv_path, columns, target_roles, offtarget_roles, shape="wide")


# --- per-cell planting ---------------------------------------------------------------------------

def pick_tier(rng, tiers=None):
    """One tier name, by weight. A single random draw per cell so the stream stays stable when the tier
    table changes weight but not order."""
    tiers = tiers or TIERS
    r = rng.random()
    acc = 0.0
    for name, weight, _doc in tiers:
        acc += weight
        if r < acc:
            return name
    return tiers[-1][0]


def _background(rng, panel, exclude, offtarget_hi=6, target_hi=3, offtarget_p=0.85, target_p=0.35):
    """Background readings on the members that are not the cell's dominant one.

    OFF-TARGET members are treated differently from other targets, and the difference is load-bearing.
    A panel with no declared negative control is read against its off-target members, so an off-target
    reading is this cell's COMPARATOR — and a comparator below the reference thin line (2 by default)
    makes the position *unreliable*, not *not bound*: the comparison could not be made. Plant the
    off-targets sparsely and almost every cell in the run comes back unreliable, which says nothing
    about binding and is an artefact of the bed, not a finding.

    So off-targets read in ~85% of cells at 2-`offtarget_hi` UMIs — a comparator that can be compared
    against — while the remaining on-target members stay at true background, present in ~35% of cells
    at 1-`target_hi`. The real library measured ~3 members read per cell and a background median of ~3
    UMIs, which both of these sit inside. The ~15% of cells with no off-target reading are left alone
    on purpose: *unreliable for want of a comparator* is a state the block has to be able to show, and
    a run with none of it cannot show it."""
    out = {}
    for name in panel.names:
        if name in exclude:
            continue
        if name in panel.offtargets:
            if rng.random() < offtarget_p:
                out[name] = rng.randint(2, max(2, offtarget_hi))
        elif rng.random() < target_p:
            out[name] = rng.randint(1, max(1, target_hi))
    return out


def plant_cell(rng, panel, tier, primary_bias=0.0, mag=None, bg=None):
    """Plant one cell's per-member distinct-UMI counts at `tier`.

    Returns (per_member, consensus, tier_actually_used). `consensus` is what the VDJ arm groups
    clonotypes on: a member NAME when the cell has one clear dominant, else the tier word. A tier a
    sample's panel cannot support (no off-target member, fewer than two targets) degrades to `good`,
    and the returned tier says so, so the truth table never claims a tier the data does not hold.

    `primary_bias` tilts the dominant choice toward the panel's FIRST target — an antigen-sorted library
    is not a uniform draw over its panel, and a real one had a single antigen at 90% of the library.

    `mag` and `bg` come from the regime (see REGIMES). They carry only MAGNITUDES; every relative
    decision — which member is dominant, how a tier degrades, how the second reading relates to the
    first — is regime-independent and stays here. Defaulting them to the deep tables keeps the RNG
    call sequence identical to what this function did before regimes existed."""
    mag = mag or MAGNITUDES_DEEP
    bg = bg or BACKGROUND_DEEP
    targets = panel.targets or panel.names
    offtargets = panel.offtargets

    def choose_target():
        if primary_bias > 0 and targets and rng.random() < primary_bias:
            return targets[0]
        return rng.choice(targets)

    if tier == "noise":
        # Deliberately thin, off-targets included: nothing here clears the count floor, and a cell whose
        # off-target reading lands at 0 or 1 has no comparator at all. Both outcomes are real and this is
        # the tier they come from, so the rest of the run does not have to carry them.
        per = {n: rng.randint(*mag["noise"]) for n in panel.names if rng.random() < 0.6}
        if not per:
            per = {rng.choice(panel.names): rng.randint(*mag["noise"])}
        return per, "noise", tier

    if tier == "crossreactive":
        if len(targets) < 2:
            tier = "good"
        else:
            a, b = rng.sample(targets, 2)
            dom = rng.randint(*mag["cross"])
            per = {a: dom, b: max(1, int(dom * rng.uniform(0.85, 1.0)))}
            per.update(_background(rng, panel, set(per), **bg))
            return per, "crossreactive", tier

    if tier == "offtarget":
        if not offtargets:
            tier = "good"
        else:
            dom = rng.choice(offtargets)
            per = {dom: rng.randint(*mag["offtarget"])}
            per.update(_background(rng, panel, {dom}, **bg))
            return per, dom, tier

    if tier == "gated":
        if not offtargets:
            tier = "medium"
        else:
            tgt = choose_target()
            ref = rng.choice(offtargets)
            per = {tgt: rng.randint(*mag["gated_target"]), ref: rng.randint(*mag["gated_ref"])}
            per.update(_background(rng, panel, set(per), **bg))
            return per, tgt, tier

    tgt = choose_target()
    if tier == "strong":
        per = {tgt: rng.randint(*mag["strong"])}
        per.update(_background(rng, panel, {tgt}, **bg))
    elif tier == "good":
        per = {tgt: rng.randint(*mag["good"])}
        per.update(_background(rng, panel, {tgt}, **bg))
    elif tier == "medium":
        # Straddles the line on purpose. With the off-target members serving as the comparator, a cell's
        # reference reading is the MAX over them — about 5 UMIs at this background — and the antigen count
        # that reaches a specificity of 75 against a reference of 5 is about 120. 60-200 therefore lands
        # on both sides of the line, which is the only way to see where the line is.
        dom = rng.randint(*mag["medium"])
        per = {tgt: dom}
        # The second reading goes on another TARGET where the panel has one. On an off-target it would
        # raise this cell's own comparator and turn a near-the-line cell into a comparator-dominated one
        # — which is what the `offtarget` tier is for, and mixing the two makes neither legible.
        rest = [n for n in (panel.targets or panel.names) if n != tgt]
        if rest:
            per[rng.choice(rest)] = max(1, int(dom * rng.uniform(0.35, 0.65)))
        per.update(_background(rng, panel, set(per),
                               **dict(bg, offtarget_hi=bg["offtarget_hi"] + 2,
                                      target_hi=bg["target_hi"] + 3)))
    elif tier == "weak":
        # Clear of the count floor of 4, so every reading here is a reading and answers *not bound* —
        # not the same thing as the floored readings the `noise` tier produces, which answer nothing.
        dom = rng.randint(*mag["weak"])
        per = {tgt: dom}
        per.update(_background(rng, panel, {tgt},
                               **dict(bg, offtarget_hi=max(4, dom // 3), target_p=0.5)))
    else:
        raise AssertionError(f"unhandled tier {tier!r}")
    return per, tgt, tier


# --- read emission -------------------------------------------------------------------------------

def _r2(barcode, offset):
    """R2 for one read. offset=10 is the real BEAM geometry (10 bp lead-in before the feature);
    offset=0 puts the feature at position 0, the generic feature-barcode geometry."""
    return (R2_FILLER[:offset] if offset else "") + barcode + R2_TAIL


def _dup_count(rng, dup_mean):
    """How many reads one distinct UMI produces.

    `dup_mean is None` keeps the original capped three-branch draw (mean ~1.3 reads/UMI). It is left as
    its own branch rather than a special case of the geometric one so the RNG call sequence is
    unchanged and pre-regime runs reproduce byte for byte.

    Otherwise a geometric draw with mean `dup_mean`. The capped draw tops out at 3 and so cannot reach
    the 2.7-5.8 reads per distinct UMI real in-vivo libraries measure; this can. Capped at 64 so a
    pathological tail cannot dominate a run."""
    if dup_mean is None:
        return 1 if rng.random() < 0.75 else (2 if rng.random() < 0.8 else 3)
    p = 1.0 / max(1.0, dup_mean)
    n = 1
    while n < 64 and rng.random() > p:
        n += 1
    return n


def emit_cell_reads(rng, reads, sample, panel, cell, per_member, offset, seq_error_frac, read_no,
                    dup_mean=None):
    """Append the reads one planted cell produces. Distinct UMIs per member, PCR duplication per
    `dup_mean`, and `seq_error_frac` of reads carrying a 1 bp error in the FEATURE barcode — the
    Hamming-1 variants a real library shows and tag refinement corrects back."""
    for member, k in per_member.items():
        bc = panel.barcode[member]
        umis = set()
        while len(umis) < k:
            umis.add(rand_seq(rng, UMI_LEN))
        # sorted(): set iteration order of strings varies per process (PYTHONHASHSEED), which would make
        # the emitted read order non-reproducible.
        for umi in sorted(umis):
            dups = _dup_count(rng, dup_mean)
            for _ in range(dups):
                read_no += 1
                emitted = mutate(rng, bc) if seq_error_frac and rng.random() < seq_error_frac else bc
                reads.append([f"{sample}_read{read_no}", cell + umi, _r2(emitted, offset), 1])
    return read_no


def add_ambient(rng, panel, reads, offset, frac, n_cells=0, barcode_ratio=0.0, dup_mean=None):
    """Append ambient reads on OFF-cell barcodes: random 16-mers carrying on-panel features.

    Two modes, and the difference is the largest single divergence between this bed and the measured
    real data.

    `barcode_ratio == 0` (the original): the ambient READ SHARE is `frac`, and the barcode count falls
    out of it — one barcode per ambient read pair, so ~18% of reads become a modest phantom population.

    `barcode_ratio > 0`: the barcode UNIVERSE is sized directly, at `n_cells * barcode_ratio` distinct
    barcodes. This is the mode that matters. The block applies no cell calling and the live
    configuration sets no whitelist, so what it reports as "cells detected" is the raw barcode
    universe: 1,374,025 of them across two samples, with a MEDIAN of one UMI each. Every QC number
    downstream inherits that. A bed whose barcodes are all real cells cannot reproduce a single one of
    those numbers.

    The per-barcode UMI shape is drawn to match: ~85% carry exactly one UMI, the rest a decaying tail.
    The measured whole-table distribution was p50 1, p75 1, p90 16, p99 61.

    Ambient barcodes stay out of the truth tables. A whitelist drops them, de-novo correction keeps
    them as phantom low-count cells, and both behaviours are worth having data for."""
    if frac <= 0 and barcode_ratio <= 0:
        return 0
    bcs = panel.barcodes
    base = len(reads)
    if barcode_ratio > 0:
        n_bc = int(n_cells * barcode_ratio)
        planted_umis = 0
        for i in range(n_bc):
            cell = rand_seq(rng, CELL_LEN)
            # Median one UMI with a long tail. The measured whole-table shape was p50 1, p75 1-6,
            # p90 10-16, p99 53-61 — so a majority of singletons is not enough on its own; the tail has
            # to reach the tens or the panel median a comparator rests on comes out too clean.
            k = 1 if rng.random() < 0.62 else 1 + int(rng.expovariate(1 / 9.0))
            k = min(k, 400)
            planted_umis += k
            for _ in range(k):
                umi = rand_seq(rng, UMI_LEN)
                r2 = _r2(rng.choice(bcs), offset)
                for _ in range(_dup_count(rng, dup_mean)):
                    reads.append([f"ambient_read{base + i}", cell + umi, r2, 1])
        return planted_umis
    n = int(len(reads) * frac)
    for i in range(n):
        cell = rand_seq(rng, CELL_LEN)
        umi = rand_seq(rng, UMI_LEN)
        r2 = _r2(rng.choice(bcs), offset)
        for _ in range(rng.randint(1, 2)):
            reads.append([f"ambient_read{base + i}", cell + umi, r2, 1])
    return n


# Relative UMI shares of the five aggregate barcodes measured in one 200k-barcode window of the
# larger measured library, normalised within the aggregate population. The largest held 18.3% of the
# WHOLE library's antigen UMIs on its own; the five together held 58.9%.
AGGREGATE_PROFILE = (0.311, 0.266, 0.183, 0.168, 0.072)


def add_aggregates(rng, panel, reads, offset, n_aggregates, umi_share, other_umis, dup_mean=None):
    """Append antigen-aggregate barcodes: a handful of droplets holding most of the library.

    Proteins clump nonspecifically during sample prep and the resulting GEMs carry enormous UMI counts.
    Cell Ranger detects and removes exactly this population BEFORE cell calling. This block does not,
    and until now this bed contained none — so nothing in the bed exercised what an aggregate does to a
    panel median, a comparator, or a "cells detected" count.

    `umi_share` is the share of the FINISHED library's UMIs these barcodes hold, so the count planted is
    `other_umis * share / (1 - share)` where `other_umis` is every non-aggregate UMI already in the
    library — signal AND ambient. Sizing it against signal alone under-plants badly once the barcode
    universe is large, because the universe holds most of the non-aggregate UMIs.

    At the measured 0.59 the aggregates outnumber everything else about 1.4 to 1 — which is why the
    measured per-cell depth is starved even though those libraries are large. Most of the sequencing
    went into five droplets.

    UMIs are spread over several panel features per barcode, because an aggregate is nonspecific: a
    clump binds whatever is nearby, and a single-feature aggregate would read as an extremely confident
    binder rather than as junk."""
    if n_aggregates <= 0 or umi_share <= 0 or other_umis <= 0:
        return []
    total = int(other_umis * umi_share / max(1e-9, 1.0 - umi_share))
    profile = list(AGGREGATE_PROFILE[:n_aggregates])
    while len(profile) < n_aggregates:
        profile.append(profile[-1] / 2)
    scale = sum(profile)
    planted = []
    bcs = panel.barcodes
    for i, w in enumerate(profile):
        cell = rand_seq(rng, CELL_LEN)
        k = max(1, int(total * w / scale))
        members = rng.sample(bcs, min(len(bcs), rng.randint(2, max(2, min(5, len(bcs))))))
        planted.append((cell, k))
        for j in range(k):
            umi = rand_seq(rng, UMI_LEN)
            r2 = _r2(members[j % len(members)], offset)
            for _ in range(_dup_count(rng, dup_mean)):
                reads.append([f"aggregate_read{i}_{j}", cell + umi, r2, 1])
    return planted


def convert_offpanel(rng, reads, off_bcs, off_frac, offset):
    """Rewrite `off_frac` of reads onto barcodes NOT in any sample's panel (Hamming >= 5 from every
    panel member, so refinement drops rather than corrects them). Drives the panel-assigned fraction
    the block's QC reports, and with it the Quality tag."""
    if off_frac <= 0 or not off_bcs:
        return
    k = min(len(reads), int(len(reads) * off_frac))
    for i in rng.sample(range(len(reads)), k):
        reads[i][2] = _r2(rng.choice(off_bcs), offset)


def add_malformed(rng, reads, matched_frac):
    """Append reads parse cannot read at all — R1 too short for CELL+UMI, or R2 too short for the
    feature — so the matched fraction lands near `matched_frac`."""
    if matched_frac >= 1.0:
        return
    m = max(0.01, matched_frac)
    n_bad = int(len(reads) * (1 - m) / m)
    base = len(reads)
    for i in range(n_bad):
        if i % 2 == 0:
            reads.append([f"malformed_read{base + i}", rand_seq(rng, 18), rand_seq(rng, FEAT_LEN), 1])
        else:
            reads.append([f"malformed_read{base + i}", rand_seq(rng, 26), rand_seq(rng, 10), 1])


# --- per-sample library quality ------------------------------------------------------------------
#
# What reaches the block as a library, before any per-cell reading is read. (matched fraction,
# panel-assigned fraction) drive the block's Read-recovery bar and Quality tag: matched < 80% or
# panel-assigned < 50% -> WARN, panel-assigned < 25% -> ALERT. These are LIBRARY defects — a bad prep,
# a panel that does not match the reads — and they are a different axis from the per-cell reading tiers,
# which are about a cell's binding signal in a library that read out fine.
LIBRARY_TIERS = {
    "clean": (1.00, 0.98, "OK"),
    "good": (0.96, 0.94, "OK"),
    "fair": (0.88, 0.78, "OK"),
    "poor": (0.70, 0.45, "WARN"),
    "bad": (0.55, 0.20, "ALERT"),
}
# How the tiers are dealt out across samples, cycling when there are more samples than entries.
QUALITY_PROFILES = {
    "uniform": ["clean"],
    "mixed": ["clean", "good", "fair", "poor"],
    "spread": ["clean", "good", "poor", "bad"],
}

# What each reading tier should come back as, given the block's defaults. A statement of intent for the
# reader of the truth table, not an assertion the generator can make on its own.
EXPECTED_STATE_SHALLOW = {
    "strong": "bound where the comparator is thick enough to compare against — this is the only tier "
    "that reaches the line at this depth",
    "good": "not bound: a real reading, but the line sits above it once the comparator is this sparse",
    "medium": "not bound, or unreliable where the comparator reads under the thin line",
    "weak": "not bound — clear of the count floor and nothing more",
    "noise": "floored, or unreliable for want of a comparator; nothing to answer with",
    "crossreactive": "two co-dominant readings, neither of which reaches bound at this depth",
    "offtarget": "not bound; the comparator IS the dominant reading",
    "gated": "unreliable (gate on) / not bound with a high comparator (gate off)",
}

EXPECTED_STATE = {
    "strong": "bound — hundreds of UMIs against a comparator of a few",
    "good": "bound — clear of the line, without much margin",
    "medium": "on the line: bound or not bound, and the tier straddles it deliberately",
    "weak": "not bound — a real reading, clear of the count floor, nowhere near the line",
    "noise": "not bound (every reading floored), or unreliable where the comparator reads below the thin line",
    "crossreactive": "bound on TWO identities at once — neither is uniquely dominant",
    "offtarget": "nothing bound and the comparator reads high (it IS the dominant one); "
    "reads bound on the off-target instead when the comparator is the panel or none",
    "gated": "unreliable (gate on) / not bound with a high comparator (gate off)",
}


def load_whitelist_cells(rng, count, assets_dir):
    """Draw `count` real 737K-august-2016 cell barcodes, WITH replacement across samples (see build()).
    Prefers the full 10x inclusion list; falls back to the small harvested pool."""
    big = os.path.join(assets_dir, "737K-august-2016.txt")
    small = os.path.join(assets_dir, "whitelist_cells.txt")
    path = big if os.path.exists(big) else small
    if not os.path.exists(path):
        raise SystemExit(
            f"no cell whitelist in {assets_dir}. Fetch the full 10x inclusion list:\n"
            "  curl -sSL -o 737K-august-2016.txt https://raw.githubusercontent.com/10XGenomics/"
            "supernova/master/tenkit/lib/python/tenkit/barcodes/737K-august-2016.txt"
        )
    with open(path) as fh:
        pool = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if len(pool) < count:
        raise SystemExit(
            f"{os.path.basename(path)} holds {len(pool)} barcodes; this run needs {count} distinct ones "
            "per sample. Fetch the full 737K-august-2016 list, or lower --cells-per-sample."
        )
    return rng.sample(pool, count)


def build(
    run_dir,
    panel_csv,
    cells_per_sample=6000,
    barcode_source="whitelist737k",
    assets_dir=None,
    columns=None,
    target_roles=DEFAULT_TARGET_ROLES,
    offtarget_roles=DEFAULT_OFFTARGET_ROLES,
    offset=10,
    quality_profile="mixed",
    regime="deep",
    ambient_frac=None,
    seq_error_frac=None,
    primary_bias=0.35,
    cell_jitter=0.25,
    arm="all",
    clonal_profile=None,
    clonal_mean_size=None,
    clonal_singleton_cell_frac=None,
    ambient_barcode_ratio=None,
    aggregates=None,
    aggregate_umi_share=None,
    unpaired_frac=None,
    dup_mean=None,
    panel_shape="auto",
    control_feature=None,
    seed=REALPANEL_SEED,
):
    """Generate a cohort-scale run against the panel at `panel_csv`, into `run_dir`.

    Cell barcodes are drawn per sample INDEPENDENTLY from the whitelist, so samples share some barcodes
    — which is what real GEM wells do, and what makes (sampleId, cellId) the load-bearing key rather
    than cellId alone. `cell_jitter` varies each sample's cell count so no two libraries are the same
    size.

    The panel file is copied into the run directory verbatim: that copy is the one the block uploads, so
    what the block reads is the panel as it actually arrived, not a re-serialisation of it.

    `arm="vdj"` rebuilds ONLY the V(D)J arm, from the antigen arm's existing ground truth. The antigen
    arm is the expensive half — hundreds of megabytes of FASTQ against a few of TSV — and the repertoire
    shape is the half worth iterating on, so reshaping it should not cost a regeneration of the reads.

    `regime` selects a measured calibration (see REGIMES). Every regime-owned argument defaults to
    None, meaning "take the regime's value"; passing one explicitly overrides it. `regime="deep"` with
    nothing overridden reproduces every run made before regimes existed, byte for byte."""
    if regime not in REGIMES:
        raise SystemExit(f"unknown regime {regime!r}; expected one of {', '.join(REGIMES)}")
    R = REGIMES[regime]
    tiers = R["tiers"]
    tier_names = [t[0] for t in tiers]
    mag, bgp = R["magnitudes"], R["background"]
    expected_state = EXPECTED_STATE if regime == "deep" else EXPECTED_STATE_SHALLOW
    pick = lambda given, key: R[key] if given is None else given  # noqa: E731
    dup_mean = pick(dup_mean, "dup_mean")
    ambient_frac = pick(ambient_frac, "ambient_frac")
    seq_error_frac = pick(seq_error_frac, "seq_error_frac")
    ambient_barcode_ratio = pick(ambient_barcode_ratio, "ambient_barcode_ratio")
    aggregates = pick(aggregates, "aggregates")
    aggregate_umi_share = pick(aggregate_umi_share, "aggregate_umi_share")
    unpaired_frac = pick(unpaired_frac, "unpaired_frac")
    clonal_profile = pick(clonal_profile, "clonal_profile")
    clonal_mean_size = pick(clonal_mean_size, "clonal_mean_size")
    clonal_singleton_cell_frac = pick(clonal_singleton_cell_frac, "clonal_singleton_cell_frac")
    clonal_tail_cycle = R["clonal_tail_cycle"]
    rng = new_rng(seed)
    panels = load_panel(panel_csv, columns, target_roles, offtarget_roles, shape=panel_shape,
                        control_feature=control_feature)
    samples = list(panels)

    if arm == "vdj":
        return _rebuild_vdj_only(run_dir, panels, samples, clonal_profile, clonal_mean_size,
                                 clonal_singleton_cell_frac, offset, columns,
                                 unpaired_frac=unpaired_frac, tail_cycle=clonal_tail_cycle)

    antigen_dir = os.path.join(run_dir, "antigen")
    truth_dir = os.path.join(run_dir, "truth")
    for d in (antigen_dir, truth_dir):
        os.makedirs(d, exist_ok=True)

    # Off-panel barcodes: far from EVERY sample's panel, so a read on one is dropped rather than
    # corrected onto a real member. Independent RNG so the per-sample streams are unperturbed.
    all_panel_bcs = sorted({bc for p in panels.values() for bc in p.barcodes})
    off_bcs = gen_distinct(new_rng(seed + 99), 4, FEAT_LEN, min_dist=5, avoid=all_panel_bcs)

    profile = QUALITY_PROFILES[quality_profile]
    lib_tier_of = {s: profile[i % len(profile)] for i, s in enumerate(samples)}

    ab_rows, con_rows, read_rows, lib_rows = [], [], [], []
    tier_counts = {t: 0 for t in tier_names}
    total_reads = 0
    agg_rows = []
    print(f"[real-panel] {len(samples)} samples from {os.path.basename(panel_csv)}, offset-{offset} R2, "
          f"regime {regime}")
    for sample in samples:
        panel = panels[sample]
        n_cells = max(1, int(cells_per_sample * (1 + rng.uniform(-cell_jitter, cell_jitter))))
        if barcode_source == "whitelist737k":
            cells = load_whitelist_cells(rng, n_cells, assets_dir)
        else:
            cells = gen_cells(rng, n_cells)

        reads = []
        read_no = 0
        signal_umis = 0
        for cell in cells:
            tier = pick_tier(rng, tiers)
            per_member, consensus, tier = plant_cell(rng, panel, tier, primary_bias, mag, bgp)
            tier_counts[tier] += 1
            con_rows.append((sample, cell, consensus))
            dominant = max(per_member, key=lambda m: (per_member[m], m))
            read_rows.append((
                sample, cell, tier, dominant, per_member[dominant],
                panel.role.get(dominant, ""), sum(per_member.values()),
                len(per_member), expected_state[tier],
            ))
            for member, k in per_member.items():
                ab_rows.append((sample, cell, member, k))
            signal_umis += sum(per_member.values())
            read_no = emit_cell_reads(
                rng, reads, sample, panel, cell, per_member, offset, seq_error_frac, read_no,
                dup_mean=dup_mean,
            )

        signal_reads = len(reads)
        matched, panel_assigned, tag = LIBRARY_TIERS[lib_tier_of[sample]]
        # Ambient first, then aggregates sized against everything else already in the library. The
        # aggregate share is a share of the FINISHED library, and once the barcode universe is large it
        # holds most of the non-aggregate UMIs — sizing against signal alone under-plants by an order of
        # magnitude.
        ambient_umis = add_ambient(rng, panel, reads, offset, ambient_frac, n_cells=len(cells),
                                   barcode_ratio=ambient_barcode_ratio, dup_mean=dup_mean) or 0
        planted_agg = add_aggregates(rng, panel, reads, offset, aggregates, aggregate_umi_share,
                                     signal_umis + ambient_umis, dup_mean=dup_mean)
        for rank, (agg_cell, agg_umis) in enumerate(planted_agg, 1):
            agg_rows.append((sample, rank, agg_cell, agg_umis))
        convert_offpanel(rng, reads, off_bcs, 1 - panel_assigned, offset)
        add_malformed(rng, reads, matched)
        rng.shuffle(reads)
        write_fastq_gz(os.path.join(antigen_dir, f"{sample}_R1.fastq.gz"), reads, 1)
        write_fastq_gz(os.path.join(antigen_dir, f"{sample}_R2.fastq.gz"), reads, 2)
        total_reads += len(reads)
        lib_rows.append((sample, len(cells), len(panel.names), len(panel.targets), len(panel.offtargets),
                         signal_reads, len(reads), lib_tier_of[sample], f"{matched:.2f}",
                         f"{panel_assigned:.2f}", tag))
        extra = ""
        if planted_agg:
            extra += f", {len(planted_agg)} aggregates ({sum(k for _, k in planted_agg)} UMIs)"
        if ambient_barcode_ratio > 0:
            extra += f", ~{int(len(cells) * ambient_barcode_ratio)} ambient barcodes"
        print(f"  {sample}: {len(cells)} cells, {len(panel.names)} members "
              f"({len(panel.targets)} target / {len(panel.offtargets)} off-target), "
              f"{len(reads)} reads, library {lib_tier_of[sample]} -> expect {tag}{extra}")

    _write_truth(truth_dir, ab_rows, con_rows, read_rows, lib_rows, panels)
    _write_sample_metadata(run_dir, samples, panels, lib_tier_of)
    uploaded_panel = os.path.join(run_dir, "panel.csv")
    shutil.copyfile(panel_csv, uploaded_panel)

    if arm in ("all", "vdj"):
        vdj.build(
            os.path.join(truth_dir, "panel-canonical.csv"),
            os.path.join(truth_dir, "expected-consensus.tsv"),
            out_dir=os.path.join(run_dir, "vdj"),
            truth_dir=truth_dir,
            clonal_profile=clonal_profile,
            mean_size=clonal_mean_size,
            singleton_cell_frac=clonal_singleton_cell_frac,
            unpaired_frac=unpaired_frac,
            tail_cycle=clonal_tail_cycle,
        )

    n_cells_total = len(con_rows)
    print(f"\n[real-panel] {len(samples)} samples, {n_cells_total} cells, {total_reads} reads -> {run_dir}")
    print("  reading tiers: " + ", ".join(
        f"{t}={tier_counts[t]} ({tier_counts[t] / max(1, n_cells_total):.0%})" for t in tier_names
    ))
    print(f"  panel to upload: {uploaded_panel}")
    if agg_rows:
        _write_tsv(os.path.join(truth_dir, "aggregates.tsv"),
                   ("sample", "rank", "cellId", "umis"), agg_rows)
    # The regime decides what the tier table should COME BACK as, so a later --validate-only has to be
    # able to recover it. Without this, revalidating a shallow run applies the deep expectations and
    # reports five failures on a run that is behaving exactly as intended.
    with open(os.path.join(truth_dir, "regime.txt"), "w") as fh:
        fh.write(regime + "\n")
    return {"samples": samples, "cells": n_cells_total, "reads": total_reads, "tiers": tier_counts,
            "panels": panels, "offset": offset, "columns": dict(DEFAULT_COLUMNS, **(columns or {})),
            "regime": regime, "tier_table": tiers, "aggregates": agg_rows,
            "ambient_barcode_ratio": ambient_barcode_ratio, "dup_mean": dup_mean}


def _rebuild_vdj_only(run_dir, panels, samples, clonal_profile, mean_size, singleton_cell_frac,
                      offset, columns, unpaired_frac=0.0, tail_cycle=None):
    """Rebuild the V(D)J arm alone, over the antigen arm already on disk. Returns the same info dict
    `build` does, read back from the truth tables rather than recomputed, so the run report stays
    accurate without the reads being touched."""
    truth_dir = os.path.join(run_dir, "truth")
    consensus = os.path.join(truth_dir, "expected-consensus.tsv")
    if not os.path.exists(consensus):
        raise SystemExit(
            f"no antigen arm under {run_dir} — --arm vdj rebuilds the repertoire over an EXISTING "
            "antigen arm. Generate the full run first (drop --arm)."
        )
    vdj.build(
        os.path.join(truth_dir, "panel-canonical.csv"),
        consensus,
        out_dir=os.path.join(run_dir, "vdj"),
        truth_dir=truth_dir,
        clonal_profile=clonal_profile,
        mean_size=mean_size,
        singleton_cell_frac=singleton_cell_frac,
        unpaired_frac=unpaired_frac,
        tail_cycle=tail_cycle,
    )
    # Keyed off whatever tiers the truth table actually holds, not a fixed list: an --arm vdj rebuild
    # runs over an antigen arm that may have been generated under a different regime's tier table.
    tiers, cells = {}, 0
    with open(os.path.join(truth_dir, "expected-readings.tsv"), newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
            cells += 1
    print(f"\n[real-panel] V(D)J arm only, over the existing antigen arm ({cells} cells) -> {run_dir}")
    return {"samples": samples, "cells": cells, "reads": 0, "tiers": tiers, "panels": panels,
            "offset": offset, "columns": dict(DEFAULT_COLUMNS, **(columns or {}))}


def _write_tsv(path, header, rows):
    """One tab-separated truth table. Small enough not to warrant a dependency."""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(list(header))
        w.writerows(rows)


def _write_sample_metadata(run_dir, samples, panels, lib_tier_of):
    """samples-metadata.tsv — the per-sample table Samples & Data imports as metadata, keyed by the
    sample name so it joins to the same sampleId the three arms share.

    The panel names its samples and says nothing else about them, so Donor and Condition are invented
    here: they exist to give downstream grouping something to split on, and a two-arm condition is the
    smallest thing that does. The other two columns are NOT invented and are the reason this file is
    worth having in this bed:

      LibraryQuality  the tier this sample's library was degraded to. Group the QC report on it and the
                      Quality tag should track it, which is the one claim the library axis makes.
      PanelTargets    how many on-target members this sample's panel declares. It varies per sample in a
                      real per-sample panel, and it is what makes *never asked* reachable — a sample that
                      never offered an identity cannot have answered about it.
    """
    path = os.path.join(run_dir, "samples-metadata.tsv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["Sample", "Donor", "Condition", "LibraryQuality", "PanelMembers", "PanelTargets"])
        for i, s in enumerate(samples):
            panel = panels[s]
            w.writerow([
                s,
                f"Donor {i + 1}",
                "baseline" if i % 2 == 0 else "stimulated",
                lib_tier_of[s],
                len(panel.names),
                len(panel.targets),
            ])
    return path


def _write_truth(truth_dir, ab_rows, con_rows, read_rows, lib_rows, panels):
    ab_rows.sort()
    with open(os.path.join(truth_dir, "expected-abundance.tsv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample", "cellId", "feature", "planted_distinct_umis"])
        w.writerows(ab_rows)
    con_rows.sort()
    with open(os.path.join(truth_dir, "expected-consensus.tsv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample", "cellId", "planted_consensus"])
        w.writerows(con_rows)
    read_rows.sort()
    with open(os.path.join(truth_dir, "expected-readings.tsv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample", "cellId", "tier", "dominantFeature", "dominantUmis", "dominantRole",
                    "totalUmis", "nFeatures", "expectedState"])
        w.writerows(read_rows)
    with open(os.path.join(truth_dir, "library-quality.tsv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample", "cells", "panelMembers", "targets", "offTargets", "signalReads",
                    "totalReads", "libraryTier", "matchedFrac", "panelAssignedFrac", "expectedQualityTag"])
        w.writerows(lib_rows)
    # The flat (tag, feature) view the VDJ arm's clear-antigen lookup reads. Not a block upload — the
    # block gets the panel file verbatim — so it lives under truth/ with the rest of the derived state.
    with open(os.path.join(truth_dir, "panel-canonical.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Sample", "tag", "feature", "role"])
        for sample, panel in panels.items():
            for name in panel.names:
                w.writerow([sample, panel.barcode[name], name, panel.role.get(name, "")])


def write_run_report(run_dir, info, panel_csv, quality_profile, gate_hint=True):
    """Write RUN.md inside the run — the settings this run expects, worked out from the panel that
    actually drove it. It lives in the run directory (gitignored) and not in the tracked README because
    it names the panel's own samples and columns, and the panel is the user's, not this repository's."""
    cols = info["columns"]
    panels = info["panels"]
    offset = info["offset"]
    # Exactly what model/src/pattern.ts assembles from the builder fields below, so the report can be
    # compared against the pattern the block shows rather than paraphrasing it.
    skip = "N{%d}" % offset if offset else ""
    pattern = "^(CELL:N{16})(UMI:N{10})*\\^" + skip + "(FEATURE:N{15})(R2:*)"
    role_values = sorted({p.role[n] for p in panels.values() for n in p.names})
    offtarget_values = sorted({p.role[n] for p in panels.values() for n in p.offtargets})
    lines = [
        "# Run settings",
        "",
        f"Generated from `{os.path.basename(panel_csv)}` — {len(info['samples'])} samples, "
        f"{info['cells']} cells, {info['reads']} reads, regime "
        f"**{info.get('regime', 'deep')}**.",
        "",
        "## Upload",
        "",
        "| what | where |",
        "| --- | --- |",
        "| feature-barcode FASTQs | `antigen/*_R{1,2}.fastq.gz` (Fastq dataset) |",
        "| panel | `panel.csv` (Xsv-csv), the source panel verbatim |",
        "| single-cell V(D)J | `vdj/*.tsv` (Xsv-tsv, import-vdj-data format **AIRR single cell**) |",
        "| sample metadata | `samples-metadata.tsv` (Samples & Data → Metadata), keyed by `Sample` |",
        "",
        "All arms carry the same bare-16nt cell barcode and the same sample names, so one "
        "Samples & Data block mints one sampleId per sample across all of them.",
        "",
        "## Feature Barcode Profiling",
        "",
        "| setting | value | why |",
        "| --- | --- | --- |",
        f"| barcode column | `{cols['sequence']}` | holds the 15 bp feature barcode |",
        f"| feature-name column | `{cols['name']}` | holds the antigen name |",
        f"| sample column | `{cols['sample']}` | **required** — the panel is per-sample and reuses "
        "sequences across samples; without it the duplicate-barcode guard fires |",
        f"| role column | `{cols['role']}` | values present: {', '.join(role_values)} |",
        "| negative control | *(none)* | this panel declares no comparator — see below |",
        "| tag preset | **Custom feature-barcode kit** (`generic-fb-umi`) | these are TotalSeq-C "
        "antigen-capture barcodes; the BEAM-Core preset assumes offset 0 |",
        "| cell / UMI / feature length | 16 / 10 / 15 | |",
        f"| Read 2 offset | **{offset}** | R2 = {offset} bp lead-in + the 15 bp feature |",
        f"| assembled pattern | `{pattern}` | what the builder produces from the row above |",
        # Under the shallow regime the barcode universe is the point, and a whitelist collapses it. The
        # table and the shallow section below would otherwise contradict each other in the same file.
        ("| cell whitelist | *(leave empty)* | the raw barcode universe is what this run is for — see "
         "below |" if info.get("regime") == "shallow" else
         "| cell whitelist | `737K-august-2016` | cell barcodes are real 737K members |"),
        "",
        "### The comparator",
        "",
        "The role column names what a member is TO THE QUESTION and carries no value meaning "
        "*negative control*, so there is no declared comparator to point at. Two readings are "
        "available and they answer differently:",
        "",
        "- **reference source = panel** — each cell is read against its own sample's panel readings.",
        "- **reference source = declared**, reference values "
        + (", ".join(f"`{v}`" for v in offtarget_values) if offtarget_values else "*(none available)*")
        + " — the off-target members serve as the comparator. Closest to how the panel was "
        "designed to be read.",
        "- **reference source = none** — no comparator; readings stand on the count floor alone.",
        "",
        "### Reading tiers planted",
        "",
        "| tier | cells | expected state |",
        "| --- | --- | --- |",
    ]
    for tier, _w, _doc in TIERS:
        n = info["tiers"].get(tier, 0)
        table = EXPECTED_STATE if info.get("regime", "deep") == "deep" else EXPECTED_STATE_SHALLOW
        lines.append(f"| {tier} | {n} ({n / max(1, info['cells']):.0%}) | {table[tier]} |")
    lines += [
        "",
        "Per-cell ground truth is `truth/expected-readings.tsv` (one row per cell: tier, dominant "
        "member, its UMIs, its role, expected state).",
        "",
        f"Library quality is the other axis, profile `{quality_profile}` — see "
        "`truth/library-quality.tsv` for each sample's matched / panel-assigned fractions and the "
        "Quality tag it should show.",
    ]
    if gate_hint:
        lines += [
            "",
            "The `gated` tier only reads as *unreliable* with the admissibility gate ON. Set the gate "
            "threshold near 300 comparator UMIs to catch it; leave it off and those cells read *bound*.",
        ]
    if info.get("regime") == "shallow":
        # aggregates holds one row PER SAMPLE per aggregate, so the per-library count is the quotient.
        # Reporting the row count would say "20 barcodes" of a four-sample run that planted five each.
        aggs = info.get("aggregates") or []
        per_lib = len(aggs) // max(1, len(info["samples"]))
        ratio = info.get("ambient_barcode_ratio") or 0
        lines += [
            "",
            "## What the shallow regime changes",
            "",
            "This run stands in for real in-vivo BEAM libraries rather than for a public 10x "
            "BEAM run. Three things follow, and the first is the one that surprises people.",
            "",
            "**Leave the cell whitelist EMPTY.** The observed live configuration sets none, so the block "
            "consumes the raw barcode universe and reports it as *cells detected*. This run plants "
            f"about {ratio:.0f}x as many ambient barcodes as real cells, with a median of one UMI each, "
            "so `cells detected` will read in the tens or hundreds of thousands and `median UMIs / "
            "cell` will read **1**. That is not the bed misbehaving — it is the number their "
            "scientists see. Setting a whitelist collapses it and hides the effect.",
            "",
            f"**Antigen aggregates are present and unfiltered.** {per_lib} barcodes per library hold "
            "roughly 59% of its UMIs, the largest about 18% on its own. Cell Ranger removes this "
            "population before cell calling; this block does not. They are recorded in "
            "`truth/aggregates.tsv`, so anything they distort — a panel median, a comparator, a "
            "*cells detected* count — can be traced back to them.",
            "",
            "**Expect roughly 1-3% of readings to come back bound.** A real in-vivo pipeline produces "
            "1.4% and "
            "2.9% across the two measured libraries, and this run is calibrated to land in that band. A "
            "shallow run showing a clean majority of confident binders has lost the regime.",
        ]
    with open(os.path.join(run_dir, "RUN.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


# --- offline validation --------------------------------------------------------------------------

def validate(run_dir, panel_csv=None, columns=None, sample_check=None, regime=None, baseline_tag=None,
             target_roles=DEFAULT_TARGET_ROLES, offtarget_roles=DEFAULT_OFFTARGET_ROLES):
    """Check a generated run without a backend. Re-derives each checked sample's per-(cell, member)
    distinct-UMI counts straight from the FASTQ pair and compares them to the planted truth — the one
    test that proves the reads say what the truth table claims. Also checks read geometry, per-sample
    barcode uniqueness, the tier mix, and that the VDJ arm's cell ids are the antigen arm's.

    `sample_check` limits the FASTQ re-derivation to one sample (the pass is linear in reads, so on a
    cohort-scale run checking every sample is slow for no extra coverage). Returns True on a clean pass."""
    import gzip

    # Prefer what the run RECORDS over what the caller guessed: a --validate-only invocation carries
    # whatever --regime happened to be on the command line, which need not be the one that built it.
    recorded = os.path.join(run_dir, "truth", "regime.txt")
    if os.path.exists(recorded):
        with open(recorded) as fh:
            regime = (fh.read().strip() or None) or regime
    regime = regime or "deep"
    failures, checks = [], 0

    def ok(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(label)

    panel_path = panel_csv or os.path.join(run_dir, "panel.csv")
    # Shape-aware AND role-aware: a run generated from a narrow panel must validate against a narrow
    # read of it, and one generated with custom role words must use the SAME words here. Re-reading with
    # the defaults silently reclassifies every member — a panel whose comparator is its `Decoy` row comes
    # back with five off-target baselines instead of one, and the declared rung then reports itself
    # refused on a run where it serves.
    panels = load_panel(panel_path, columns, target_roles, offtarget_roles, shape="auto")
    truth = os.path.join(run_dir, "truth")

    for sample, panel in panels.items():
        ok(len(set(panel.barcodes)) == len(panel.barcodes), f"{sample}: barcodes unique within the sample")
        ok(all(len(b) == FEAT_LEN for b in panel.barcodes), f"{sample}: every barcode is {FEAT_LEN} bp")
        # A 1 bp read error must not turn one member into another, or the planted counts and the
        # recoverable counts are different numbers and no per-cell reading can be checked at all.
        bcs = panel.barcodes
        closest = min(
            (sum(1 for x, y in zip(a, b) if x != y) for i, a in enumerate(bcs) for b in bcs[i + 1:]),
            default=FEAT_LEN,
        )
        ok(closest >= 3, f"{sample}: panel members are >= 3 bp apart (closest pair {closest} bp)")

    # tier mix present and complete
    tiers = {}
    with open(os.path.join(truth, "expected-readings.tsv"), newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    for r in rows:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
    for tier in TIER_NAMES:
        ok(tiers.get(tier, 0) > 0, f"tier {tier} is present ({tiers.get(tier, 0)} cells)")
    ok(len(rows) > 0, "readings truth is non-empty")

    # Each sample's panel-assigned fraction. It is the recovery bound: a degraded library has a share of
    # its reads deliberately rewritten onto off-panel barcodes, so its planted UMIs are NOT all
    # recoverable and a fixed bar would fail every sample the profile degrades on purpose.
    panel_assigned = {}
    lq = os.path.join(truth, "library-quality.tsv")
    if os.path.exists(lq):
        with open(lq, newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                panel_assigned[r["sample"]] = float(r["panelAssignedFrac"])

    # planted abundance, indexed by (sample, cell, member)
    planted = {}
    with open(os.path.join(truth, "expected-abundance.tsv"), newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            planted[(r["sample"], r["cellId"], r["feature"])] = int(r["planted_distinct_umis"])

    check_samples = [sample_check] if sample_check else list(panels)
    for sample in check_samples:
        panel = panels[sample]
        by_seq = {}
        for name in panel.names:
            by_seq.setdefault(panel.barcode[name], name)
        r1p = os.path.join(run_dir, "antigen", f"{sample}_R1.fastq.gz")
        r2p = os.path.join(run_dir, "antigen", f"{sample}_R2.fastq.gz")
        ok(os.path.exists(r1p) and os.path.exists(r2p), f"{sample}: FASTQ pair exists")
        if not (os.path.exists(r1p) and os.path.exists(r2p)):
            continue
        # offset is whatever position the panel barcodes actually sit at; read it off the first
        # structurally valid read rather than trusting an argument.
        seen = {}
        n_reads = 0
        offsets = set()
        with gzip.open(r1p, "rt") as f1, gzip.open(r2p, "rt") as f2:
            while True:
                h1, s1, _p1, _q1 = (f1.readline(), f1.readline(), f1.readline(), f1.readline())
                h2, s2, _p2, _q2 = (f2.readline(), f2.readline(), f2.readline(), f2.readline())
                if not h1 or not h2:
                    break
                n_reads += 1
                ok(h1.split()[0] == h2.split()[0], f"{sample}: R1/R2 read names line up") if n_reads == 1 else None
                s1, s2 = s1.strip(), s2.strip()
                if len(s1) < CELL_LEN + UMI_LEN:
                    continue
                cell, umi = s1[:CELL_LEN], s1[CELL_LEN:CELL_LEN + UMI_LEN]
                for off in (0, 10):
                    seq = s2[off:off + FEAT_LEN]
                    member = by_seq.get(seq)
                    if member:
                        offsets.add(off)
                        seen.setdefault((cell, member), set()).add(umi)
                        break
        ok(n_reads > 0, f"{sample}: FASTQ is non-empty ({n_reads} reads)")
        ok(len(offsets) == 1, f"{sample}: one feature offset in the file (found {sorted(offsets)})")
        # Every planted (cell, member) must be recoverable from the reads, and NOTHING must be
        # recoverable that was not planted. Two separate claims, because they fail for different
        # reasons and only one of them tolerates slack:
        #
        #   over-recovery is bounded near zero — a member reading MORE distinct UMIs than were planted
        #   is either a panel whose members are close enough that a 1 bp error turns one into another
        #   (which the >= 3 bp check above rules out), or an AMBIENT read whose random 16-mer cell
        #   barcode happened to equal a real one. The second is real and unavoidable: at ~1M ambient
        #   reads against ~7k cells it lands about twice per sample (n_ambient * n_cells / 4^16), and a
        #   real library does exactly this. So the bar is a rate, not zero, and a rate this small can
        #   only be met by ambient collision.
        #
        #   under-recovery is expected, and how much is expected depends on the sample's LIBRARY tier.
        #   A UMI is lost when every one of its reads is unrecoverable — either it took a feature-barcode
        #   error (1.5% of reads) or it was rewritten onto an off-panel barcode (whatever the tier's
        #   panel-assigned fraction leaves). Multi-read UMIs survive better than reads do, so recovery
        #   sits ABOVE the panel-assigned fraction, never below it, and never above 1. Those two are the
        #   bound. Checked in aggregate rather than per pair: per pair the loss is a coin toss, in
        #   aggregate it is the rate, and the rate is the thing worth asserting.
        n_pairs = sum(1 for key in planted if key[0] == sample)
        over = [key for key, k in planted.items()
                if key[0] == sample and len(seen.get((key[1], key[2]), ())) > k]
        over_rate = len(over) / max(1, n_pairs)
        ok(over_rate < 0.001, f"{sample}: over-recovery stays at the ambient-collision rate "
                              f"({len(over)}/{n_pairs} = {over_rate:.4%}, must be < 0.1%)")
        tot_planted = sum(k for key, k in planted.items() if key[0] == sample)
        tot_got = sum(len(seen.get((key[1], key[2]), ())) for key in planted if key[0] == sample)
        frac = tot_got / max(1, tot_planted)
        # The floor is the per-READ survival probability: a read survives when it was neither rewritten
        # off-panel (the library tier) nor hit by a feature-barcode error (SEQ_ERROR_FRAC). A UMI can
        # only do BETTER than one of its reads, because losing it needs every one of its reads to fail —
        # so per-read survival is a true lower bound on UMI recovery, and a tight one.
        floor_frac = panel_assigned.get(sample, 1.0) * (1 - SEQ_ERROR_FRAC)
        ok(floor_frac <= frac <= 1.0,
           f"{sample}: planted UMIs recovered from the FASTQs ({tot_got}/{tot_planted} = {frac:.2%}, "
           f"expected between this library's per-read survival {floor_frac:.2%} and 100%)")

    # metadata must name exactly the samples the arms carry, or a grouping column silently covers only
    # part of the run
    meta_path = os.path.join(run_dir, "samples-metadata.tsv")
    ok(os.path.exists(meta_path), "samples-metadata.tsv exists")
    if os.path.exists(meta_path):
        with open(meta_path, newline="") as fh:
            meta = list(csv.DictReader(fh, delimiter="\t"))
        ok({r["Sample"] for r in meta} == set(panels),
           "metadata names exactly the panel's samples")
        ok(all(int(r["PanelMembers"]) == len(panels[r["Sample"]].names) for r in meta),
           "metadata's panel sizes match the panel")

    # cross-arm barcode alignment: every VDJ cell must be an antigen cell of the same sample
    vdj_dir = os.path.join(run_dir, "vdj")
    if os.path.isdir(vdj_dir):
        antigen_cells = {}
        with open(os.path.join(truth, "expected-consensus.tsv"), newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                antigen_cells.setdefault(r["sample"], set()).add(r["cellId"])
        for fn in sorted(os.listdir(vdj_dir)):
            if not fn.endswith(".tsv"):
                continue
            sample = fn[:-4]
            with open(os.path.join(vdj_dir, fn), newline="") as fh:
                cells = {r["cell_id"] for r in csv.DictReader(fh, delimiter="\t")}
            ok(cells and cells <= antigen_cells.get(sample, set()),
               f"{sample}: every VDJ cell_id is an antigen cell of the same sample ({len(cells)} cells)")

    # --- the reading, simulated -------------------------------------------------------------------
    # The tiers are promises about verdicts. Predict every verdict from the truth with the block's own
    # rule and check the promises, so a magnitude that drifts out of its tier fails here rather than
    # surfacing as a puzzling run.
    # Which rungs this panel can even be read on, under the CURRENT rule. A bed whose panel cannot serve
    # a comparator is a fact about the panel, not a failure of the bed, and the report has to say which.
    rungs = {}
    for src in ("declared", "panel", "none"):
        try:
            rungs[src] = simulate_verdicts(run_dir, panels, source=src, baseline_tag=baseline_tag)
        except BaselineRefused as exc:
            rungs[src] = exc
    # Report on the best rung that actually served: a declared single baseline, else the panel, else none.
    chosen = None
    for src in ("declared", "panel", "none"):
        r = rungs[src]
        if not isinstance(r, BaselineRefused) and r[3] == src:
            chosen = src
            break
    if chosen is None:
        chosen = "none"
        per_tier, totals, multi, served = simulate_verdicts(run_dir, panels, source="none",
                                                    baseline_tag=baseline_tag)
    else:
        per_tier, totals, multi, served = rungs[chosen]
    try:
        gated_tier, _gt, _gm, _gs = simulate_verdicts(run_dir, panels, source=chosen, gate=300,
                                                      baseline_tag=baseline_tag)
    except BaselineRefused:
        gated_tier = {t: {} for t in TIER_NAMES}
    n_of = {t: sum(per_tier[t].values()) for t in TIER_NAMES}

    def share(table, tier, state):
        """`state` as a share of the tier's cells. For bound / not bound the denominator is the cells
        whose comparison could be MADE — a cell with no off-target reading has no comparator and reads
        unreliable, and folding those into the denominator makes a claim about binding depend on how
        many off-target members the sample happens to declare. `unreliable` keeps the full denominator,
        because that is the number being asked about."""
        full = sum(table[tier].values())
        if not full:
            return 0.0
        if state == "unreliable":
            return table[tier].get(state, 0) / full
        comparable = full - table[tier].get("unreliable", 0)
        return table[tier].get(state, 0) / comparable if comparable else 0.0

    print(f"\n[verdict simulation] regime {regime} — every cell's DOMINANT identity, floor {FLOOR} / "
          f"cutoff {CUTOFF}, no thin-reference line (the block removed it)")
    for src in ("declared", "panel", "none"):
        r = rungs[src]
        if isinstance(r, BaselineRefused):
            note = f"REFUSED — {r}"
        elif r[3] != src:
            note = f"cannot serve (panel holds < {PANEL_MIN_MEMBERS} tags) -> degrades to none"
        else:
            note = "serves" + ("  <- reported below" if src == chosen else "")
        print(f"  rung {src:<10} {note}")
    print("  bound / not bound are shares of the cells that HAD a comparator; unreliable is of all of them")
    print(f"  {'tier':<14}{'cells':>7}{'bound':>9}{'not bound':>11}{'unreliable':>12}   bound on 2+")
    for tier in TIER_NAMES:
        n = n_of[tier]
        print(f"  {tier:<14}{n:>7}"
              f"{share(per_tier, tier, 'bound'):>9.0%}"
              f"{share(per_tier, tier, 'not bound'):>11.0%}"
              f"{share(per_tier, tier, 'unreliable'):>12.0%}"
              f"{(multi.get(tier, 0) / n if n else 0):>14.0%}")
    grid = sum(totals.values())
    print("  whole grid (every cell x every identity its sample offered): "
          + ", ".join(f"{k} {v} ({v / max(1, grid):.0%})" for k, v in sorted(totals.items())))

    grid_bound = totals.get("bound", 0) / max(1, grid)
    if chosen == "none":
        # No rung could serve, so EVERY reading is unreliable for want of a comparator. That is the
        # correct answer for this panel under the current rule, not a defect in the bed, and asserting
        # the regime's bound share here would fail a run that is behaving exactly as the block would.
        #
        # It happens because the baseline is global BY TAG while a per-sample panel's comparators are
        # per sample: `declared` refuses several tags, `panel` needs PANEL_MIN_MEMBERS, and a small
        # per-sample panel satisfies neither. Name one tag with --baseline-tag to read on `declared`,
        # accepting that cells in samples not offering that tag still have no comparator.
        ok(totals.get("unreliable", 0) == grid,
           f"no rung serves this panel, so every reading is unreliable ({totals.get('unreliable', 0)}"
           f"/{grid})")
        ok(grid_bound == 0.0, f"nothing reads bound without a comparator ({grid_bound:.1%})")
    elif regime == "deep":
        ok(all(totals.get(k, 0) > 0 for k in ("bound", "not bound")),
           "both settled states occur in the run")
        ok(share(per_tier, "strong", "bound") >= 0.98,
           f"strong reads bound ({share(per_tier, 'strong', 'bound'):.0%})")
        ok(share(per_tier, "good", "bound") >= 0.85,
           f"good reads bound ({share(per_tier, 'good', 'bound'):.0%})")
        ok(share(per_tier, "medium", "bound") >= 0.10 and share(per_tier, "medium", "not bound") >= 0.10,
           f"medium straddles the line (bound {share(per_tier, 'medium', 'bound'):.0%} / "
           f"not bound {share(per_tier, 'medium', 'not bound'):.0%})")
        ok(share(per_tier, "weak", "not bound") >= 0.98,
           f"weak reads not bound ({share(per_tier, 'weak', 'not bound'):.0%})")
        ok(share(per_tier, "noise", "bound") == 0.0,
           f"noise never reads bound ({share(per_tier, 'noise', 'bound'):.0%})")
        ok(share(per_tier, "offtarget", "bound") <= 0.05,
           f"offtarget does not read bound against itself ({share(per_tier, 'offtarget', 'bound'):.0%})")
        cr_comparable = n_of["crossreactive"] - per_tier["crossreactive"].get("unreliable", 0)
        cr_multi = multi.get("crossreactive", 0) / max(1, cr_comparable)
        ok(cr_multi >= 0.85,
           f"crossreactive binds two identities at once ({cr_multi:.0%} of its comparable cells)")
        ok(share(gated_tier, "gated", "unreliable") >= 0.95,
           f"gated is set aside with the gate at 300 ({share(gated_tier, 'gated', 'unreliable'):.0%})")
    else:
        # SHALLOW asserts something different, because at this depth the block's line is not reachable
        # from most cells and asserting that it is would be asserting a fiction. Two things are checked
        # instead, and both are properties of real measured output rather than of the bed.
        #
        # 1. The bound share of the whole grid stays in the band a real pipeline actually produces:
        #    1.4% and 2.9% across the two measured libraries. A shallow run coming back with 20% bound has
        #    lost the regime, and one that comes back with 0% has nothing to test against.
        # 2. Signal ORDERING holds. Absolute rates are all low, so the invariant that carries meaning is
        #    monotonicity: a tier planted with more signal must never read bound LESS often than one
        #    planted with less. That catches a broken comparator or an inverted score without pretending
        #    to know where the line sits.
        ok(all(totals.get(k, 0) > 0 for k in ("bound", "not bound")),
           "both settled states occur in the run")
        ok(0.002 <= grid_bound <= 0.30,
           f"the bound share is in the range the current rule produces on shallow data ({grid_bound:.1%})")
        # A sparse comparator no longer produces *unreliable* — that was the thin-reference line, and the
        # block removed it. With a comparator serving and the gate off, nothing is unreliable, and a run
        # that still shows some has either lost its comparator for part of the panel or been gated.
        ok(totals.get("unreliable", 0) == 0,
           f"a served comparator leaves nothing unreliable ({totals.get('unreliable', 0)} of {grid})")
        ladder = [(t, share(per_tier, t, "bound")) for t in ("strong", "good", "medium", "weak", "noise")]
        monotone = all(ladder[i][1] >= ladder[i + 1][1] - 1e-9 for i in range(len(ladder) - 1))
        ok(monotone, "bound rate falls monotonically from strong to noise ("
                     + " >= ".join(f"{t} {v:.0%}" for t, v in ladder) + ")")
        ok(share(per_tier, "noise", "bound") == 0.0,
           f"noise never reads bound ({share(per_tier, 'noise', 'bound'):.0%})")
        ok(share(per_tier, "offtarget", "bound") <= 0.05,
           f"offtarget does not read bound against itself ({share(per_tier, 'offtarget', 'bound'):.0%})")

    print(f"\n[validate] {checks - len(failures)}/{checks} PASS")
    for f in failures:
        print(f"  FAIL: {f}")
    return not failures


# --- verdict simulation --------------------------------------------------------------------------
#
# The block's own reading rule, re-implemented over the truth tables so a run can be checked BEFORE it
# reaches a backend. Its whole value is that it fails when the planted magnitudes do not land where the
# tier names claim: a tier is a promise about a verdict, and a promise nothing checks drifts.
#
# The rule, from software/per-cell-metrics/src/verdict.py:
#   1. counts below the floor read as zero, except a comparator's;
#   2. the cell's comparator reading is the MAX over its reference tags;
#   3. score = (1 - I_0.925(count + 1, reference + 3)) * 100;
#   4. no comparator -> unreliable; comparator below the thin line -> unreliable; comparator at or above
#      the gate -> unreliable; else score >= cutoff -> bound, otherwise not bound.

FLOOR = 4
CUTOFF = 75
# The thin-reference line is GONE from the block (`count-becomes-a-state` removed the branch rather than
# filling it in). A low comparator is no longer a reason to call a reading unreliable: the comparison
# runs, and a comparator of 0 is a real comparison that any count clearing the floor beats. Simulating
# the old line here would model a rule the block no longer has — and would hide the new failure, which is
# the opposite one: a run that used to look broken now looks spectacularly successful.
#
# The panel rung instead GATES on how many members the panel holds. Below the minimum, comparing a count
# against a handful of other antigens is not a background estimate, so the rung refuses to serve at all.
PANEL_MIN_MEMBERS = 25


class BaselineRefused(Exception):
    """The panel declares more than one baseline tag, which the block refuses rather than combines.

    Not a simulation limitation — it is a hard exit in `verdict.py`, because reading against several
    baselines needs a panel column saying which antigens each one belongs to, and the panel format has
    no such column. Raised here so a bed whose panel cannot be read that way says so, rather than
    quietly reporting a grid the block would never produce."""
BETA_X, BETA_A_OFFSET, BETA_B_OFFSET = 0.925, 1, 3


def _betacf(a, b, x, maxit=300, eps=3e-16, fpmin=1e-300):
    """Continued fraction for the incomplete beta function (modified Lentz). Standard formulation; the
    block itself calls scipy, which this bed does not depend on."""
    import math

    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    _ = math
    return h


def specificity_score(count, reference):
    """The block's specificity score, 0-100, in the standard library."""
    import math

    a = count + BETA_A_OFFSET
    b = reference + BETA_B_OFFSET
    x = BETA_X
    if x <= 0:
        cdf = 0.0
    elif x >= 1:
        cdf = 1.0
    else:
        lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        bt = math.exp(lb + a * math.log(x) + b * math.log1p(-x))
        cdf = bt * _betacf(a, b, x) / a if x < (a + 1.0) / (a + b + 2.0) else 1.0 - bt * _betacf(b, a, 1.0 - x) / b
    return (1.0 - cdf) * 100.0


def simulate_verdicts(run_dir, panels, floor=FLOOR, cutoff=CUTOFF, gate=None,
                      source="declared", baseline_tag=None, min_members=PANEL_MIN_MEMBERS):
    """Predict every (cell, identity) state from the truth tables under the block's CURRENT rule.

    Returns (per_tier, totals, per_tier_multi, served). `per_tier` maps tier -> {state: n} counted over
    each cell's DOMINANT identity; `totals` counts every position in the grid, including the silent ones,
    since an identity a cell was offered and did not read answers *not bound* rather than nothing.
    `served` is the rung that actually served — never the one asked for unless it could serve.

    The rule, as `verdict.py` now has it:

    * The floor zeroes a non-baseline reading below it. The baseline is EXEMPT, because the floor removes
      what is not evidence of binding and the baseline is not evidence of binding.
    * `declared` reads against exactly ONE baseline tag. A panel declaring several is REFUSED, not
      combined — the block used to take the highest across them and no longer does.
    * `panel` reads against the median of the cell's own readings, and only serves at all when the panel
      holds `min_members` tags. Below that it degrades to no comparator.
    * There is no thin-reference line. A comparator of 0 is a real comparison.
    * A reading is unreliable only where there is NO comparator, or where the gate set the cell aside.
    """
    truth = os.path.join(run_dir, "truth")
    counts = {}
    with open(os.path.join(truth, "expected-abundance.tsv"), newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            counts.setdefault((r["sample"], r["cellId"]), {})[r["feature"]] = int(r["planted_distinct_umis"])
    tier_of, dominant_of = {}, {}
    with open(os.path.join(truth, "expected-readings.tsv"), newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            tier_of[(r["sample"], r["cellId"])] = r["tier"]
            dominant_of[(r["sample"], r["cellId"])] = r["dominantFeature"]

    # panel_size the way the block computes it: distinct TAGS across the whole panel, not per sample
    # (`emit_verdicts.py` reads `panel["tag"].n_unique()`), so a per-sample panel does not clear the gate
    # by being counted several times.
    panel_size = len({p.barcode[n] for p in panels.values() for n in p.names})

    # Baseline tags are global: a tag is the comparator in every sample or in none. Named by SEQUENCE for
    # the same reason the block keys on the tag rather than the antigen, since one sequence can carry
    # different antigen names in different samples.
    if baseline_tag:
        baseline_seqs = {p.barcode[n] for p in panels.values() for n in p.names
                         if n == baseline_tag or p.barcode[n] == baseline_tag}
    else:
        baseline_seqs = {p.barcode[n] for p in panels.values() for n in p.offtargets}

    served = source
    if source == "declared":
        if not baseline_seqs:
            served = "none"
        elif len(baseline_seqs) > 1:
            raise BaselineRefused(
                f"the panel declares {len(baseline_seqs)} baseline tags and the block reads against one "
                "or none. Name a single tag with --baseline-tag, or read with source 'panel' or 'none'."
            )
    elif source == "panel" and panel_size < min_members:
        served = "none"

    per_tier = {t: {} for t in TIER_NAMES}
    per_tier_multi = {t: 0 for t in TIER_NAMES}
    totals = {}
    for key, per in counts.items():
        sample, _cell = key
        panel = panels[sample]
        seq_of = {n: panel.barcode[n] for n in panel.names}
        local_baseline = [n for n in panel.names if seq_of[n] in baseline_seqs]

        if served == "none":
            reference = None
        elif served == "declared":
            # Offered but unread is a reading of zero; not offered at all is no comparator. The block
            # tests membership rather than defaulting to 0 for exactly this reason.
            reference = max((per.get(n, 0) for n in local_baseline), default=None) if local_baseline else None
        else:  # panel
            observed = [v for v in per.values()]
            reference = int(st.median(observed)) if observed else None

        tier = tier_of.get(key, "?")
        n_bound = 0
        for name in panel.names:
            raw = per.get(name, 0)
            count = raw if (name in local_baseline or raw >= floor) else 0
            if reference is None:
                state = "unreliable"
            elif gate is not None and reference >= gate:
                state = "unreliable"
            else:
                state = "bound" if specificity_score(count, reference) >= cutoff else "not bound"
            totals[state] = totals.get(state, 0) + 1
            if state == "bound":
                n_bound += 1
            if name == dominant_of.get(key):
                per_tier[tier][state] = per_tier[tier].get(state, 0) + 1
        if n_bound >= 2:
            per_tier_multi[tier] = per_tier_multi.get(tier, 0) + 1
    return per_tier, totals, per_tier_multi, served
