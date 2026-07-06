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
    for r in rows:
        expected = specificity_score(umi[(r["cellId"], r["feature"])], control_umi[r["cellId"]])
        assert float(r["specificityScore"]) == pytest.approx(float(expected))
