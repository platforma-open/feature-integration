"""Shared fixtures for the Feature Integration software tests.

A conftest.py at the software root is auto-applied to every test below it (fixtures are consumed by
name, never via ``import conftest``). The committed test bed lives at
software/test-data/fixtures/per-cell-metrics/: a small mitool tag-stat TSV plus a tag->feature CSV.
"""

import pathlib

import pytest

BED = pathlib.Path(__file__).resolve().parent / "test-data" / "fixtures" / "per-cell-metrics"


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
