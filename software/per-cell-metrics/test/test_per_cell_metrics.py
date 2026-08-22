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

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st
from per_cell_metrics import (
    _load,
    combine_barcode_counts,
    per_cell_summary,
    with_fraction,
)

SRC = pathlib.Path(__file__).parents[1] / "src" / "per_cell_metrics.py"


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
    # Only one BG505 barcode fired, so under AND the antigen is NOT called. The cell has no BG505
    # entry at all -- omitted, not zero -- so it never takes a fraction of that cell's signal.
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
    for name in ["result_abundance.csv", "result_fractions.csv", "result_per_cell_summary.csv"]:
        assert (tmp_path / name).exists(), f"missing {name}"


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
    # (AAAA, CCCC) match the committed tagstat_main.tsv bed. Mapped to the same AGX/BGX names the
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
    # or a sample with no on-panel reads -- the run must still emit both CSVs header-only, never crash.
    # abundance and fractions are pure-polars transforms that carry their schema through the empty
    # case, and this guards that they stay header-only.
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
            "--output-prefix",
            str(tmp_path / "result"),
        ],
        check=True,  # the old crash exited non-zero -> this would fail here
        cwd=tmp_path,
    )
    for name, header in [
        ("result_abundance.csv", ["sampleId", "cellId", "feature", "umiCount"]),
        ("result_fractions.csv", ["sampleId", "cellId", "feature", "fraction"]),
    ]:
        p = tmp_path / name
        assert p.exists(), f"missing {name}"
        with open(p, newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == header  # schema/header preserved
            assert list(reader) == []  # zero data rows (empty result)


@pytest.mark.slow
def test_cli_per_cell_summary_maxima_match_exported_columns(tagstat_tsv, tags_csv, tmp_path):
    # The per-cell summary's maxUmiCount and maxFraction are a collapse of the exported
    # (cell x feature) columns. They must equal the per-cell max of those exported CSVs, not a
    # separately-recomputed value. This guards the with_fraction single-compute reuse.
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

    def _max_by_cell(path, value_col, cast):
        by_cell: dict[str, float] = {}
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                v = cast(r[value_col])
                by_cell[r["cellId"]] = max(by_cell.get(r["cellId"], v), v)
        return by_cell

    exp_umi = _max_by_cell(tmp_path / "result_abundance.csv", "umiCount", int)
    exp_frac = _max_by_cell(tmp_path / "result_fractions.csv", "fraction", float)

    with open(tmp_path / "result_per_cell_summary.csv", newline="") as f:
        summary = {r["cellId"]: r for r in csv.DictReader(f)}

    assert set(summary) == set(exp_umi)
    for cell, row in summary.items():
        assert int(row["maxUmiCount"]) == exp_umi[cell]
        assert float(row["maxFraction"]) == pytest.approx(exp_frac[cell])


@pytest.mark.slow
def test_cli_combine_all_gates_dual_barcode_antigen(tmp_path):
    # End-to-end: a dual-barcode antigen (BG505 = b1 + b2) in "all" (AND) mode is called only in cells
    # where BOTH barcodes fired. A single-barcode antigen (OTHER = cx) stays OR. Mirrors the LIBRA-seq
    # dual-probe design (a cell is BG505-specific only when both probe barcodes are present).
    tags = tmp_path / "tags.csv"
    tags.write_text("tag,feature,combine\nb1,BG505,all\nb2,BG505,all\ncx,OTHER,sum\n")
    tagstat = tmp_path / "tagstat.tsv"
    tagstat.write_text(
        "CELL\tFEATURE\tcount\ttotalWeight\tunique_UMI\n"
        "cellBoth\tb1\t6\t6\t6\n"
        "cellBoth\tb2\t6\t6\t6\n"  # both BG505 barcodes fire -> BG505 called (12)
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


def test_a_cell_whose_every_count_is_zero_gets_a_zero_share_not_a_nan():
    # 0/0 is NaN, and the NaN does not stay put. It reaches the exported fractions CSV as a float
    # nothing downstream expects, and in per_cell_summary it slips past the "<1%" guard, which requires
    # umiCount > 0, into a cast to Int64 that raises and takes the whole CLI down with a raw traceback.
    # Real tag-stat cannot emit such a row, but this CLI is driven by hand during verification.
    frame = pl.DataFrame(
        {
            "sampleId": ["S1", "S1"],
            "cellId": ["c1", "c1"],
            "feature": ["A", "B"],
            "umiCount": [0, 0],
        }
    )
    fractions = with_fraction(frame)
    assert fractions["fraction"].to_list() == [0.0, 0.0]

    # And the collapse runs rather than dying. A cell with no signal still gets its row: dropping it
    # would make a cell that was measured and read nothing indistinguishable from one never measured.
    summary = per_cell_summary(fractions)
    assert summary.height == 1
    assert summary["maxUmiCount"].to_list() == [0]
    assert summary["maxFraction"].to_list() == [0.0]


def test_a_cell_with_signal_is_unaffected_by_the_zero_total_guard():
    # The guard must not change the ordinary case -- a `when/otherwise` around a division is exactly the
    # shape that quietly zeroes a whole column if the predicate is wrong.
    frame = pl.DataFrame(
        {
            "sampleId": ["S1", "S1"],
            "cellId": ["c1", "c1"],
            "feature": ["A", "B"],
            "umiCount": [3, 1],
        }
    )
    assert with_fraction(frame)["fraction"].to_list() == [0.75, 0.25]
