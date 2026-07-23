"""Panel-metadata tests: the generated tags.csv must carry the real customer panel's per-antigen
Type / Species / Class columns (alongside the backward-compatible tag,feature role mapping), the
--offtarget-count flag must designate exactly N non-control antigens as Off-Target, and out-of-range
counts must be rejected on BOTH the full-run and the --beam paths."""

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # software/test-data/manual


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
    _run("tiny", "--offtarget-count", "2", out=tmp_path)
    header, rows = _read_csv(tmp_path / "tags.csv")
    assert {"Type", "Species", "Class"} <= set(header)
    types = {r["Type"] for r in rows}
    assert "Off-Target" in types and "Target" in types
    assert len([r for r in rows if r["Type"] == "Off-Target"]) == 2
    assert {"Human", "Cyno"} <= {r["Species"] for r in rows}


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


def _load_consensus():
    p = HERE.parent.parent / "per-cell-metrics" / "src" / "per_cell_metrics.py"
    spec = importlib.util.spec_from_file_location("pcm", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_crossreactive_two_even_antigens():
    pcm = _load_consensus()
    counts = {"AgA": 50.0, "AgB": 48.0, "ctrl": 3.0}
    result = pcm.consensus_category(
        counts, threshold=0.6, control="ctrl", offtargets=frozenset(), label_crossreactive=True
    )
    assert result == "Target cross-reactive"


def test_generator_plants_crossreactive(tmp_path):
    _run("tiny", "--offtarget-count", "1", "--crossreactive-frac", "0.1", out=tmp_path)
    consensus = list(csv.DictReader((tmp_path / "truth" / "expected-consensus.tsv").open(), delimiter="\t"))
    assert any(r.get("planted_consensus") == "crossreactive" for r in consensus)


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
        assert result.returncode != 0, f"expected nonzero exit for {extra}, got 0"
