"""Panel-metadata tests: the generated tags.csv must carry the real customer panel's per-antigen
Type / Species / Class columns (alongside the backward-compatible tag,feature role mapping), the
--offtarget-count flag must designate exactly N non-control antigens as Off-Target, and out-of-range
counts must be rejected on BOTH the full-run and the --beam paths."""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent  # software/test-data/manual

# The gex arm annotates against a human gene-annotations table that is downloaded, not committed, so a
# full multiomic run cannot be built in a clean checkout. Tests needing only the antigen arm use the
# scenario path instead and are unaffected; the ones that genuinely need a full run say so rather than
# failing with a bare subprocess error that names no cause.
GENE_ANNOTATIONS = HERE / "assets" / "homo_sapiens_gene_annotations.csv"
needs_full_run = pytest.mark.skipif(
    not GENE_ANNOTATIONS.exists(),
    reason=(
        f"missing {GENE_ANNOTATIONS.name}; fetch it with:\n"
        "  curl -sSL -o /tmp/hs.zip https://bin.pl-open.science/assets/platforma-open/"
        "milaboratories.gene-annotations.homo-sapiens/main/1.1.0.zip"
        f" && unzip -o /tmp/hs.zip -d {GENE_ANNOTATIONS.parent}"
    ),
)


def _run(*args, out):
    subprocess.run(
        [sys.executable, str(HERE / "generate.py"), *args, "--out", str(out), "--no-validate"],
        check=True,
    )


def _read_csv(path):
    with path.open() as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames  # header-only files don't IndexError
        rows = list(reader)
    return header, rows


def test_panel_has_type_species_class(tmp_path):
    # tags.csv comes from the antigen arm, so the self-contained scenario bed suffices — no full run,
    # no gene-annotations asset.
    _run("--scenario", "errors", "--offtarget-count", "2", out=tmp_path)
    header, rows = _read_csv(tmp_path / "tags.csv")
    assert {"Type", "Species", "Class"} <= set(header)
    types = {r["Type"] for r in rows}
    assert "Off-Target" in types and "Target" in types
    assert len([r for r in rows if r["Type"] == "Off-Target"]) == 2
    assert {"Human", "Cyno"} <= {r["Species"] for r in rows}


@needs_full_run
def test_multibarcode_combine_column(tmp_path):
    _run("tiny", "--multibarcode", out=tmp_path)
    with (tmp_path / "tags.csv").open() as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    assert "combine" in header
    from collections import Counter

    feat_counts = Counter(r["feature"] for r in rows)
    assert any(c >= 2 for c in feat_counts.values())  # one antigen on 2+ barcodes
    assert {"all", "sum"} <= {r["combine"] for r in rows}


@needs_full_run
def test_messy_metadata_variants(tmp_path):
    _run("tiny", "--offtarget-count", "3", "--messy-metadata", out=tmp_path)
    with (tmp_path / "tags.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    types = {r["Type"] for r in rows}
    assert "Off-Target" in types and "Off-target" in types
    assert any("  " in r["feature"] for r in rows)  # stray double space


def test_beam_panel_has_type_species(tmp_path):
    # beam-exact: 2 samples x panel_size antigens; --offtarget-count applies per sample.
    _run("--beam", "--offtarget-count", "2", "--cells-per-sample", "10", out=tmp_path)
    header, rows = _read_csv(tmp_path / "tags.csv")
    assert {"Type", "Species"} <= set(header)
    assert len([r for r in rows if r["Type"] == "Off-Target"]) == 4  # 2 samples x 2 off-target
    assert "Target" in {r["Type"] for r in rows}


def _read_tsv(path):
    with path.open() as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _top_two_ratio(counts):
    """Second-largest count over the largest, or None where the cell has fewer than two features."""
    ranked = sorted(counts, reverse=True)
    return None if len(ranked) < 2 else ranked[1] / ranked[0]


def test_generator_plants_crossreactive(tmp_path):
    """A planted cross-reactive cell carries a co-dominant pair of two ON-TARGET antigens.

    This asserts the generator against its own output and reads nothing from the block. It used to
    check the block's `consensus_category` instead, which no longer exists: a single dominant antigen
    per cell answers a different question from the four-state verdict and was removed with it. What
    the bed still owes is the planted shape, because the gex and vdj arms are both built from this
    truth file.
    """
    # The antigen-only scenario bed rather than a full `tiny` run: a full run also builds the gex arm,
    # which needs a gene-annotations asset that is downloaded rather than committed, so a full run
    # cannot be generated in a clean checkout. This test needs the antigen arm alone. The scenario bed
    # is self-contained and writes its truth files flat in the output directory.
    _run("--scenario", "errors", "--offtarget-count", "1", "--crossreactive-frac", "0.1", out=tmp_path)

    consensus = _read_tsv(tmp_path / "expected-consensus.tsv")
    crossreactive = {(r["sample"], r["cellId"]) for r in consensus if r["planted_consensus"] == "crossreactive"}
    assert crossreactive, "no cross-reactive cell was planted at --crossreactive-frac 0.1"

    _, panel_rows = _read_csv(tmp_path / "tags.csv")
    on_target = {r["feature"] for r in panel_rows if r["Type"] == "Target"}

    counts_by_cell = {}
    for r in _read_tsv(tmp_path / "expected-abundance.tsv"):
        counts_by_cell.setdefault((r["sample"], r["cellId"]), {})[r["feature"]] = int(r["planted_distinct_umis"])

    # The pair is planted as second = first * U(0.85, 1.0), then truncated to an int, so 0.84 is the
    # floor a correctly planted cell cannot fall below.
    for key in crossreactive:
        counts = counts_by_cell[key]
        ratio = _top_two_ratio(counts.values())
        assert ratio is not None and ratio >= 0.84, f"{key} is labelled cross-reactive but its top two counts are {ratio}"
        top_two = sorted(counts, key=counts.get, reverse=True)[:2]
        assert set(top_two) <= on_target, f"{key}'s co-dominant pair includes a non-Target antigen: {top_two}"

    # The check above is only worth running if it can fail, and an evenness test passes vacuously on a
    # bed where every cell is even. An ordinary binder plants one dominant antigen over background, so
    # some non-cross-reactive cell must be visibly UNEVEN — otherwise the assertion above proves
    # nothing about the label.
    uneven = [
        key
        for key, counts in counts_by_cell.items()
        if key not in crossreactive and (_top_two_ratio(counts.values()) or 0) < 0.5
    ]
    assert uneven, "every cell in the bed is co-dominant, so the cross-reactive assertion cannot discriminate"


@needs_full_run
def test_heavy_only_airr(tmp_path):
    _run("tiny", "--heavy-only", out=tmp_path)
    tsvs = list((tmp_path / "vdj").glob("*.tsv"))
    assert tsvs
    loci = set()
    for t in tsvs:
        with t.open() as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                loci.add(r["locus"])
    assert loci == {"IGH"}


@needs_full_run
def test_annotation_emitter(tmp_path):
    _run("tiny", "--with-annotations", out=tmp_path)
    tsvs = list((tmp_path / "annotations").glob("*.tsv"))
    assert tsvs
    with tsvs[0].open() as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        header = reader.fieldnames
        rows = list(reader)
    assert {"cell_id", "cell_type", "cluster"} <= set(header)
    with (tmp_path / "truth" / "expected-consensus.tsv").open() as fh:
        cons = list(csv.DictReader(fh, delimiter="\t"))
    ann_ids = {r["cell_id"] for r in rows}
    # expected-consensus.tsv keys the barcode as `cellId` (antigen arm); annotations use `cell_id`.
    # Accept either so the join-compatibility check is meaningful against the real truth schema.
    cons_ids = {r.get("cell_id") or r.get("cellId") for r in cons}
    assert ann_ids & cons_ids


def test_offtarget_count_out_of_range_errors(tmp_path):
    # The full-run AND the --beam paths must both reject an offtarget count above the panel size.
    for i, extra in enumerate((["tiny"], ["--beam", "--panel-size", "4", "--cells-per-sample", "10"])):
        out = tmp_path / f"run{i}"
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "generate.py"),
                *extra,
                "--offtarget-count",
                "99",
                "--out",
                str(out),
                "--no-validate",
            ],
            capture_output=True,
            text=True,
        )
        # Asserting only a nonzero exit would pass for any failure at all — a missing asset, a syntax
        # error, a bad path — so it must name the rejection it is checking for.
        assert result.returncode != 0, f"expected nonzero exit for {extra}, got 0"
        assert "--offtarget-count must be between" in (result.stderr + result.stdout), (
            f"exited nonzero for {extra}, but not because the count was out of range:\n{result.stderr}"
        )
