"""Shared fixtures for the Feature Integration software tests.

A conftest.py at the software root is auto-applied to every test below it, and fixtures are consumed by
name rather than via ``import conftest``. The committed test bed lives at
software/test-data/fixtures/per-cell-metrics/: a small mitool tag-stat TSV plus a tag->feature CSV.
"""

import os
import pathlib
import sys

import pytest

SOFTWARE_ROOT = pathlib.Path(__file__).resolve().parent

# Keep the bytecode cache out of every package's `src/`.
_PYCACHE = SOFTWARE_ROOT / ".pycache"
sys.pycache_prefix = str(_PYCACHE)
os.environ["PYTHONPYCACHEPREFIX"] = str(_PYCACHE)

BED = SOFTWARE_ROOT / "test-data" / "fixtures" / "per-cell-metrics"


@pytest.fixture(scope="session")
def tagstat_tsv():
    p = BED / "tagstat_main.tsv"
    if not p.exists():
        pytest.fail(
            f"committed test bed missing at {p}; restore software/test-data/fixtures/per-cell-metrics/",
            pytrace=False,
        )
    return p


@pytest.fixture(scope="session")
def tags_csv():
    return BED / "tags.csv"
