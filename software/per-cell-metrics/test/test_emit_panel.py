"""Behavioral tests for emit_panel.py (Feature Integration software).

Writes the panel's barcode column out as a plain one-per-line list for mitool's
refine-tags whitelist (`-t FEATURE#file:panel.txt`). Three properties of that
output are contract rather than convenience, and each has a test below:
deduplicated, sorted, and one barcode per line. mitool matches against this file
verbatim, and the workflow's pure-template dedup rests on the bytes being stable,
so an unsorted or duplicated file is not cosmetically wrong -- it changes a
resource handle and silently costs every downstream node its cache.

Run through the CLI like the other tool tests: this is a subprocess entry point,
and a caller reaching past it into `main()` would not exercise the argparse and
SystemExit behavior that is most of what the file does.
"""

import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).parents[1] / "src" / "emit_panel.py"


def _run(tmp_path, csv_text, *args, expect_failure=False):
    """Run the tool over `csv_text`, returning the output file's text.

    Asserts success by default: a tool that exits non-zero has written nothing,
    so a test that goes on to assert emptiness of the output would pass for the
    wrong reason.
    """
    src_csv = tmp_path / "tags.csv"
    src_csv.write_text(csv_text)
    out = tmp_path / "panel.txt"
    r = subprocess.run(
        [sys.executable, str(SRC), str(src_csv), str(out), *args],
        capture_output=True,
        text=True,
    )
    if expect_failure:
        assert r.returncode != 0, f"expected failure, got 0. stdout={r.stdout!r}"
        return r.stderr
    assert r.returncode == 0, f"exited {r.returncode}. stderr={r.stderr!r}"
    return out.read_text()


def test_dedupes_sorts_and_writes_one_barcode_per_line(tmp_path):
    # The whole contract in one assertion, on exact bytes: AAAA appears twice in
    # the input under two different antigen names and must appear once here, and
    # CCCC must follow it rather than lead. Asserting the set of lines instead
    # would pass on an unsorted file, which is the case that breaks dedup
    # downstream rather than anything a reader would notice.
    text = _run(tmp_path, "tag,feature\nCCCC,x\nAAAA,y\nAAAA,z\n")
    assert text == "AAAA\nCCCC\n"


def test_tag_col_selects_a_renamed_column(tmp_path):
    # The panel file's barcode column is whatever the user pointed the block at,
    # so the default name is a default and not an assumption.
    text = _run(tmp_path, "Sequence,Name\nGGGG,x\nTTTT,y\n", "--tag-col", "Sequence")
    assert text == "GGGG\nTTTT\n"


def test_missing_column_names_the_column_it_wanted(tmp_path):
    stderr = _run(tmp_path, "sequence,feature\nAAAA,x\n", expect_failure=True)
    assert "tag" in stderr


def test_header_only_input_is_refused(tmp_path):
    # An empty whitelist is not an empty correction -- mitool given a whitelist
    # of nothing corrects every barcode to nothing. Failing here is the point.
    stderr = _run(tmp_path, "tag,feature\n", expect_failure=True)
    assert "no feature barcodes" in stderr


def test_all_blank_tag_cells_are_refused(tmp_path):
    stderr = _run(tmp_path, "tag,feature\n ,x\n,y\n", expect_failure=True)
    assert "no feature barcodes" in stderr


def test_padded_cells_are_trimmed_and_blank_cells_skipped(tmp_path):
    # The barcode is a join key against the counts, whose reader strips for the
    # same reason: " AAAA " and "AAAA" are one barcode, and a whitelist carrying
    # the padded form matches nothing.
    text = _run(tmp_path, "tag,feature\n  AAAA  ,x\n,y\nCCCC,z\n")
    assert text == "AAAA\nCCCC\n"


def test_a_ragged_short_row_is_skipped_rather_than_crashing(tmp_path):
    # csv.DictReader fills a short row's missing keys with None, so the tag cell
    # is present-and-None rather than absent. `row.get(col, "")` returns None
    # there -- the default never fires, because the key exists -- and .strip()
    # raised AttributeError, taking the whole run down on one malformed line of a
    # user-supplied CSV.
    #
    # The barcode column is deliberately SECOND here. A short row only truncates
    # the trailing columns, so with the barcode first the missing key is the other
    # column and nothing reads it -- the first version of this test put it first,
    # passed against the unfixed code, and proved nothing.
    text = _run(tmp_path, "feature,tag\nx,AAAA\ny\nz,GGGG\n", "--tag-col", "tag")
    assert text == "AAAA\nGGGG\n"
