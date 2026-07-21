"""Panel-metadata tests: the generated tags.csv must carry the real customer panel's per-antigen
Type / Species / Class columns (alongside the backward-compatible tag,feature role mapping), the
--offtarget-count flag must designate exactly N non-control antigens as Off-Target, and out-of-range
counts must be rejected on BOTH the full-run and the --beam paths."""

import csv
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


def test_beam_panel_has_type_species(tmp_path):
    # beam-exact: 2 samples x panel_size antigens; --offtarget-count applies per sample.
    _run("--beam", "--offtarget-count", "2", "--cells-per-sample", "10", out=tmp_path)
    header, rows = _read_csv(tmp_path / "tags.csv")
    assert {"Type", "Species"} <= set(header)
    assert len([r for r in rows if r["Type"] == "Off-Target"]) == 4  # 2 samples x 2 off-target
    assert "Target" in {r["Type"] for r in rows}


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
