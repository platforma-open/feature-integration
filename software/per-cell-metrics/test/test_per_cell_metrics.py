"""Behavioral tests for per_cell_metrics.py (Feature Integration software).

Run from the software/ directory:
    uv sync --all-groups
    uv run pytest -m "not slow"   # fast: pure functions + properties
    uv run pytest                 # + the slow CLI/golden lane
"""

import csv
import pathlib
import subprocess
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st
from per_cell_metrics import (
    CROSS_REACTIVE,
    _load,
    combine_barcode_counts,
    consensus_category,
    offtarget_features,
    specificity_score,
)

SRC = pathlib.Path(__file__).parents[1] / "src" / "per_cell_metrics.py"


# --- dominant-category rule (spec A-0012) ---


def test_consensus_single_winner_above_threshold():
    # 7 of 10 -> 0.7 >= 0.6 default -> that feature
    assert consensus_category({"A": 7, "B": 2, "C": 1}, 0.6) == "A"


def test_consensus_winner_exactly_at_threshold():
    assert consensus_category({"A": 6, "B": 4}, 0.6) == "A"


def test_consensus_no_winner_is_ambiguous():
    # max share 0.4 < 0.6 -> ambiguous (signal present, none passes)
    assert consensus_category({"A": 4, "B": 3, "C": 3}, 0.6) == "ambiguous"


def test_consensus_exact_half_split_at_floor_is_ambiguous():
    # 50/50 at the 0.5 floor -> tie -> ambiguous (A-0012: "an exact split at the 0.5 floor")
    assert consensus_category({"A": 5, "B": 5}, 0.5) == "ambiguous"


def test_consensus_threshold_clamped_to_floor():
    # request 0.4 but floor is 0.5; 0.55 share passes 0.5, unique -> winner
    assert consensus_category({"A": 11, "B": 9}, 0.4) == "A"


def test_consensus_single_category():
    assert consensus_category({"A": 3}, 0.6) == "A"


def test_consensus_no_signal_is_none():
    assert consensus_category({"A": 0, "B": 0}, 0.6) is None
    assert consensus_category({}, 0.6) is None


# --- negative control is a reference, not a callable antigen (spec A-0014) ---


def test_consensus_excludes_control_from_candidates():
    # The control must not win consensus even when it has the most UMIs: AGX 3 / CTRL 5 -> the top
    # antigen (AGX) share is 3/8 = 0.375 < 0.6 -> ambiguous, NOT "CTRL".
    assert consensus_category({"AGX": 3, "CTRL": 5}, 0.6, control="CTRL") == "ambiguous"


def test_consensus_control_stays_in_denominator():
    # Control UMIs remain in the denominator, so control signal suppresses (not inflates) dominance.
    # AGX 7 / CTRL 2 -> 7/9 = 0.78 >= 0.6 -> AGX (control did not spuriously push it under threshold).
    assert consensus_category({"AGX": 7, "CTRL": 2}, 0.6, control="CTRL") == "AGX"
    # Were the control dropped from the denominator, AGX 3 / CTRL 5 would renormalise to 1.0 and wrongly
    # win; keeping it in the denominator makes the control-swamped cell correctly ambiguous.
    assert consensus_category({"AGX": 3, "CTRL": 5}, 0.6, control="CTRL") == "ambiguous"


def test_consensus_control_only_is_ambiguous():
    # A cell whose only signal is the control has no antigen candidate -> ambiguous, never the control.
    assert consensus_category({"CTRL": 5}, 0.6, control="CTRL") == "ambiguous"


def test_consensus_no_control_arg_is_unchanged():
    # control=None (no negative control set) keeps the original rule: every feature is a candidate.
    assert consensus_category({"AGX": 3, "OTHER": 5}, 0.6) == "OTHER"


# --- off-target-aware dominant call + cross-reactive label (spec A-0014 Type-aware direction, F2) ---


def test_consensus_excludes_offtargets_like_control():
    # An off-target feature is excluded from the winners exactly as the control is: OT swamps the cell,
    # the single on-target's share of the total is 3/8 = 0.375 < 0.6 -> ambiguous, never "OT".
    assert consensus_category({"AGX": 3, "OT": 5}, 0.6, offtargets=frozenset({"OT"})) == "ambiguous"


def test_consensus_offtargets_stay_in_denominator():
    # Off-target UMIs remain in the denominator (suppress, not inflate): AGX 7 / OT 2 -> 7/9 >= 0.6 -> AGX.
    assert consensus_category({"AGX": 7, "OT": 2}, 0.6, offtargets=frozenset({"OT"})) == "AGX"


def test_consensus_crossreactive_two_ontargets_pass_together():
    # Two on-targets (same target's human+cyno) split ~50/50 with only minor off-target signal: neither
    # passes alone, but the on-target set is 90% of the total across 2 features -> cross-reactive, not
    # ambiguous. This is the binder F2 rescues from the overloaded "ambiguous" bucket.
    assert (
        consensus_category(
            {"TgtA_human": 45, "TgtA_cyno": 45, "OT": 10},
            0.6,
            offtargets=frozenset({"OT"}),
            label_crossreactive=True,
        )
        == CROSS_REACTIVE
    )


def test_consensus_crossreactive_needs_label_flag():
    # Without the label flag the same split stays "ambiguous" (backward-compatible when the feature is off).
    assert (
        consensus_category({"TgtA_human": 45, "TgtA_cyno": 45, "OT": 10}, 0.6, offtargets=frozenset({"OT"}))
        == "ambiguous"
    )


def test_consensus_offtarget_swamped_is_ambiguous_not_crossreactive():
    # On-target set collectively below threshold (off-target-dominated) -> ambiguous, never cross-reactive:
    # TgtA 20 + TgtB 20 = 40 of 100 (0.4 < 0.6); OT 60 swamps.
    assert (
        consensus_category(
            {"TgtA": 20, "TgtB": 20, "OT": 60},
            0.6,
            offtargets=frozenset({"OT"}),
            label_crossreactive=True,
        )
        == "ambiguous"
    )


def test_consensus_crossreactive_single_ontarget_still_calls_feature():
    # A single dominant on-target still wins outright (not cross-reactive): AGX 80 / OT 20 -> AGX.
    assert (
        consensus_category({"AGX": 80, "OT": 20}, 0.6, offtargets=frozenset({"OT"}), label_crossreactive=True) == "AGX"
    )


def test_consensus_only_offtarget_signal_is_ambiguous():
    # A cell whose only signal is off-target has no on-target candidate -> ambiguous.
    assert consensus_category({"OT": 5}, 0.6, offtargets=frozenset({"OT"}), label_crossreactive=True) == "ambiguous"


def test_offtarget_features_resolves_from_property_column(tmp_path):
    # The off-target feature set is resolved from a designated property column + its off-target values.
    csv = tmp_path / "tags.csv"
    csv.write_text(
        "tag,feature,antigen_class\n"
        "b1,TgtA,Target\n"
        "b2,TgtB,Target\n"
        "b3,DecoyX,Decoy\n"
        "b4,OTx, Off-Target \n"  # whitespace tolerated (stripped)
    )
    got = offtarget_features(str(csv), "feature", "antigen_class", frozenset({"Off-Target", "Decoy"}))
    assert got == frozenset({"DecoyX", "OTx"})


def test_offtarget_features_bad_column_exits(tmp_path):
    csv = tmp_path / "tags.csv"
    csv.write_text("tag,feature\nb1,TgtA\n")
    with pytest.raises(SystemExit):
        offtarget_features(str(csv), "feature", "nope", frozenset({"Off-Target"}))


def test_offtarget_features_matching_is_case_insensitive(tmp_path):
    # Real B043 panel: the Type column carries BOTH casings of the off-target designation ("Off-Target"
    # AND "Off-target"). A user who selects one canonical value must catch every casing -> matching is
    # case- AND whitespace-insensitive on both sides. Returned FEATURE names stay verbatim from the CSV.
    csv = tmp_path / "tags.csv"
    csv.write_text(
        "tag,feature,Type\n"
        "b1,AgOffLower,Off-target\n"  # lower 't' — the B043 duplicate casing
        "b2,AgOffSpaced, OFF-TARGET \n"  # different case + surrounding whitespace
        "b3,AgOn,Target\n"
    )
    got = offtarget_features(str(csv), "feature", "Type", frozenset({"Off-Target"}))
    assert got == frozenset({"AgOffLower", "AgOffSpaced"})


# --- specificity score (spec A-0014, Cell Ranger betaCDF) ---


def test_specificity_strong_signal_high_score():
    # many antigen UMIs, no control -> high confidence (the betaCDF formula gives ~98.6 here)
    s = specificity_score(antigen_umi=100, control_umi=0)
    assert s > 95.0


def test_specificity_no_signal_low_score():
    # no antigen reads, control present -> low confidence
    s = specificity_score(antigen_umi=0, control_umi=20)
    assert 0.0 <= s < 5.0


def test_specificity_formula_exact():
    # Reference-oracle guard against a constant typo. Weak on its own (mirrors the impl via the same
    # scipy call); the bounds + monotonicity PROPERTIES below are the real behavioral guards.
    from scipy.stats import beta

    a, c = 7, 3
    expected = (1.0 - float(beta.cdf(0.925, a + 1, c + 3))) * 100.0
    assert specificity_score(a, c) == pytest.approx(expected)


# --- properties (invariants that hold for ALL valid inputs) ---


@given(
    st.dictionaries(st.text(min_size=1), st.integers(min_value=0, max_value=1000), max_size=8),
    st.floats(min_value=0.5, max_value=1.0),
)
def test_consensus_result_in_domain(counts, threshold):
    # The result is always a key present in counts, "ambiguous", or None -- never an arbitrary string.
    r = consensus_category(counts, threshold)
    assert r is None or r == "ambiguous" or r in counts


@given(
    st.dictionaries(st.text(min_size=1), st.integers(min_value=0, max_value=1000), max_size=8),
    st.floats(min_value=0.5, max_value=1.0),
    st.sets(st.text(min_size=1), max_size=4),
)
def test_consensus_offtarget_result_in_domain(counts, threshold, offtargets):
    # With off-targets + the label on, the result is an on-target key, "cross-reactive", "ambiguous", or
    # None -- and never an off-target/control key (they can never win).
    r = consensus_category(counts, threshold, offtargets=frozenset(offtargets), label_crossreactive=True)
    assert r is None or r in ("ambiguous", CROSS_REACTIVE) or (r in counts and r not in offtargets)


@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=0, max_value=10_000))
def test_specificity_bounded_0_100(antigen, control):
    # It is a confidence percentage: always within [0, 100].
    assert 0.0 <= specificity_score(antigen, control) <= 100.0


@given(
    st.integers(min_value=0, max_value=500),  # control
    st.integers(min_value=0, max_value=500),  # base antigen
    st.integers(min_value=1, max_value=500),  # delta
)
def test_specificity_monotonic_in_antigen(control, base, delta):
    # More antigen UMIs (same control) never lowers confidence.
    assert specificity_score(base + delta, control) >= specificity_score(base, control)


# --- multi-barcode antigen combine modes: sum (OR) / all (AND) ---

# A dual-barcode antigen (BG505 read out by b1 + b2) alongside a single-barcode antigen (OTHER = cx).
_B2F = {"b1": "BG505", "b2": "BG505", "cx": "OTHER"}
_FB = {"BG505": {"b1", "b2"}, "OTHER": {"cx"}}


def test_combine_sum_is_default_and_adds_members():
    # No modes -> everything sums (OR); the two BG505 barcodes add up, matching historical behaviour.
    assert combine_barcode_counts({"b1": 3, "b2": 4, "cx": 2}, _B2F, _FB, {}) == {"BG505": 7, "OTHER": 2}


def test_combine_all_both_fire_emits_summed():
    assert combine_barcode_counts({"b1": 3, "b2": 4}, _B2F, _FB, {"BG505": "all"}) == {"BG505": 7}


def test_combine_all_one_missing_omits_feature():
    # Only one BG505 barcode fired -> under AND the antigen is NOT called; the cell has no BG505 entry
    # at all (omitted, not zero), so it never competes for dominance or takes a fraction.
    assert combine_barcode_counts({"b1": 5}, _B2F, _FB, {"BG505": "all"}) == {}


def test_combine_all_respects_min_umi():
    # Both present but b2 below the min-UMI floor -> not all fired -> omitted.
    assert combine_barcode_counts({"b1": 9, "b2": 2}, _B2F, _FB, {"BG505": "all"}, min_umi=3) == {}
    # b2 at the floor -> now both fired -> called, summed.
    assert combine_barcode_counts({"b1": 9, "b2": 3}, _B2F, _FB, {"BG505": "all"}, min_umi=3) == {"BG505": 12}


def test_combine_mixed_modes_in_one_cell():
    # BG505 = AND, OTHER = OR (default). Both barcodes of BG505 present -> both features called.
    assert combine_barcode_counts({"b1": 2, "b2": 2, "cx": 5}, _B2F, _FB, {"BG505": "all"}) == {
        "BG505": 4,
        "OTHER": 5,
    }
    # Drop one BG505 barcode -> BG505 gone, OTHER (OR) still called.
    assert combine_barcode_counts({"b1": 2, "cx": 5}, _B2F, _FB, {"BG505": "all"}) == {"OTHER": 5}


def test_combine_off_panel_barcode_ignored():
    # A barcode with no feature mapping is ignored (mirrors the tag->feature inner join).
    assert combine_barcode_counts({"b1": 1, "b2": 1, "zzz": 99}, _B2F, _FB, {"BG505": "all"}) == {"BG505": 2}


def test_combine_single_barcode_all_is_presence():
    # A single-barcode feature under AND reduces to "present at >= min_umi".
    assert combine_barcode_counts({"cx": 1}, _B2F, _FB, {"OTHER": "all"}) == {"OTHER": 1}


@given(
    st.dictionaries(st.sampled_from(["b1", "b2", "cx"]), st.integers(min_value=1, max_value=50), max_size=3),
    st.integers(min_value=1, max_value=10),
)
def test_combine_all_never_calls_without_every_member(cell, min_umi):
    # Property: an "all"-mode feature appears in the output ONLY when every member barcode is present
    # with umi >= min_umi. (BG505 is AND here; OTHER is OR.)
    out = combine_barcode_counts(cell, _B2F, _FB, {"BG505": "all"}, min_umi=float(min_umi))
    if "BG505" in out:
        assert all(cell.get(bc, 0) >= min_umi for bc in _FB["BG505"])
        assert out["BG505"] == sum(cell[bc] for bc in _FB["BG505"])


def test_load_no_combine_col_sums_shared_feature(tmp_path):
    # Backward-compat: with no combine column, two barcodes on one feature name still sum (OR).
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature\nb1,BG505\nb2,BG505\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\nc1\tb1\t3\t3\t3\nc1\tb2\t4\t4\t4\n")
    df = _load(str(tagstat), str(tags), "CELL", "FEATURE", "unique_UMI", "tag", "feature")
    got = {(r["cellId"], r["feature"]): int(r["umiCount"]) for r in df.to_dicts()}
    assert got == {("c1", "BG505"): 7}


def test_load_matches_pure_combine(tmp_path):
    # Oracle: the vectorized _load AND/OR gate must equal the pure combine_barcode_counts rule.
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,combine\nb1,BG505,all\nb2,BG505,all\ncx,OTHER,sum\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellBoth\tb1\t3\t3\t3\n"
        "cellBoth\tb2\t4\t4\t4\n"  # both BG505 barcodes fire -> BG505 = 7
        "cellBoth\tcx\t2\t2\t2\n"  # OTHER = 2
        "cellOne\tb1\t5\t5\t5\n"  # only b1 -> BG505 omitted (AND)
        "cellOne\tcx\t9\t9\t9\n"  # OTHER = 9
        "cellOther\tcx\t1\t1\t1\n"  # only OTHER
    )
    df = _load(
        str(tagstat),
        str(tags),
        "CELL",
        "FEATURE",
        "unique_UMI",
        "tag",
        "feature",
        combine_col="combine",
        min_umi=1.0,
    )
    got = {(r["cellId"], r["feature"]): int(r["umiCount"]) for r in df.to_dicts()}

    modes = {"BG505": "all", "OTHER": "sum"}
    cells = {
        "cellBoth": {"b1": 3, "b2": 4, "cx": 2},
        "cellOne": {"b1": 5, "cx": 9},
        "cellOther": {"cx": 1},
    }
    expected = {}
    for cell, bc in cells.items():
        for feat, umi in combine_barcode_counts(bc, _B2F, _FB, modes).items():
            expected[(cell, feat)] = int(umi)
    assert got == expected  # vectorized _load == pure rule
    # ...and spell out the AND effect so the oracle isn't vacuous.
    assert ("cellOne", "BG505") not in got
    assert got[("cellBoth", "BG505")] == 7


# --- end-to-end CLI over the committed bed (slow lane) ---


@pytest.mark.slow
def test_cli_writes_outputs(tagstat_tsv, tags_csv, tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    for name in ["result_abundance.csv", "result_fractions.csv", "result_consensus.csv"]:
        assert (tmp_path / name).exists(), f"missing {name}"


@pytest.mark.slow
def test_cli_consensus_golden(tagstat_tsv, tags_csv, tmp_path):
    # End-to-end over the committed bed: cell1 dominant on AGX, cell2 ambiguous, cell3 single-feature.
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_consensus.csv", newline="") as f:
        by_cell = {row["cellId"]: row["consensusFeature"] for row in csv.DictReader(f)}
    assert by_cell["cell1"] == "AGX"
    assert by_cell["cell2"] == "ambiguous"
    assert by_cell["cell3"] == "AGX"


@pytest.mark.slow
def test_cli_abundance_uses_unique_umi(tagstat_tsv, tags_csv, tmp_path):
    # DP-2: the matrix must use mitool's deduplicated `unique_UMI` (cell1/AGX = 3 distinct UMIs),
    # NOT the raw read `count` (which is 7 in the bed). Guards against reading the wrong column.
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_abundance.csv", newline="") as f:
        umi = {(r["cellId"], r["feature"]): int(r["umiCount"]) for r in csv.DictReader(f)}
    assert umi[("cell1", "AGX")] == 3  # unique_UMI, not count=7
    assert umi[("cell1", "BGX")] == 1
    assert umi[("cell3", "AGX")] == 2


@pytest.mark.slow
def test_cli_with_renamed_csv_columns(tagstat_tsv, tmp_path):
    # D4: the CSV's barcode/feature columns can be named anything -- --csv-barcode-col /
    # --csv-feature-col map them to the join key and output "feature" column. Barcode values
    # (AAAA, CCCC) match the committed tagstat_main.tsv bed; mapped to the same AGX/BGX names the
    # golden test expects, just via a differently-named CSV.
    renamed_csv = tmp_path / "renamed_tags.csv"
    renamed_csv.write_text("barcode,antigen\nAAAA,AGX\nCCCC,BGX\n")

    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(renamed_csv),
            "--sample-id",
            "s1",
            "--csv-barcode-col",
            "barcode",
            "--csv-feature-col",
            "antigen",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    out = tmp_path / "result_abundance.csv"
    assert out.exists()
    with open(out, newline="") as f:
        features = {row["feature"] for row in csv.DictReader(f)}
    assert features == {"AGX", "BGX"}


@pytest.mark.slow
def test_cli_rejects_colliding_feature_col(tagstat_tsv, tmp_path):
    # D4 guard: mapping --csv-feature-col onto a tag-stat column name (e.g. `count`) would otherwise
    # silently corrupt the output `feature` column with the wrong data. It must exit non-zero instead.
    renamed_csv = tmp_path / "renamed_tags.csv"
    renamed_csv.write_text("barcode,count\nAAAA,AGX\nCCCC,BGX\n")

    r = subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(renamed_csv),
            "--sample-id",
            "s1",
            "--csv-barcode-col",
            "barcode",
            "--csv-feature-col",
            "count",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        cwd=tmp_path,
    )
    assert r.returncode != 0


@pytest.mark.slow
def test_cli_with_control_writes_specificity(tagstat_tsv, tags_csv, tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    assert (tmp_path / "result_specificity.csv").exists()


@pytest.mark.slow
@pytest.mark.parametrize(
    "tagstat_body",
    [
        "cellX\tTTTT\t5\t5\t3\n",  # a real tag-stat row whose feature barcode is off-panel (not in tags.csv)
        "",  # a tag-stat with no rows at all (header only)
    ],
    ids=["off-panel-rows", "header-only"],
)
def test_cli_empty_join_writes_header_only_not_crash(tags_csv, tmp_path, tagstat_body):
    # Regression: when no (cell, feature) pair survives the tag->feature join -- a wrong read geometry,
    # or a sample with no on-panel reads -- the run must still emit all four CSVs header-only, never
    # crash. --control exercises the specificity write too. (consensus and specificity are pure-polars
    # transforms that carry their schema through the empty case; this guards that they stay header-only.)
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n" + tagstat_body)

    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,  # the old crash exited non-zero -> this would fail here
        cwd=tmp_path,
    )
    for name, header in [
        ("result_abundance.csv", ["sampleId", "cellId", "feature", "umiCount"]),
        ("result_fractions.csv", ["sampleId", "cellId", "feature", "fraction"]),
        ("result_consensus.csv", ["sampleId", "cellId", "consensusFeature"]),
        ("result_specificity.csv", ["sampleId", "cellId", "feature", "specificityScore"]),
    ]:
        p = tmp_path / name
        assert p.exists(), f"missing {name}"
        with open(p, newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == header  # schema/header preserved
            assert list(reader) == []  # zero data rows (empty result)


@pytest.mark.slow
def test_cli_per_cell_summary_maxima_match_exported_columns(tagstat_tsv, tags_csv, tmp_path):
    # The per-cell summary's maxUmiCount / maxFraction / maxSpecificityScore are a collapse of the
    # exported (cell x feature) columns -- they must equal the per-cell max of those exported CSVs, not a
    # separately-recomputed value (guards the with_fraction / with_specificity single-compute refactor).
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )

    def _max_by_cell(path, value_col, cast):
        by_cell: dict[str, float] = {}
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                v = cast(r[value_col])
                by_cell[r["cellId"]] = max(by_cell.get(r["cellId"], v), v)
        return by_cell

    exp_umi = _max_by_cell(tmp_path / "result_abundance.csv", "umiCount", int)
    exp_frac = _max_by_cell(tmp_path / "result_fractions.csv", "fraction", float)
    exp_spec = _max_by_cell(tmp_path / "result_specificity.csv", "specificityScore", float)

    with open(tmp_path / "result_per_cell_summary.csv", newline="") as f:
        summary = {r["cellId"]: r for r in csv.DictReader(f)}

    assert set(summary) == set(exp_umi)
    for cell, row in summary.items():
        assert int(row["maxUmiCount"]) == exp_umi[cell]
        assert float(row["maxFraction"]) == pytest.approx(exp_frac[cell])
        assert float(row["maxSpecificityScore"]) == pytest.approx(exp_spec[cell])


@pytest.mark.slow
def test_cli_consensus_matches_pure_rule(tmp_path):
    # Oracle: the vectorized CLI consensus must equal the pure consensus_category rule across cases the
    # committed golden bed doesn't cover (unique winner, exact tie, sub-threshold spread, single feature).
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature\nAAAA,AGX\nCCCC,BGX\nGGGG,CGX\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellW\tAAAA\t8\t8\t8\n"
        "cellW\tCCCC\t1\t1\t1\n"
        "cellW\tGGGG\t1\t1\t1\n"  # AGX 8 / BGX 1 / CGX 1 -> unique winner AGX (0.8 >= 0.6)
        "cellX\tAAAA\t5\t5\t5\n"
        "cellX\tCCCC\t5\t5\t5\n"  # AGX 5 / BGX 5 -> tie, 0.5 < 0.6 -> ambiguous
        "cellY\tAAAA\t4\t4\t4\n"
        "cellY\tCCCC\t3\t3\t3\n"
        "cellY\tGGGG\t3\t3\t3\n"  # max share 0.4 < 0.6 -> ambiguous
        "cellZ\tAAAA\t6\t6\t6\n"  # single feature -> AGX
    )
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--dominance-threshold",
            "0.6",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_consensus.csv", newline="") as f:
        got = {r["cellId"]: r["consensusFeature"] for r in csv.DictReader(f)}
    expected = {
        "cellW": consensus_category({"AGX": 8, "BGX": 1, "CGX": 1}, 0.6),
        "cellX": consensus_category({"AGX": 5, "BGX": 5}, 0.6),
        "cellY": consensus_category({"AGX": 4, "BGX": 3, "CGX": 3}, 0.6),
        "cellZ": consensus_category({"AGX": 6}, 0.6),
    }
    assert got == expected  # vectorized CLI == the pure rule
    # ...and the pure rule is what we think (guards against a vacuous match to a wrong rule)
    assert expected == {"cellW": "AGX", "cellX": "ambiguous", "cellY": "ambiguous", "cellZ": "AGX"}


@pytest.mark.slow
def test_cli_specificity_matches_pure_score(tagstat_tsv, tags_csv, tmp_path):
    # Oracle: the vectorized specificity column must equal the pure specificity_score per (cell, feature)
    # vs the cell's control (CTRL) UMIs -- 0 when the cell has no control reads. Guards the array path
    # (scipy beta.cdf over whole columns) against the scalar formula, including the fill_null(0) case.
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat_tsv),
            str(tags_csv),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_abundance.csv", newline="") as f:
        umi = {(r["cellId"], r["feature"]): int(r["umiCount"]) for r in csv.DictReader(f)}
    control_umi = {cell: umi.get((cell, "CTRL"), 0) for (cell, _feat) in umi}
    with open(tmp_path / "result_specificity.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows  # non-empty: the committed bed has cells and features
    assert "CTRL" not in {r["feature"] for r in rows}  # control is the reference, not a scored feature
    for r in rows:
        expected = specificity_score(umi[(r["cellId"], r["feature"])], control_umi[r["cellId"]])
        assert float(r["specificityScore"]) == pytest.approx(float(expected))


@pytest.mark.slow
def test_cli_consensus_excludes_control(tmp_path):
    # With --control set, the control is a reference and never a called antigen (spec A-0014): a
    # control-dominated cell must be "ambiguous", not the control. Control UMIs stay in the denominator,
    # so they suppress dominance rather than being renormalised away.
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature\nAAAA,AGX\nGGGG,CTRL\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellP\tAAAA\t3\t3\t3\n"
        "cellP\tGGGG\t5\t5\t5\n"  # AGX 3 / CTRL 5 -> top antigen 3/8 < 0.6 -> ambiguous (NOT CTRL)
        "cellQ\tAAAA\t7\t7\t7\n"
        "cellQ\tGGGG\t2\t2\t2\n"  # AGX 7 / CTRL 2 -> 7/9 = 0.78 >= 0.6 -> AGX
        "cellR\tGGGG\t5\t5\t5\n"  # only control signal -> ambiguous
    )
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--dominance-threshold",
            "0.6",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_consensus.csv", newline="") as f:
        got = {r["cellId"]: r["consensusFeature"] for r in csv.DictReader(f)}
    assert got == {"cellP": "ambiguous", "cellQ": "AGX", "cellR": "ambiguous"}
    # ...and the vectorized CLI agrees with the pure rule (guards against a vacuous match).
    assert got == {
        "cellP": consensus_category({"AGX": 3, "CTRL": 5}, 0.6, control="CTRL"),
        "cellQ": consensus_category({"AGX": 7, "CTRL": 2}, 0.6, control="CTRL"),
        "cellR": consensus_category({"CTRL": 5}, 0.6, control="CTRL"),
    }


@pytest.mark.slow
def test_cli_consensus_offtarget_and_crossreactive(tmp_path):
    # End-to-end: with an --offtarget-col/--offtarget-values designation the vectorized consensus must
    # match the pure rule -- off-targets excluded from winners, and an on-target-split cell called
    # cross-reactive. antigen_class is a per-feature property column of the tag CSV (A-0026 pass-through).
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,antigen_class\nAAAA,TgtA_human,Target\nCCCC,TgtA_cyno,Target\nGGGG,OTx,Off-Target\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellX\tAAAA\t45\t45\t45\n"
        "cellX\tCCCC\t45\t45\t45\n"
        "cellX\tGGGG\t10\t10\t10\n"  # human+cyno split 45/45, OT 10 -> cross-reactive
        "cellY\tAAAA\t80\t80\t80\n"
        "cellY\tGGGG\t20\t20\t20\n"  # single on-target 80/100 -> TgtA_human
        "cellZ\tAAAA\t20\t20\t20\n"
        "cellZ\tCCCC\t20\t20\t20\n"
        "cellZ\tGGGG\t60\t60\t60\n"  # OT-swamped (on-target 40/100 < 0.6) -> ambiguous
        "cellW\tGGGG\t7\t7\t7\n"  # only off-target signal -> ambiguous
    )
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--dominance-threshold",
            "0.6",
            "--offtarget-col",
            "antigen_class",
            "--offtarget-values",
            "Off-Target,Decoy",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_consensus.csv", newline="") as f:
        got = {r["cellId"]: r["consensusFeature"] for r in csv.DictReader(f)}
    ot = frozenset({"OTx"})
    expected = {
        "cellX": consensus_category(
            {"TgtA_human": 45, "TgtA_cyno": 45, "OTx": 10}, 0.6, offtargets=ot, label_crossreactive=True
        ),
        "cellY": consensus_category({"TgtA_human": 80, "OTx": 20}, 0.6, offtargets=ot, label_crossreactive=True),
        "cellZ": consensus_category(
            {"TgtA_human": 20, "TgtA_cyno": 20, "OTx": 60}, 0.6, offtargets=ot, label_crossreactive=True
        ),
        "cellW": consensus_category({"OTx": 7}, 0.6, offtargets=ot, label_crossreactive=True),
    }
    assert got == expected  # vectorized CLI == pure rule
    # ...and the pure rule is what we intend (guards against a vacuous match).
    assert expected == {
        "cellX": CROSS_REACTIVE,
        "cellY": "TgtA_human",
        "cellZ": "ambiguous",
        "cellW": "ambiguous",
    }


@pytest.mark.slow
def test_cli_offtarget_flags_require_each_other(tmp_path):
    # --offtarget-col without --offtarget-values (or vice versa) is a user error -> exit non-zero.
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,antigen_class\nAAAA,TgtA,Target\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncX\tAAAA\t3\t3\t3\n")
    r = subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--offtarget-col",
            "antigen_class",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        cwd=tmp_path,
    )
    assert r.returncode != 0


@pytest.mark.slow
def test_cli_control_not_scored_as_feature(tmp_path):
    # The control is the specificity reference, not a scored antigen: it must not appear as a feature in
    # the specificity output, and a control-heavy cell's maxSpecificityScore must be the real antigen's
    # score vs the control, never the control's self-score.
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature\nAAAA,AGX\nGGGG,CTRL\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellP\tAAAA\t3\t3\t3\n"
        "cellP\tGGGG\t9\t9\t9\n"  # control-heavy cell
    )
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--control",
            "CTRL",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_specificity.csv", newline="") as f:
        spec_features = {r["feature"] for r in csv.DictReader(f)}
    assert spec_features == {"AGX"}  # CTRL is not emitted as a scored feature
    with open(tmp_path / "result_per_cell_summary.csv", newline="") as f:
        summary = {r["cellId"]: r for r in csv.DictReader(f)}
    # AGX 3 UMIs vs CTRL 9 UMIs — the antigen's score, not specificity_score(9, 9) (the control self-score)
    assert float(summary["cellP"]["maxSpecificityScore"]) == pytest.approx(specificity_score(3, 9))


@pytest.mark.slow
def test_cli_combine_all_gates_dual_barcode_antigen(tmp_path):
    # End-to-end: a dual-barcode antigen (BG505 = b1 + b2) in "all" (AND) mode is called only in cells
    # where BOTH barcodes fired; a single-barcode antigen (OTHER = cx) stays OR. Mirrors the LIBRA-seq
    # dual-probe design (a cell is BG505-specific only when both probe barcodes are present).
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,combine\nb1,BG505,all\nb2,BG505,all\ncx,OTHER,sum\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellBoth\tb1\t6\t6\t6\n"
        "cellBoth\tb2\t6\t6\t6\n"  # both BG505 barcodes fire -> BG505 called (12), dominant
        "cellOne\tb1\t9\t9\t9\n"  # only b1 fired -> BG505 NOT called
        "cellOne\tcx\t1\t1\t1\n"  # OTHER present
    )
    subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--combine-col",
            "combine",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,
        cwd=tmp_path,
    )
    with open(tmp_path / "result_abundance.csv", newline="") as f:
        umi = {(r["cellId"], r["feature"]): int(r["umiCount"]) for r in csv.DictReader(f)}
    assert umi[("cellBoth", "BG505")] == 12  # AND: both fired -> summed
    assert ("cellOne", "BG505") not in umi  # AND: only one fired -> omitted entirely
    assert umi[("cellOne", "OTHER")] == 1
    # consensus follows: cellBoth is BG505; cellOne has only OTHER present -> OTHER
    with open(tmp_path / "result_consensus.csv", newline="") as f:
        cons = {r["cellId"]: r["consensusFeature"] for r in csv.DictReader(f)}
    assert cons["cellBoth"] == "BG505"
    assert cons["cellOne"] == "OTHER"


@pytest.mark.slow
def test_cli_rejects_conflicting_combine_mode(tmp_path):
    # A feature whose rows disagree on the combine mode is a user error -> exit non-zero, never a silent
    # pick of one mode.
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,combine\nb1,BG505,all\nb2,BG505,sum\n")  # BG505 rows disagree
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text("CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\ncA\tb1\t1\t1\t1\n")
    r = subprocess.run(
        [
            sys.executable,
            str(SRC),
            str(tagstat),
            str(tags),
            "--sample-id",
            "s1",
            "--combine-col",
            "combine",
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        cwd=tmp_path,
    )
    assert r.returncode != 0
