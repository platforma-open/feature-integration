import dataclasses
import re

import polars as pl
from qc_measures import (
    _COMPARISON,
    DEFAULT_LINES,
    LINE_ROUTES,
    MEASUREMENTS,
    Coverage,
    Line,
    Measurement,
    Reading,
    Status,
    antigen_count_deciles,
    measurement_rows,
    per_antigen_measures,
    reads_per_cell,
    roll_up,
    status_for,
)

# Every row maps one to one. This is the expected per-id level, built from the
# spec's own table rather than copied from this module, so a level typo on any id
# changes the multiset below.
EXPECTED_LEVEL_BY_ID = {
    "readsTotal": "sample",
    "panelAssignedFraction": "sample",
    "cellBarcodeValidFraction": "sample",
    "readsPerCell": "sample",
    "antigenCountDistribution": "sample",
    "aggregateBarcodeFraction": "sample",
    "undeclaredBarcodes": "tag",
    "declaredNeverSeen": "tag",
    "floorRemoved": "sample",
    "uniqueCountsPerCell": "sample",
    "highReferenceCells": "sample",
    "perAntigen": "tag",
    "fittedBackground": "tag",
    "scoreDistribution": "run",
    "tagDisagreement": "tag",
}

DEFERRED_IDS = {"aggregateBarcodeFraction"}


def test_every_declared_id_is_expected_and_every_expected_id_is_declared():
    assert {m.id for m in MEASUREMENTS} == set(EXPECTED_LEVEL_BY_ID)


def test_a_comparison_is_not_a_line():
    # A comparison against siblings yields no boundary: nothing separates OK from alerting, so nothing
    # can be computed. A status derived from it would need a multiplier -- a
    # median-absolute-deviation cut, an interquartile multiple -- that nobody has published for this
    # measurement. The invention would move up a level rather than disappear.
    assert LINE_ROUTES == {"inherited", "categorical", "recommended-and-observed"}
    assert {m.line for m in MEASUREMENTS if m.line} <= LINE_ROUTES


def test_tag_disagreement_reads_unjudged():
    # Such measurements read unjudged and are shown where the comparison is free
    # to make: a column beside its siblings. The value still travels.
    assert status_for("tagDisagreement", 0.24, DEFAULT_LINES) is None


def test_no_measurement_is_refused_by_status_for():
    # The against-the-run route was the only case `status_for` raised on. With it
    # gone, every declared measurement gets an answer rather than an exception. None is
    # an answer: it says no line stands behind this one.
    for m in MEASUREMENTS:
        answer = status_for(m.id, 0.5, DEFAULT_LINES)
        assert answer is None or isinstance(answer, Status), m.id


def test_self_disagreement_is_measured_at_the_tag_and_nowhere_else():
    # The identity-level figure has nothing to compare against, so it cannot separate a faulty reagent
    # from a panel full of weak binders. It measures how many clonotypes sit near the line, which is a
    # fact about the panel rather than a fault to fix. The tag-level figure is read against the other
    # tags in the same panel, under the same cells and the same line.
    ids = {m.id for m in MEASUREMENTS}
    assert "tagDisagreement" in ids
    assert "identityDisagreement" not in ids


def test_saturation_and_known_answer_are_not_measured():
    # Both are stated exclusions rather than gaps. Saturation is a number nobody
    # can act on for the run already collected, and depth is answered by reads
    # per cell against a stated recommendation. The known-answer check needs a
    # declaration no surface asks for, so building it means building that first.
    ids = {m.id for m in MEASUREMENTS}
    assert "sequencingSaturation" not in ids
    assert "knownAnswerRecovered" not in ids
    assert "readsPerCell" in ids, "the depth question is answered here instead"


def test_declared_levels_match_the_spec_as_a_multiset():
    # A multiset comparison, not a per-id comparison: swapping any one
    # measurement's level changes how many times that level appears overall,
    # so a typo trips this even without knowing which id was mistyped.
    declared = sorted(m.level for m in MEASUREMENTS)
    expected = sorted(EXPECTED_LEVEL_BY_ID.values())
    assert declared == expected


def test_every_measurement_declares_a_known_level():
    # `run` is a grain of one. 320 puts the score spread there because the cutoff is one number
    # for the run, so a per-sample figure would answer a question nobody asked.
    assert {m.level for m in MEASUREMENTS} <= {"sample", "tag", "identity", "run"}


def test_every_measurement_says_what_it_counts():
    assert all(m.counts for m in MEASUREMENTS)


def test_measurement_has_no_produced_today_field():
    # produced_today would answer whether the superseded tool produced this
    # measurement, which reads backwards from what a reader of this block
    # needs: deferred_reason is None already answers whether THIS build does.
    field_names = {f.name for f in dataclasses.fields(Measurement)}
    assert "produced_today" not in field_names


def test_an_unjudged_measurement_says_nothing_about_a_bad_value():
    for m in MEASUREMENTS:
        if m.line is None:
            assert m.implies is None, m.id


BANNED_ADVICE_PHRASES = (
    "should",
    "must",
    "need to",
    "needs to",
    "ought",
    "advise",
    "advice",
    "we suggest",
    "try ",
    "consider ",
    "re-run",
    "rerun",
    "replace",
    "avoid",
    "ensure",
    "make sure",
    "flag for",
    "recommend that",
    "recommend you",
)

# A sentence opening with one of these reads as an instruction regardless of what follows. Compare
# "Replace the reagent." against "A reagent that produced nothing did not work". So this catches advice
# phrased as an imperative, which the substring list above does not: none of these words are banned
# outright, and several appear as ordinary nouns or adjectives elsewhere in the set, such as "the
# vendor's recommended minimum".
IMPERATIVE_OPENERS = {
    "check",
    "verify",
    "replace",
    "remove",
    "increase",
    "decrease",
    "use",
    "try",
    "consider",
    "avoid",
    "ensure",
    "fix",
    "rerun",
    "lower",
    "raise",
    "discard",
    "exclude",
    "flag",
    "recheck",
    "investigate",
    "review",
}


def test_no_measurement_carries_advice():
    for m in MEASUREMENTS:
        text = f"{m.counts} {m.implies or ''}"
        lowered = text.lower()
        # Word boundaries, not bare substrings. "try " matched inside "chemistry does", which
        # would have rejected honest prose -- "industry", "poultry", "symmetry" all carry it.
        assert not any(re.search(rf"\b{re.escape(phrase.strip())}\b", lowered) for phrase in BANNED_ADVICE_PHRASES), (
            m.id
        )

        for sentence in text.split("."):
            first_word = sentence.strip().split(" ", 1)[0].strip(",:;").lower()
            assert first_word not in IMPERATIVE_OPENERS, (m.id, sentence)


def test_deferred_measurements_are_declared_not_omitted():
    deferred = {m.id for m in MEASUREMENTS if m.deferred_reason}
    assert deferred == DEFERRED_IDS


def test_deferred_measurement_reasons_are_stated():
    for m in MEASUREMENTS:
        if m.id in DEFERRED_IDS:
            assert m.deferred_reason, m.id
            assert m.implies is None, m.id


def test_deferred_measurement_produces_a_row_with_its_reason_and_no_status():
    rows = measurement_rows()
    # Never absent: every declared id, deferred or not, has a row.
    assert {r["id"] for r in rows} == {m.id for m in MEASUREMENTS}

    by_id = {r["id"]: r for r in rows}
    for deferred_id in DEFERRED_IDS:
        row = by_id[deferred_id]
        # A declaration is not a reading, so no row here carries a status. The reason is what
        # tells a reader nothing computed this one.
        assert row["status"] is None
        assert row["reason"]


def test_a_computed_measurement_carries_no_status():
    rows = measurement_rows()
    by_id = {r["id"]: r for r in rows}
    for m in MEASUREMENTS:
        if m.id not in DEFERRED_IDS:
            assert by_id[m.id]["status"] is None, m.id
            assert by_id[m.id]["reason"] is None, m.id


# --- per_antigen_measures: tag grain -----------------------------------------


def _counts(tag: list[str], umi: list[int], sample: list[str] | None = None) -> pl.DataFrame:
    return pl.DataFrame({"sampleId": sample or ["S1"] * len(tag), "tag": tag, "umiCount": umi})


def _states(tag: list[str], state: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"tag": tag, "state": state})


def test_per_antigen_measures_separates_delivered_from_bound():
    # The reagent 330-the-quality-readout names: two counts into every cell. Counted after the
    # minimum it delivered nothing, which is the reading that must not come back. Counted before
    # it, it delivered into every cell and none of them cleared the line.
    counts = _counts(["T1"] * 3, [2, 2, 2])
    states = _states(["T1"] * 3, ["not bound"] * 3)

    out = per_antigen_measures(counts, states, ["T1"]).row(0, named=True)

    assert out["cellsWithCount"] == 3
    assert out["cellsAboveTheLine"] == 0
    # Below the minimum on purpose. The atom calls that the finding rather than an error.
    assert out["medianCountPerCell"] == 2.0


def test_per_antigen_measures_medians_over_every_cell_holding_a_count():
    # The regression this guards. Over the bound cells alone the median is 50, which is a healthy
    # figure for a half-degraded reagent, computed from the two cells that scraped over. Over every
    # cell holding a count it is 22, which moves with the reagent.
    counts = _counts(["T1"] * 4, [2, 4, 40, 60])
    states = _states(["T1"] * 4, ["not bound", "not bound", "bound", "bound"])

    out = per_antigen_measures(counts, states, ["T1"]).row(0, named=True)

    assert out["medianCountPerCell"] == 22.0
    assert out["medianCountPerCell"] != 50.0
    assert out["cellsWithCount"] == 4
    assert out["cellsAboveTheLine"] == 2


def test_per_antigen_measures_keeps_a_row_for_a_tag_the_reads_never_show():
    # A dead reagent reads as a zero under cells-with-count. Grouping the observed frame gave it no
    # row at all, and a row that is not there cannot be scanned against its neighbours.
    counts = _counts(["T1", "T1"], [10, 20])
    states = _states(["T1", "T1"], ["bound", "bound"])

    out = per_antigen_measures(counts, states, ["T1", "DEAD"])
    dead = out.filter(pl.col("tag") == "DEAD").row(0, named=True)

    assert out.height == 2
    assert dead["cellsWithCount"] == 0
    assert dead["cellsAboveTheLine"] == 0
    # No counts at all, so no median exists. Distinct from a median that computed to zero.
    assert dead["medianCountPerCell"] is None


def test_per_antigen_measures_gives_the_reference_tag_no_bound_count():
    # The reference is held out of the verdict read, so no cell was ever asked about it. A zero
    # here would read as "asked and never bound", which is the opposite finding. Its median stays,
    # because that is the run's ambient floor and the reason it belongs in this table.
    counts = _counts(["T1", "T1", "REF", "REF"], [10, 20, 3, 5])
    states = _states(["T1", "T1"], ["bound", "bound"])

    out = per_antigen_measures(counts, states, ["T1"], reference_tags=["REF"])
    ref = out.filter(pl.col("tag") == "REF").row(0, named=True)

    assert ref["cellsAboveTheLine"] is None
    assert ref["cellsWithCount"] == 2
    assert ref["medianCountPerCell"] == 4.0


def test_per_antigen_measures_differs_between_tag_and_identity_grain():
    # T1 and T2 both feed one identity. As tags, T1 shows one weak cell (one
    # bound of two). As the combined identity, the same cells collapse to one
    # row and T1's weak showing is no longer visible on its own.
    tag_counts = _counts(["T1", "T1", "T2", "T2"], [8, 1, 20, 15])
    tag_states = _states(["T1", "T1", "T2", "T2"], ["bound", "not bound", "bound", "bound"])
    # The identity each cell's highest tag reading combined into.
    identity_counts = _counts(["ID1"] * 3, [20, 1, 15])
    identity_states = _states(["ID1"] * 3, ["bound", "not bound", "bound"])

    by_tag = per_antigen_measures(tag_counts, tag_states, ["T1", "T2"])
    by_identity = per_antigen_measures(identity_counts, identity_states, ["ID1"])

    assert by_tag.height == 2
    assert by_identity.height == 1
    assert dict(zip(by_tag["tag"], by_tag["cellsAboveTheLine"], strict=True)) == {"T1": 1, "T2": 2}
    assert by_identity.row(0, named=True)["cellsAboveTheLine"] == 2


# --- reads_per_cell --------------------------------------------------------


def test_reads_per_cell_computes_the_rate():
    assert reads_per_cell(1000, 200) == 5.0


def test_reads_per_cell_empty_cell_list_does_not_divide_by_zero():
    assert reads_per_cell(1000, 0) is None


# --- antigen_count_deciles -----------------------------------------------------


def _cell_counts(totals: dict[str, int], sample_id: str = "S1") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "sampleId": [sample_id] * len(totals),
            "cellId": list(totals.keys()),
            "umiCount": list(totals.values()),
        },
        schema={"sampleId": pl.String, "cellId": pl.String, "umiCount": pl.Int64},
    )


def test_antigen_count_deciles_on_a_known_distribution():
    # 11 cells with totals 0, 10, ..., 100. With linear interpolation over 11 sorted points, the p-th
    # percentile lands exactly on index p/10, so every decile equals its own cell's total. That is a
    # fixture an off-by-one position error cannot pass unnoticed on.
    counts = _cell_counts({f"c{i}": i * 10 for i in range(11)})
    out = antigen_count_deciles(counts)
    assert out["decile"].to_list() == list(range(0, 101, 10))
    assert out["value"].to_list() == [float(i * 10) for i in range(11)]


def test_antigen_count_deciles_single_cell_sample():
    counts = _cell_counts({"c0": 42})
    out = antigen_count_deciles(counts)
    assert out.height == 11
    assert all(v == 42.0 for v in out["value"].to_list())


def test_antigen_count_deciles_empty_sample():
    counts = _cell_counts({})
    out = antigen_count_deciles(counts)
    assert out.height == 11
    assert out["decile"].to_list() == list(range(0, 101, 10))
    assert all(v is None for v in out["value"].to_list())


def test_three_statuses_and_no_fourth():
    # Atom 310 refuses a fourth and a fifth: a reader meeting five words in one column reads
    # them as a scale. The two cases a fourth word covered are read from the value instead.
    assert {s.value for s in Status} == {"OK", "warn", "alert"}


def test_a_measurement_with_no_line_carries_no_status_rather_than_a_fourth_word():
    assert status_for("antigenCountDistribution", 12, DEFAULT_LINES) is None
    assert status_for("readsPerCell", None, DEFAULT_LINES) is None


# --- the route is the single authority -------------------------------------
# Both directions, so neither table can grow an entry the other does not know
# about. This is what stops `DEFAULT_LINES` becoming a second declaration of
# which measurements carry a line.


def test_every_declared_route_is_one_of_the_three():
    assert {m.line for m in MEASUREMENTS if m.line} <= LINE_ROUTES


def test_a_measurement_with_a_route_has_a_line_and_a_comparison_and_nothing_else_does():
    # Every surviving route puts an absolute number on the measurement, so the
    # route set and the line set are the same set. A comparison is not a line and
    # so declares no route at all.
    routed = {m.id for m in MEASUREMENTS if m.line}
    assert set(DEFAULT_LINES) == routed
    assert set(_COMPARISON) == routed


def test_the_undeclared_barcode_line_transfers_as_its_complement():
    # 315 publishes this line on the UNDECLARED share: warn above 0.50, error at 1.0. The block
    # measures the assigned share, which is one minus that, so both thresholds mirror. A run
    # where every read carries an undeclared barcode assigns nothing and alerts.
    line = DEFAULT_LINES["panelAssignedFraction"]
    assert (line.warn, line.error) == (1 - 0.50, 1 - 1.0)
    assert status_for("panelAssignedFraction", 1 - 0.60, DEFAULT_LINES) is Status.WARN
    assert status_for("panelAssignedFraction", 1 - 1.0, DEFAULT_LINES) is Status.ALERT


def test_barcode_validity_is_the_line_with_a_gradient_at_both_ends():
    # The one inherited line whose thresholds step the same way twice, and the reason 310 admits
    # a third status level at all. The other three put error at total failure.
    line = DEFAULT_LINES["cellBarcodeValidFraction"]
    assert (line.warn, line.error) == (0.75, 0.50)
    assert _COMPARISON["cellBarcodeValidFraction"] == ("at-least", "at-least")
    assert status_for("cellBarcodeValidFraction", 0.80, DEFAULT_LINES) is Status.OK
    assert status_for("cellBarcodeValidFraction", 0.75, DEFAULT_LINES) is Status.OK
    assert status_for("cellBarcodeValidFraction", 0.60, DEFAULT_LINES) is Status.WARN
    assert status_for("cellBarcodeValidFraction", 0.40, DEFAULT_LINES) is Status.ALERT


def test_a_reagent_condition_declares_that_it_does_not_roll_up():
    # 310 keeps a reagent's failure off every sample: one bad panel marking twenty samples is how
    # a sample status becomes noise. The undeclared-barcode share is the one measurement whose
    # line is published per sample while the finding belongs to the run.
    by_id = {m.id: m for m in MEASUREMENTS}
    assert by_id["panelAssignedFraction"].rolls_up is False
    assert [m.id for m in MEASUREMENTS if not m.rolls_up] == ["panelAssignedFraction"]


def test_a_line_without_an_error_threshold_declares_no_error_comparison():
    # Two places say "this line has no error threshold" and they must agree, or a line would
    # carry a direction for a boundary it does not have.
    for measurement, line in DEFAULT_LINES.items():
        _, error_comparison = _COMPARISON[measurement]
        assert (line.error is None) == (error_comparison is None), measurement


def test_an_unjudged_measurement_claims_nothing_about_a_bad_value():
    # Atom 315: where no line can be defended, nothing is said about what a bad
    # value would mean, because nothing is known.
    for m in MEASUREMENTS:
        if m.line is None and m.deferred_reason is None:
            assert m.implies is None, m.id


def test_reads_total_and_high_reference_cells_are_unjudged():
    by_id = {m.id: m for m in MEASUREMENTS}
    assert by_id["readsTotal"].line is None
    assert by_id["highReferenceCells"].line is None
    assert status_for("readsTotal", 0.5, DEFAULT_LINES) is None


def test_the_invented_matched_fraction_line_is_gone():
    assert "matchedFraction" not in DEFAULT_LINES
    assert "readsTotal" not in DEFAULT_LINES


# --- lines are parameters, and every boundary is pinned --------------------


def test_depth_line_is_a_parameter_not_a_literal():
    assert DEFAULT_LINES["readsPerCell"] == Line(warn=5_000)
    assert status_for("readsPerCell", 4_000, {"readsPerCell": Line(5_000)}) is Status.WARN
    assert status_for("readsPerCell", 4_000, {"readsPerCell": Line(1_000)}) is Status.OK


def test_a_stated_recommendation_warns_and_never_alerts():
    # Atom 315: "One number gives one boundary, so it warns and never alerts." Depth has no
    # published error threshold, so no value of it can reach alert -- not even zero.
    assert DEFAULT_LINES["readsPerCell"].error is None
    assert status_for("readsPerCell", 0, DEFAULT_LINES) is Status.WARN
    assert status_for("readsPerCell", -1_000_000, DEFAULT_LINES) is Status.WARN


def test_two_thresholds_give_three_levels():
    # The distinction collapsing them lost. Three of the four inherited lines put error at total
    # failure, so a low-but-non-zero share warns and only a wholly failed one alerts.
    line = DEFAULT_LINES["panelAssignedFraction"]
    assert (line.warn, line.error) == (0.5, 0.0)
    assert status_for("panelAssignedFraction", 0.6, DEFAULT_LINES) is Status.OK
    assert status_for("panelAssignedFraction", 0.1, DEFAULT_LINES) is Status.WARN
    assert status_for("panelAssignedFraction", 0.0, DEFAULT_LINES) is Status.ALERT


def test_error_is_tested_before_warn(monkeypatch):
    # A value past both boundaries reads alert. Tested warn-first it would read warn, and the
    # worse finding would be the one that never showed. Barcode validity is the shipped line
    # that steps the same way twice -- warn below 0.75, error below 0.50 -- and it is not
    # computed here yet, so the case is exercised against a registered stand-in.
    monkeypatch.setitem(_COMPARISON, "syntheticTwoStep", ("at-least", "at-least"))
    lines = {"syntheticTwoStep": Line(warn=0.75, error=0.50)}
    assert status_for("syntheticTwoStep", 0.80, lines) is Status.OK
    assert status_for("syntheticTwoStep", 0.60, lines) is Status.WARN
    assert status_for("syntheticTwoStep", 0.40, lines) is Status.ALERT


def test_at_least_is_acceptable_exactly_at_the_line():
    # Atom 315 alerts *below* the recommendation, so the recommendation itself
    # is acceptable. The named value satisfies the condition it names.
    assert status_for("readsPerCell", 5_000, DEFAULT_LINES) is Status.OK
    assert status_for("readsPerCell", 4_999, DEFAULT_LINES) is Status.WARN
    assert status_for("panelAssignedFraction", 0.5, DEFAULT_LINES) is Status.OK
    assert status_for("panelAssignedFraction", 0.49, DEFAULT_LINES) is Status.WARN


def test_at_most_is_acceptable_exactly_at_the_line(monkeypatch):
    # No shipped measurement reads `at-most`: the only candidate was the undeclared-barcode fraction,
    # which now ships unjudged. The reading stays in the vocabulary because a line can be an upper
    # bound as easily as a lower one, so it is exercised against a registered stand-in rather than
    # left as an untested branch.
    monkeypatch.setitem(_COMPARISON, "syntheticUpperBound", ("at-most", None))
    lines = {"syntheticUpperBound": Line(warn=0.1)}
    assert status_for("syntheticUpperBound", 0.1, lines) is Status.OK
    assert status_for("syntheticUpperBound", 0.11, lines) is Status.WARN


def test_the_undeclared_barcode_fraction_ships_unjudged():
    # Atom 315 lists it among the four inherited numbers and the field does
    # publish 0.50 -- but for one aggregate library fraction, while this
    # measurement is per sequence at tag level. A fraction's line does not
    # transfer to a list of sequences, and given a count any upper bound
    # collapses into "alerting if a single undeclared barcode exists".
    by_id = {m.id: m for m in MEASUREMENTS}
    assert by_id["undeclaredBarcodes"].line is None
    assert by_id["undeclaredBarcodes"].implies is None
    assert "undeclaredBarcodes" not in DEFAULT_LINES
    assert status_for("undeclaredBarcodes", 0.4, DEFAULT_LINES) is None


def test_a_tag_the_reads_never_show_carries_no_status():
    # The verdict took this job: a tag with no reads removes its cells from what could answer, so the
    # position reads *never asked* rather than a confident negative. The measurement is a fact on the
    # tag's row, kept for the reagent's sake, and warning a reader off an answer that already says so
    # would be a second voice on one fact.
    assert status_for("declaredNeverSeen", 0, DEFAULT_LINES) is None
    assert status_for("declaredNeverSeen", 1, DEFAULT_LINES) is None
    assert "declaredNeverSeen" not in DEFAULT_LINES


def test_the_categorical_route_is_kept_with_no_member():
    # One of the three places a line can come from, and the only one nothing
    # currently uses. Kept as a route rather than deleted, so the next
    # measurement standing on a fact rather than a quantity has somewhere to go.
    assert "categorical" in LINE_ROUTES
    assert not [m.id for m in MEASUREMENTS if m.line == "categorical"]


def test_no_defensible_line_means_unjudged():
    assert status_for("antigenCountDistribution", 12, DEFAULT_LINES) is None


def test_a_deferred_measurement_is_never_unjudged_even_holding_a_value():
    assert status_for("aggregateBarcodeFraction", 0.9, DEFAULT_LINES) is None


def test_a_missing_value_is_not_evaluated():
    assert status_for("readsPerCell", None, DEFAULT_LINES) is None


# --- the three-level rollup -----------------------------------------------


def test_rollup_takes_the_worst_status():
    assert roll_up([Reading(Status.OK, 1.0), Reading(Status.ALERT, 1.0)]).status is Status.ALERT


def test_a_rollup_returns_a_coverage():
    assert isinstance(roll_up([Reading(Status.OK, 1.0)]), Coverage)


def test_coverage_never_enters_the_ordinal():
    r = roll_up([Reading(Status.OK, 1.0), Reading(None, 1.0), Reading(None, None)])
    assert r.status is Status.OK


def test_coverage_is_reported_beside_the_status():
    # Two unjudged against one not-evaluated, deliberately unequal. With one of each, a counter that
    # reported the other's total would read correctly, and the two questions "was a line defensible"
    # and "did anybody look" would be silently interchangeable.
    r = roll_up(
        [
            Reading(Status.OK, 1.0),
            Reading(Status.ALERT, 1.0),
            # No status but a number came back: computed, and no line to judge it against.
            Reading(None, 1.0),
            Reading(None, 1.0),
            # No status and no number: nothing computed it.
            Reading(None, None),
        ]
    )
    assert (r.judged, r.unjudged, r.not_evaluated) == (2, 2, 1)


def test_the_two_no_status_cases_are_told_apart_by_the_value():
    # Both return None from `status_for`, so the coverage triple is the only thing keeping
    # "no line to judge it against" apart from "nothing computed it".
    assert roll_up([Reading(None, 0.0)]).unjudged == 1
    assert roll_up([Reading(None, 0.0)]).not_evaluated == 0
    assert roll_up([Reading(None, float("nan"))]).not_evaluated == 1


def test_a_level_with_nothing_judgeable_is_not_evaluated():
    assert roll_up([Reading(None, 1.0), Reading(None, None)]).status is None


def test_a_level_with_no_measurements_at_all_is_not_evaluated():
    r = roll_up([])
    assert r.status is None
    assert (r.judged, r.unjudged, r.not_evaluated) == (0, 0, 0)


def test_only_one_aggregation_rule_remains():
    # A panel status overestimated what could be judged categorically, and a
    # capture status became the worst of every sample -- which the samples
    # already say. `roll_up` over a sample's own measurements is what is left.
    import qc_measures

    assert not hasattr(qc_measures, "roll_up_panel")
    assert not hasattr(qc_measures, "roll_up_capture")


def test_a_dead_reagent_does_not_mark_every_sample_alerting():
    # A per-tag failure is usually a property of the reagent across the whole run
    # rather than of any one sample. Fed into a sample status, one dead reagent in
    # a panel of twenty tags would mark every sample alerting, which makes that
    # status noise within one run. The sample rolls up its OWN measurements only.
    samples = [roll_up([Reading(Status.OK, 1.0)]).status for _ in range(3)]
    assert samples == [Status.OK] * 3


# --- corrupt numbers must never read green -------------------------------------------
#
# Every `<` and `>` comparison against NaN is False, so an unguarded NaN value falls through to
# `bad = False` and the measurement reads ACCEPTABLE. For QC code, corrupt-input-reads-green is the
# worst available failure mode: it is the one state a reader will not investigate.


def test_a_nan_value_is_not_evaluated_rather_than_acceptable():
    assert status_for("readsPerCell", float("nan"), DEFAULT_LINES) is None


def test_infinite_values_are_not_evaluated_rather_than_judged():
    # +inf would have read ACCEPTABLE against an at-least line, which is the green reading again. -inf
    # happens to alert, so only one direction was dangerous. But neither is a measurement, and one rule
    # for "not a finite number" is easier to defend than a rule that depends on the sign.
    assert status_for("readsPerCell", float("inf"), DEFAULT_LINES) is None
    assert status_for("readsPerCell", float("-inf"), DEFAULT_LINES) is None


def test_seen_in_counts_distinct_samples_not_cells():
    # Two cells in one sample must read as one sample, not two.
    counts = _counts(["T1", "T1"], [5, 3], ["S1", "S1"])
    states = _states(["T1"], ["bound"])

    row = per_antigen_measures(counts, states, ["T1"]).row(0, named=True)

    assert row["samplesSeenIn"] == 1


def test_seen_in_is_zero_for_a_dead_tag():
    # A declared tag with no counts anywhere. Zero, never null: a blank and a zero are opposite
    # findings on this table.
    counts = _counts(["T1"], [5])
    states = _states(["T1"], ["bound"])

    out = per_antigen_measures(counts, states, ["T1", "DEAD"])
    dead = out.filter(pl.col("tag") == "DEAD").row(0, named=True)

    assert dead["samplesSeenIn"] == 0
    assert dead["cellsWithCount"] == 0


def test_seen_in_reports_the_panel_size_beside_it():
    # "Most samples" is unreadable without the denominator.
    counts = _counts(["T1", "T1"], [5, 5], ["S1", "S2"])
    states = _states(["T1"], ["bound"])

    row = per_antigen_measures(counts, states, ["T1"]).row(0, named=True)

    assert row["samplesSeenIn"] == 2
    assert row["samplesInPanel"] == 2


def test_a_reference_tag_reports_its_own_seen_in():
    # Held out of the verdict read, so cellsAboveTheLine is None. The reagent still delivered it,
    # and seen-in says so.
    counts = _counts(["REF", "REF"], [9, 9], ["S1", "S2"])
    states = _states(["T1"], ["bound"])

    out = per_antigen_measures(counts, states, ["T1"], reference_tags=["REF"])
    ref = out.filter(pl.col("tag") == "REF").row(0, named=True)

    assert ref["samplesSeenIn"] == 2
    assert ref["cellsAboveTheLine"] is None


def test_the_denominator_is_the_declared_roster_not_the_observed_samples():
    # S3 is in the panel and contributed no rows at all -- a failed library. It must stay in the
    # denominator, or a tag seen in the two surviving samples reads as seen everywhere.
    counts = _counts(["T1", "T1"], [5, 5], ["S1", "S2"])
    states = _states(["T1"], ["bound"])

    out = per_antigen_measures(counts, states, ["T1"], panel_samples=["S1", "S2", "S3"])
    row = out.filter(pl.col("tag") == "T1").row(0, named=True)

    assert row["samplesSeenIn"] == 2
    assert row["samplesInPanel"] == 3
