"""Real-panel path tests.

The point of `--real-panel` is that a panel supplied from outside the repository drives the run, so
these tests write their OWN synthetic wide panel and drive the generator with that. Nothing here may
carry a real panel's vocabulary -- its sample names, antigen names, catalogue ids, sequences or channel
values -- because this file is committed and a real panel is not ours to commit.

The synthetic panel reproduces the three shapes that matter: per-sample panels of unequal size, a
sequence reused across two samples under different antigen names, and a role column that declares target
and off-target and nothing meaning *negative control*.
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent  # software/test-data/manual
sys.path.insert(0, str(HERE))

from lib import realpanel  # noqa: E402

# 15-mers, pairwise Hamming >= 3, invented for this test.
SEQ = {
    "a": "ACGTACGTACGTACG",
    "b": "TTGGCCAATTGGCCA",
    "c": "GGATCCGGATCCGGA",
    "d": "CCTAGGCCTAGGCCT",
    "e": "AAGCTTAAGCTTAAG",
    "f": "TCGATCGATCGATCG",
}

# (Samples, Name, Barcode, Sequence, Channel, Residues, Type)
PANEL_ROWS = [
    ("grp1", "Ag Alpha", "X0001", SEQ["a"], "PE", "ECD", "Target (Primary)"),
    ("grp1", "Ag Alpha Var", "X0002", SEQ["b"], "PE", "ECD", "Target (Secondary)"),
    ("grp1", "Ctl One", "X0003", SEQ["c"], "APC", "ECD", "Off-Target"),
    ("grp1", "Ctl Two", "X0004", SEQ["d"], "APC", "ECD", "Off-Target"),
    ("grp2", "Ag Beta", "X0005", SEQ["e"], "PE", "ECD", "Target (Primary)"),
    ("grp2", "Ag Gamma", "X0006", SEQ["f"], "PE", "ECD", "Target (Secondary)"),
    # SEQ["c"] again, under a DIFFERENT antigen name: the cross-sample reuse the sample column exists for.
    ("grp2", "Ctl Other", "X0003", SEQ["c"], "APC", "ECD", "Off-Target"),
]


@pytest.fixture
def panel_csv(tmp_path):
    path = tmp_path / "panel.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Samples", "Name", "Barcode", "Sequence", "Channel", "Residues", "Type"])
        w.writerows(PANEL_ROWS)
    return path


def run_generator(panel_csv, out_dir, *extra):
    return subprocess.run(
        [sys.executable, str(HERE / "generate.py"), "--real-panel", str(panel_csv),
         "--cells-per-sample", "300", "--barcode-source", "random", "--out", str(out_dir), *extra],
        capture_output=True, text=True,
    )


# --- panel loading -------------------------------------------------------------------------------

def test_loads_one_panel_per_sample(panel_csv):
    panels = realpanel.load_wide_panel(str(panel_csv))
    assert list(panels) == ["grp1", "grp2"], "samples keep file order"
    assert len(panels["grp1"].names) == 4
    assert len(panels["grp2"].names) == 3


def test_role_matched_on_the_leading_word(panel_csv):
    """`Target (Primary)` and `Target (Secondary)` are two distinct values and both are on-target."""
    panels = realpanel.load_wide_panel(str(panel_csv))
    assert panels["grp1"].targets == ["Ag Alpha", "Ag Alpha Var"]
    assert panels["grp1"].offtargets == ["Ctl One", "Ctl Two"]


def test_a_sequence_may_carry_different_names_in_different_samples(panel_csv):
    panels = realpanel.load_wide_panel(str(panel_csv))
    assert panels["grp1"].barcode["Ctl One"] == panels["grp2"].barcode["Ctl Other"]


def test_duplicate_sequence_within_one_sample_is_rejected(tmp_path):
    """The block's own guard rejects it, so the bed must not be able to produce it."""
    path = tmp_path / "dup.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Samples", "Name", "Sequence", "Type"])
        w.writerow(["grp1", "One", SEQ["a"], "Target"])
        w.writerow(["grp1", "Two", SEQ["a"], "Off-Target"])
    with pytest.raises(SystemExit, match="appears twice"):
        realpanel.load_wide_panel(str(path))


def test_wrong_length_sequence_is_rejected(tmp_path):
    path = tmp_path / "short.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Samples", "Name", "Sequence", "Type"])
        w.writerow(["grp1", "One", "ACGT", "Target"])
    with pytest.raises(SystemExit, match="expected 15"):
        realpanel.load_wide_panel(str(path))


def test_missing_column_names_the_flags_that_fix_it(tmp_path):
    path = tmp_path / "wrong.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "antigen", "bc", "role"])
        w.writerow(["grp1", "One", SEQ["a"], "Target"])
    with pytest.raises(SystemExit, match="--panel-seq-col"):
        realpanel.load_wide_panel(str(path))


def test_columns_are_overridable(tmp_path):
    path = tmp_path / "renamed.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "antigen", "bc", "role"])
        w.writerow(["grp1", "One", SEQ["a"], "Target"])
        w.writerow(["grp1", "Two", SEQ["b"], "Off-Target"])
    panels = realpanel.load_wide_panel(
        str(path), columns={"sample": "sample_id", "name": "antigen", "sequence": "bc", "role": "role"}
    )
    assert panels["grp1"].targets == ["One"]
    assert panels["grp1"].offtargets == ["Two"]


# --- the reading rule ----------------------------------------------------------------------------

@pytest.mark.parametrize("count,reference,expected", [
    (0, 0, 0.0421875),
    (1, 1, 0.01487109375),
    (60, 4, 23.5711265135),
    (150, 6, 85.0165945400),
    (300, 20, 63.2309112766),
    (600, 50, 29.4968614580),
])
def test_specificity_score_matches_the_block(count, reference, expected):
    """The bed's standard-library score against the values scipy gives for the block's own formula.
    They have to agree, or the verdict simulation predicts a different run than the block produces."""
    assert realpanel.specificity_score(count, reference) == pytest.approx(expected, rel=1e-9)


def test_the_line_sits_where_the_tiers_assume(tmp_path):
    """`medium` is 60-200 UMIs because the line against a comparator of ~5 is ~120. If that moves, the
    tier stops straddling anything and the bed silently loses its borderline cells."""
    below = realpanel.specificity_score(60, 5)
    above = realpanel.specificity_score(200, 5)
    assert below < realpanel.CUTOFF < above


# --- a whole run ---------------------------------------------------------------------------------

def test_a_run_generates_and_validates(panel_csv, tmp_path):
    out = tmp_path / "run"
    res = run_generator(panel_csv, out)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "PASS" in res.stdout
    for rel in ("panel.csv", "RUN.md", "truth/expected-readings.tsv", "truth/expected-abundance.tsv",
                "truth/expected-consensus.tsv", "truth/library-quality.tsv", "truth/panel-canonical.csv"):
        assert (out / rel).exists(), rel
    for sample in ("grp1", "grp2"):
        assert (out / "antigen" / f"{sample}_R1.fastq.gz").exists()
        assert (out / "antigen" / f"{sample}_R2.fastq.gz").exists()
        assert (out / "vdj" / f"{sample}.tsv").exists()


def test_the_uploaded_panel_is_the_source_file_byte_for_byte(panel_csv, tmp_path):
    """The block must read the panel as it arrived, not a re-serialisation of it."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    assert (out / "panel.csv").read_bytes() == panel_csv.read_bytes()


def test_every_tier_a_sample_can_support_is_present(panel_csv, tmp_path):
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "truth" / "expected-readings.tsv", newline="") as fh:
        tiers = {r["tier"] for r in csv.DictReader(fh, delimiter="\t")}
    assert tiers == set(realpanel.TIER_NAMES), f"missing {set(realpanel.TIER_NAMES) - tiers}"


def test_no_negative_control_is_planted(panel_csv, tmp_path):
    """A real role column declares no comparator, so the run must not invent one — otherwise the
    declared-reference path is exercised and the panel-reference path never is."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "truth" / "expected-abundance.tsv", newline="") as fh:
        features = {r["feature"] for r in csv.DictReader(fh, delimiter="\t")}
    assert "negative_control" not in features
    assert features <= {name for _s, name, _b, _q, _c, _r, _t in PANEL_ROWS}


def test_the_run_is_reproducible(panel_csv, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert run_generator(panel_csv, a).returncode == 0
    assert run_generator(panel_csv, b).returncode == 0
    for rel in ("antigen/grp1_R1.fastq.gz", "antigen/grp1_R2.fastq.gz",
                "truth/expected-readings.tsv", "vdj/grp1.tsv"):
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_library_quality_profiles_change_the_expected_tag(panel_csv, tmp_path):
    out = tmp_path / "run"
    assert run_generator(panel_csv, out, "--library-quality", "spread").returncode == 0
    with open(out / "truth" / "library-quality.tsv", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert {r["libraryTier"] for r in rows} == {"clean", "good"}, "two samples take the first two tiers"
    out2 = tmp_path / "run2"
    assert run_generator(panel_csv, out2, "--library-quality", "uniform").returncode == 0
    with open(out2 / "truth" / "library-quality.tsv", newline="") as fh:
        assert {r["libraryTier"] for r in csv.DictReader(fh, delimiter="\t")} == {"clean"}


def test_offset_zero_moves_the_feature_to_the_front_of_r2(panel_csv, tmp_path):
    import gzip

    out = tmp_path / "run"
    assert run_generator(panel_csv, out, "--offset", "0").returncode == 0
    panels = realpanel.load_wide_panel(str(panel_csv))
    at_front = 0
    with gzip.open(out / "antigen" / "grp1_R2.fastq.gz", "rt") as fh:
        for i, line in enumerate(fh):
            if i >= 4000:
                break
            if i % 4 == 1 and line[:15] in set(panels["grp1"].barcodes):
                at_front += 1
    assert at_front > 100, "with offset 0 the feature barcode sits at R2 position 0"


# --- sample metadata ------------------------------------------------------------------------------

def test_sample_metadata_names_exactly_the_panels_samples(panel_csv, tmp_path):
    """Samples & Data joins metadata on the sample name. A row for a sample that is not in the run, or a
    sample with no row, means a grouping column that silently covers part of the run."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "samples-metadata.tsv", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert {r["Sample"] for r in rows} == {"grp1", "grp2"}
    assert list(rows[0]) == ["Sample", "Donor", "Condition", "LibraryQuality", "PanelMembers", "PanelTargets"]


def test_metadata_carries_the_per_sample_panel_shape(panel_csv, tmp_path):
    """PanelMembers / PanelTargets are the two metadata columns that are read from the panel rather than
    invented, and the per-sample difference is what makes *never asked* reachable."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "samples-metadata.tsv", newline="") as fh:
        by_sample = {r["Sample"]: r for r in csv.DictReader(fh, delimiter="\t")}
    assert by_sample["grp1"]["PanelMembers"] == "4"
    assert by_sample["grp2"]["PanelMembers"] == "3"
    assert by_sample["grp1"]["PanelTargets"] == "2"


def test_metadata_records_the_library_tier(panel_csv, tmp_path):
    out = tmp_path / "run"
    assert run_generator(panel_csv, out, "--library-quality", "uniform").returncode == 0
    with open(out / "samples-metadata.tsv", newline="") as fh:
        assert {r["LibraryQuality"] for r in csv.DictReader(fh, delimiter="\t")} == {"clean"}


# --- reshaping the repertoire without regenerating the reads ---------------------------------------

def test_arm_vdj_rebuilds_the_repertoire_and_leaves_the_reads_alone(panel_csv, tmp_path):
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    fastq = out / "antigen" / "grp1_R1.fastq.gz"
    before_reads = fastq.read_bytes()
    before_vdj = (out / "vdj" / "grp1.tsv").read_bytes()

    res = run_generator(panel_csv, out, "--arm", "vdj", "--clonal-mean-size", "60")
    assert res.returncode == 0, res.stdout + res.stderr
    assert fastq.read_bytes() == before_reads, "the antigen arm must not be regenerated"
    assert (out / "vdj" / "grp1.tsv").read_bytes() != before_vdj, "the repertoire must change"


def test_arm_vdj_without_an_antigen_arm_says_so(panel_csv, tmp_path):
    res = run_generator(panel_csv, tmp_path / "empty", "--arm", "vdj")
    assert res.returncode != 0
    assert "rebuilds the repertoire over an EXISTING antigen arm" in res.stdout + res.stderr


def test_most_cells_sit_in_expanded_clones(panel_csv, tmp_path):
    """The shape that matters for an antibody-discovery bed: a clonotype's verdict must usually rest on
    SEVERAL cells. Asserted on the share of CELLS in clones of >= 10, not on the share of clonotypes.
    Singletons are legitimately a large share of clonotypes and a small share of cells, and confusing
    the two is what produced a 97%-singleton repertoire in the first place."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "truth" / "truth_clonotypes.csv", newline="") as fh:
        sizes = [int(r["nCells"]) for r in csv.DictReader(fh)]
    cells = sum(sizes)
    in_expanded = sum(s for s in sizes if s >= 10)
    assert in_expanded / cells > 0.5, f"only {in_expanded}/{cells} cells in clones of >= 10"
    assert max(sizes) >= 20, f"largest clone is {max(sizes)} cells — no lead to find"


def test_clone_size_knobs_move_the_distribution(panel_csv, tmp_path):
    def biggest(out):
        with open(out / "truth" / "truth_clonotypes.csv", newline="") as fh:
            return max(int(r["nCells"]) for r in csv.DictReader(fh))

    diverse, expanded = tmp_path / "d", tmp_path / "e"
    assert run_generator(panel_csv, diverse, "--clonal-mean-size", "4").returncode == 0
    assert run_generator(panel_csv, expanded, "--clonal-mean-size", "60").returncode == 0
    assert biggest(expanded) > biggest(diverse)


def test_crossreactive_cells_form_clones_not_singletons(panel_csv, tmp_path):
    """A cross-reactive clonotype is a real lead. If those cells are all singletons, no clonotype is ever
    cross-reactive with more than one cell agreeing, and the two-identity case is untestable."""
    out = tmp_path / "run"
    assert run_generator(panel_csv, out).returncode == 0
    with open(out / "truth" / "truth_clonotypes.csv", newline="") as fh:
        cr = [int(r["nCells"]) for r in csv.DictReader(fh) if r["targetAntigen"] == "crossreactive"]
    assert cr, "no cross-reactive clones at all"
    assert max(cr) > 1, "every cross-reactive clone is a singleton"


def test_clone_sizes_account_for_every_cell():
    """A size list that does not sum to n drops or duplicates cells, and the cross-arm join then loses
    them silently."""
    from lib import vdj

    for n in (0, 1, 2, 7, 50, 1500, 3701):
        for mean_size in (2, 5, 25, 60):
            for frac in (0.0, 0.1, 0.35, 1.0):
                sizes = vdj._clone_sizes(n, mean_size, frac)
                assert sum(sizes) == n, (n, mean_size, frac, sum(sizes))
                assert all(s >= 1 for s in sizes)


# --- regimes -------------------------------------------------------------------------------------
#
# Two measured calibrations exist and they disagree by more than an order of magnitude. `deep` is the
# public 10x BEAM shape, and `shallow` stands in for real in-vivo BEAM libraries. The contract these
# tests hold is that `deep` is unchanged by the existence of `shallow`, and that `shallow` actually lands
# in the regime it claims rather than merely running.

NARROW_ROWS = [
    ("grp1", SEQ["a"], "Ag Alpha"),
    ("grp1", SEQ["b"], "Ag Alpha Var"),
    ("grp1", SEQ["c"], "Ctl One (high OT risk)"),
    ("grp1", SEQ["d"], "Ctl Two homology"),
    ("grp2", SEQ["e"], "Ag Beta"),
    ("grp2", SEQ["f"], "Ag Gamma"),
    ("grp2", SEQ["c"], "Ctl Other off-target"),
]


@pytest.fixture
def narrow_panel_csv(tmp_path):
    """The NARROW shape: sample, sequence, antigen — no role column. Role lives in the antigen name."""
    path = tmp_path / "narrow.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Sample", "Sequence", "Antigen"])
        w.writerows(NARROW_ROWS)
    return path


def test_regime_tables_are_complete():
    """Every regime supplies every key the builder reads, and both tier tables use the same tier names.

    The tier NAMES are shared on purpose: the truth tables, the validator and RUN.md all key off them,
    so a regime that renamed a tier would break those silently rather than loudly."""
    keys = set(realpanel.REGIMES["deep"])
    for name, spec in realpanel.REGIMES.items():
        assert set(spec) == keys, f"{name} has {set(spec) ^ keys} against deep"
        assert abs(sum(w for _n, w, _d in spec["tiers"]) - 1.0) < 1e-9, f"{name} weights do not sum to 1"
        assert [t[0] for t in spec["tiers"]] == realpanel.TIER_NAMES
        for tier in ("strong", "good", "medium", "weak", "noise"):
            lo, hi = spec["magnitudes"][tier]
            assert 0 < lo <= hi


def test_shallow_magnitudes_are_ordered_below_deep():
    """Signal ordering holds within a regime, and shallow sits strictly under deep.

    Ordering is what the shallow validator asserts instead of absolute bound rates, so if the
    magnitudes ever stop descending the validator's monotonicity check becomes vacuous."""
    for spec in realpanel.REGIMES.values():
        ladder = [spec["magnitudes"][t] for t in ("strong", "good", "medium", "weak", "noise")]
        for (lo, _hi), (nlo, _nhi) in zip(ladder, ladder[1:]):
            assert lo > nlo
    for tier in ("strong", "good", "medium"):
        assert realpanel.MAGNITUDES_SHALLOW[tier][1] < realpanel.MAGNITUDES_DEEP[tier][1]


def test_deep_regime_is_the_default(panel_csv, tmp_path):
    """The default path must not acquire the shallow regime's behaviours by accident: no aggregates, no
    sized barcode universe, and the original duplication draw."""
    r = realpanel.REGIMES["deep"]
    assert r["aggregates"] == 0 and r["ambient_barcode_ratio"] == 0.0
    assert r["dup_mean"] is None
    assert r["unpaired_frac"] == 0.0
    out = run_generator(panel_csv, tmp_path / "run", "--no-validate")
    assert out.returncode == 0, out.stderr
    assert not (tmp_path / "run" / "truth" / "aggregates.tsv").exists()
    assert (tmp_path / "run" / "truth" / "regime.txt").read_text().strip() == "deep"


def test_shallow_run_lands_in_the_measured_regime(panel_csv, tmp_path):
    """A shallow run reproduces the shape real in-vivo data actually shows, checked from the truth tables
    rather than from the log line."""
    out = run_generator(panel_csv, tmp_path / "run", "--regime", "shallow")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "[validate]" in out.stdout and "FAIL" not in out.stdout, out.stdout
    assert (tmp_path / "run" / "truth" / "regime.txt").read_text().strip() == "shallow"

    # Aggregates: five per library, and the largest holds far more than an even split would.
    aggs = list(csv.DictReader(open(tmp_path / "run" / "truth" / "aggregates.tsv"), delimiter="\t"))
    per_sample = {}
    for row in aggs:
        per_sample.setdefault(row["sample"], []).append(int(row["umis"]))
    assert per_sample, "shallow planted no aggregates"
    for sample, umis in per_sample.items():
        assert len(umis) == 5, f"{sample} has {len(umis)} aggregates"
        assert max(umis) > sum(umis) / len(umis), f"{sample} aggregates are evenly sized"

    # Cells per clonotype sits near the measured 1.05, which TAIL_CYCLE alone cannot reach.
    sizes = [int(r["nCells"]) for r in csv.DictReader(open(tmp_path / "run" / "truth" / "truth_clonotypes.csv"))]
    assert sizes
    assert 1.0 <= sum(sizes) / len(sizes) <= 1.15, f"{sum(sizes) / len(sizes):.2f} cells per clonotype"


def test_shallow_tail_cycle_beats_the_deep_floor():
    """TAIL_CYCLE averages 1.47 cells per clonotype and is a floor no parameter setting gets under. The
    sparse cycle is why the shallow regime can reach 1.05."""
    from lib import vdj
    assert sum(vdj.TAIL_CYCLE) / len(vdj.TAIL_CYCLE) > 1.4
    sparse = sum(vdj.TAIL_CYCLE_SPARSE) / len(vdj.TAIL_CYCLE_SPARSE)
    assert 1.0 < sparse <= 1.06, sparse
    assert realpanel.REGIMES["shallow"]["clonal_tail_cycle"] is vdj.TAIL_CYCLE_SPARSE
    assert realpanel.REGIMES["deep"]["clonal_tail_cycle"] is None


# --- the narrow panel shape ----------------------------------------------------------------------

def test_narrow_shape_is_detected_and_wide_still_is(panel_csv, narrow_panel_csv):
    assert realpanel.detect_panel_shape(str(panel_csv)) == "wide"
    assert realpanel.detect_panel_shape(str(narrow_panel_csv)) == "narrow"


def test_narrow_panel_infers_role_from_the_antigen_name(narrow_panel_csv):
    """A narrow panel declares no role column, so role has to come from the name. Getting this wrong in
    the permissive direction is the dangerous one: a target mistaken for a comparator moves the line
    every reading in the sample is judged against."""
    panels = realpanel.load_panel(str(narrow_panel_csv))
    grp1 = panels["grp1"]
    assert set(grp1.targets) == {"Ag Alpha", "Ag Alpha Var"}
    assert set(grp1.offtargets) == {"Ctl One (high OT risk)", "Ctl Two homology"}
    assert set(panels["grp2"].offtargets) == {"Ctl Other off-target"}


def test_control_feature_overrides_the_name(narrow_panel_csv):
    """Naming a control wins over the name heuristic, mirroring the block's own dropdown."""
    panels = realpanel.load_panel(str(narrow_panel_csv), control_feature="Ag Alpha")
    assert "Ag Alpha" in panels["grp1"].offtargets
    assert "Ag Alpha" not in panels["grp1"].targets


def test_narrow_panel_drives_a_whole_run(narrow_panel_csv, tmp_path):
    out = run_generator(narrow_panel_csv, tmp_path / "run", "--regime", "shallow")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "FAIL" not in out.stdout, out.stdout
