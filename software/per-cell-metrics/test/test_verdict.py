import polars as pl
from verdict import DEFAULT_FLOOR, apply_floor


def _counts(rows):
    return pl.DataFrame(
        rows, orient="row", schema={"sampleId": pl.String, "cellId": pl.String, "tag": pl.String, "umiCount": pl.Int64}
    )


def test_default_floor_is_four():
    assert DEFAULT_FLOOR == 4


def test_counts_below_the_floor_become_zero():
    df = _counts([("S1", "c1", "AAAA", 3), ("S1", "c1", "CCCC", 4)])
    out, stats = apply_floor(df, floor=4, reference_tags=set())
    assert out.sort("tag")["umiCount"].to_list() == [0, 4]
    assert stats["readingsFloored"] == 1


def test_floor_is_per_cell_and_tag_not_per_cell_total():
    df = _counts([("S1", "c1", "AAAA", 3), ("S1", "c1", "CCCC", 3)])
    out, stats = apply_floor(df, floor=4, reference_tags=set())
    assert out["umiCount"].to_list() == [0, 0]
    assert stats["readingsFloored"] == 2


def test_reference_tags_are_never_floored():
    df = _counts([("S1", "c1", "CTRL", 1), ("S1", "c1", "AAAA", 1)])
    out, _ = apply_floor(df, floor=4, reference_tags={"CTRL"})
    got = dict(zip(out["tag"].to_list(), out["umiCount"].to_list(), strict=True))
    assert got["CTRL"] == 1  # the comparator is not evidence of binding
    assert got["AAAA"] == 0


def test_cells_left_with_nothing_are_counted():
    df = _counts([("S1", "c1", "AAAA", 1), ("S1", "c2", "AAAA", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 1


def test_same_barcode_in_two_samples_stays_two_cells():
    df = _counts([("S1", "c1", "AAAA", 9), ("S2", "c1", "AAAA", 9)])
    out, _ = apply_floor(df, floor=4, reference_tags=set())
    assert out.height == 2


def test_floor_of_zero_removes_nothing():
    df = _counts([("S1", "c1", "AAAA", 1)])
    out, stats = apply_floor(df, floor=0, reference_tags=set())
    assert out["umiCount"].to_list() == [1] and stats["readingsFloored"] == 0


def test_count_exactly_at_the_floor_survives():
    df = _counts([("S1", "c1", "AAAA", 4)])
    out, _ = apply_floor(df, floor=4, reference_tags=set())
    assert out["umiCount"].to_list() == [4]


def test_a_cell_holding_only_the_reference_is_not_emptied():
    # Its non-reference readings are absent, not zeroed. "Emptied" means the
    # floor took a cell's evidence away, not that it never had any.
    df = _counts([("S1", "c1", "CTRL", 1)])
    _, stats = apply_floor(df, floor=4, reference_tags={"CTRL"})
    assert stats["cellsEmptied"] == 0


def test_a_cell_keeping_one_reading_is_not_emptied():
    df = _counts([("S1", "c1", "AAAA", 1), ("S1", "c1", "CCCC", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 0
    assert stats["readingsFloored"] == 1


def test_the_same_cell_id_in_two_samples_empties_independently():
    # (sampleId, cellId) is the key. Keying on cellId alone would let S2's
    # surviving reading rescue S1's emptied cell.
    df = _counts([("S1", "c1", "AAAA", 1), ("S2", "c1", "AAAA", 9)])
    _, stats = apply_floor(df, floor=4, reference_tags=set())
    assert stats["cellsEmptied"] == 1


def test_a_disabled_floor_is_a_no_op_even_for_a_zero_reading():
    # floor <= 0 returns early, and that early return is behavioural rather
    # than an optimisation: falling through would count a cell whose only
    # reading is already 0 as "emptied", when the floor removed nothing.
    df = _counts([("S1", "c1", "AAAA", 0)])
    out, stats = apply_floor(df, floor=0, reference_tags=set())
    assert out["umiCount"].to_list() == [0]
    assert stats == {"readingsFloored": 0, "cellsEmptied": 0}
