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
from per_cell_metrics import consensus_category, specificity_score

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
