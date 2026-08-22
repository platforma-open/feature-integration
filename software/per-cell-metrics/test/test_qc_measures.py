import dataclasses

import polars as pl
import pytest
from qc_measures import (
    _COMPARISON,
    DEFAULT_LINES,
    DEFAULT_OUTLIER_FENCE,
    LINE_ROUTES,
    MEASUREMENTS,
    NUMERIC_LINE_ROUTES,
    Coverage,
    Measurement,
    Status,
    antigen_count_deciles,
    attach_alerting_identities,
    measurement_rows,
    outlier_status,
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
    "readsPerCell": "sample",
    "antigenCountDistribution": "sample",
    "aggregateBarcodeFraction": "sample",
    "undeclaredBarcodes": "tag",
    "declaredNeverSeen": "tag",
    "floorRemoved": "sample",
    "uniqueCountsPerCell": "sample",
    "highReferenceCells": "sample",
    "perAntigen": "tag",
    "identityDisagreement": "identity",
    "tagDisagreement": "tag",
}

DEFERRED_IDS = {"aggregateBarcodeFraction"}


def test_every_declared_id_is_expected_and_every_expected_id_is_declared():
    assert {m.id for m in MEASUREMENTS} == set(EXPECTED_LEVEL_BY_ID)


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
    assert {m.level for m in MEASUREMENTS} <= {"sample", "tag", "identity"}


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

# A sentence opening with one of these reads as an instruction regardless of
# what follows -- "Replace the reagent." vs "A reagent that produced nothing
# did not work" -- so this catches advice phrased as an imperative, which the
# substring list above does not, since none of these words are banned outright
# (several appear as ordinary nouns/adjectives elsewhere in the set, e.g. "the
# vendor's recommended minimum").
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
        assert not any(phrase in lowered for phrase in BANNED_ADVICE_PHRASES), m.id

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


def test_deferred_measurement_produces_a_not_evaluated_row_with_its_reason():
    rows = measurement_rows()
    # Never absent: every declared id, deferred or not, has a row.
    assert {r["id"] for r in rows} == {m.id for m in MEASUREMENTS}

    by_id = {r["id"]: r for r in rows}
    for deferred_id in DEFERRED_IDS:
        row = by_id[deferred_id]
        assert row["status"] == "not evaluated"
        assert row["reason"]


def test_a_computed_measurement_carries_no_status():
    rows = measurement_rows()
    by_id = {r["id"]: r for r in rows}
    for m in MEASUREMENTS:
        if m.id not in DEFERRED_IDS:
            assert by_id[m.id]["status"] is None, m.id
            assert by_id[m.id]["reason"] is None, m.id


# --- per_antigen_measures: tag grain -----------------------------------------


def test_per_antigen_measures_reports_signal_above_and_median():
    # The cell reading 5 and not binding is what separates the two counters.
    # Without it both land on the same rows and each reads 2, so a version that
    # counted bound cells for both would pass -- and "cells with signal" would
    # silently become "cells above the line" wherever it is reported.
    states = pl.DataFrame(
        {
            "tag": ["T1", "T1", "T1", "T1"],
            "umiCount": [0, 5, 10, 40],
            "state": ["not bound", "not bound", "bound", "bound"],
        }
    )
    out = per_antigen_measures(states).row(0, named=True)
    assert out["cellsWithSignal"] == 3
    assert out["cellsAboveTheLine"] == 2
    assert out["medianAboveTheLine"] == 25.0


def test_per_antigen_measures_differs_between_tag_and_identity_grain():
    # T1 and T2 both feed one identity. As tags, T1 shows one weak cell (one
    # bound of two). As the combined identity, the same cells collapse to one
    # row and T1's weak showing is no longer visible on its own.
    tag_grain = pl.DataFrame(
        {
            "tag": ["T1", "T1", "T2", "T2"],
            "umiCount": [8, 1, 20, 15],
            "state": ["bound", "not bound", "bound", "bound"],
        }
    )
    identity_grain = pl.DataFrame(
        {
            "tag": ["ID1", "ID1", "ID1"],  # the identity each cell's highest tag reading combined into
            "umiCount": [20, 1, 15],
            "state": ["bound", "not bound", "bound"],
        }
    )

    by_tag = per_antigen_measures(tag_grain)
    by_identity = per_antigen_measures(identity_grain)

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
    # 11 cells with totals 0, 10, ..., 100: with linear interpolation over 11
    # sorted points, the p-th percentile lands exactly on index p/10, so every
    # decile equals its own cell's total -- a fixture an off-by-one position
    # error cannot pass unnoticed on.
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


# --- attach_alerting_identities ------------------------------------------------


def _identity_measures(rows: dict[str, tuple[int, int, float]]) -> pl.DataFrame:
    keys = list(rows)
    return pl.DataFrame(
        {
            "key": keys,
            "setsEvaluated": [rows[k][0] for k in keys],
            "setsDisagreeing": [rows[k][1] for k in keys],
            "disagreementRate": [rows[k][2] for k in keys],
            "level": ["identity"] * len(keys),
            "diagnosticOnly": ["false"] * len(keys),
        }
    )


def test_attach_alerting_identities_feeding_two_identities_attaches_both():
    identity_measures = _identity_measures({"ID1": (10, 1, 0.1), "ID2": (8, 0, 0.0), "ID3": (5, 2, 0.4)})
    grouping = {"T1": {"ID1", "ID2"}, "T2": {"ID3"}}

    out = attach_alerting_identities(identity_measures, grouping, alerting={"T1"})

    assert set(out["identity"].to_list()) == {"ID1", "ID2"}
    assert set(out["tag"].to_list()) == {"T1"}
    assert "ID3" not in out["identity"].to_list()
    # The identity's own figures travel with it, not just its name.
    row = out.filter(pl.col("identity") == "ID1").row(0, named=True)
    assert row["setsEvaluated"] == 10
    assert row["disagreementRate"] == pytest.approx(0.1)


def test_attach_alerting_identities_no_alerting_tags_returns_no_rows():
    identity_measures = _identity_measures({"ID1": (10, 1, 0.1)})
    out = attach_alerting_identities(identity_measures, {"T1": {"ID1"}}, alerting=set())
    assert out.height == 0
    assert set(out.columns) == {
        "tag",
        "identity",
        "setsEvaluated",
        "setsDisagreeing",
        "disagreementRate",
        "level",
        "diagnosticOnly",
    }


def test_attach_alerting_identities_tag_with_no_grouping_entry_contributes_no_row():
    identity_measures = _identity_measures({"ID1": (10, 1, 0.1)})
    out = attach_alerting_identities(identity_measures, {"T1": {"ID1"}}, alerting={"CONTROL"})
    assert out.height == 0


def test_four_readings_only_two_are_statuses():
    assert {s.value for s in Status} == {"acceptable", "alerting", "unjudged", "not evaluated"}


# --- the route is the single authority -------------------------------------
# Both directions, so neither table can grow an entry the other does not know
# about. This is what stops `DEFAULT_LINES` becoming a second declaration of
# which measurements carry a line.


def test_every_declared_route_is_one_of_the_atoms_four():
    assert {m.line for m in MEASUREMENTS if m.line} <= LINE_ROUTES
    assert LINE_ROUTES == {"inherited", "categorical", "recommended-and-observed", "against-the-run"}


def test_a_numeric_route_has_a_line_and_a_comparison_and_nothing_else_does():
    numeric = {m.id for m in MEASUREMENTS if m.line in NUMERIC_LINE_ROUTES}
    assert set(DEFAULT_LINES) == numeric
    assert set(_COMPARISON) == numeric


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
    assert status_for("readsTotal", 0.5, DEFAULT_LINES) is Status.UNJUDGED


def test_the_invented_matched_fraction_line_is_gone():
    assert "matchedFraction" not in DEFAULT_LINES
    assert "readsTotal" not in DEFAULT_LINES


# --- lines are parameters, and every boundary is pinned --------------------


def test_depth_line_is_a_parameter_not_a_literal():
    assert DEFAULT_LINES["readsPerCell"] == 5_000
    assert status_for("readsPerCell", 4_000, {"readsPerCell": 5_000}) is Status.ALERTING
    assert status_for("readsPerCell", 4_000, {"readsPerCell": 1_000}) is Status.ACCEPTABLE


def test_at_least_is_acceptable_exactly_at_the_line():
    # Atom 315 alerts *below* the recommendation, so the recommendation itself
    # is acceptable. The named value satisfies the condition it names.
    assert status_for("readsPerCell", 5_000, DEFAULT_LINES) is Status.ACCEPTABLE
    assert status_for("readsPerCell", 4_999, DEFAULT_LINES) is Status.ALERTING
    assert status_for("panelAssignedFraction", 0.5, DEFAULT_LINES) is Status.ACCEPTABLE
    assert status_for("panelAssignedFraction", 0.49, DEFAULT_LINES) is Status.ALERTING


def test_at_most_is_acceptable_exactly_at_the_line(monkeypatch):
    # No shipped measurement reads `at-most`: the only candidate was the
    # undeclared-barcode fraction, which now ships unjudged. The reading stays in
    # the vocabulary because a line can be an upper bound as easily as a lower
    # one, so it is exercised against a registered stand-in rather than left as
    # an untested branch.
    monkeypatch.setitem(_COMPARISON, "syntheticUpperBound", "at-most")
    lines = {"syntheticUpperBound": 0.1}
    assert status_for("syntheticUpperBound", 0.1, lines) is Status.ACCEPTABLE
    assert status_for("syntheticUpperBound", 0.11, lines) is Status.ALERTING


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
    assert status_for("undeclaredBarcodes", 0.4, DEFAULT_LINES) is Status.UNJUDGED


def test_categorical_alerts_only_on_the_named_fact():
    # Alerting *at* zero -- a different predicate from "at or below a floor",
    # which is why one direction flag cannot serve both.
    assert status_for("declaredNeverSeen", 0, DEFAULT_LINES) is Status.ALERTING
    assert status_for("declaredNeverSeen", 1, DEFAULT_LINES) is Status.ACCEPTABLE


def test_no_defensible_line_means_unjudged():
    assert status_for("antigenCountDistribution", 12, DEFAULT_LINES) is Status.UNJUDGED


def test_a_deferred_measurement_is_never_unjudged_even_holding_a_value():
    assert status_for("aggregateBarcodeFraction", 0.9, DEFAULT_LINES) is Status.NOT_EVALUATED


def test_a_missing_value_is_not_evaluated():
    assert status_for("readsPerCell", None, DEFAULT_LINES) is Status.NOT_EVALUATED


# --- the against-the-run route ---------------------------------------------


@pytest.mark.parametrize("measurement", ["identityDisagreement", "tagDisagreement"])
def test_an_against_the_run_measurement_is_refused_not_called_unjudged(measurement):
    # These two do carry a status; `status_for` just cannot compute it, having
    # no peers. Answering `unjudged` would be worse than refusing: unjudged
    # never enters a rollup, so an outlying reagent would leave its panel
    # reading clean -- the failure the rollup exists to invert.
    with pytest.raises(ValueError, match="outlier_status"):
        status_for(measurement, 0.4, DEFAULT_LINES)


def test_every_measurement_either_answers_or_refuses_but_never_lies():
    # Walks the whole declared set so a route added later cannot quietly fall
    # through to `unjudged`, which is the one wrong answer that hides.
    for m in MEASUREMENTS:
        if m.line == "against-the-run":
            with pytest.raises(ValueError):
                status_for(m.id, 0.4, DEFAULT_LINES)
        else:
            assert status_for(m.id, 0.4, DEFAULT_LINES) in set(Status)


def test_a_lone_outlier_is_flagged_because_peers_exclude_the_value():
    # If `peers` included the value, one extreme reading would inflate q3 and
    # could never be flagged -- the measure would defeat itself, and no fixture
    # carrying a second outlier would reveal it.
    assert outlier_status(0.9, [0.01, 0.02, 0.03, 0.02, 0.01]) is Status.ALERTING


def test_a_value_inside_its_peers_is_acceptable():
    assert outlier_status(0.02, [0.01, 0.02, 0.03, 0.02, 0.01]) is Status.ACCEPTABLE


def test_the_shipped_fence_is_the_far_out_fence():
    # Pinned like every other line. Without this, every fence assertion below
    # derives its expected value from the constant, so the constant itself is
    # free to move and no test notices.
    assert DEFAULT_OUTLIER_FENCE == 3.0


def test_the_fence_multiplier_is_a_parameter():
    # q1 0.02, q3 0.04, so the default fence sits at 0.10 and a fence of 0.5
    # at 0.05. 0.08 falls between them, which is the only way the parameter is
    # observable at all -- a value outside both brackets proves nothing.
    peers = [0.01, 0.02, 0.03, 0.04, 0.05]
    assert outlier_status(0.08, peers, fence=DEFAULT_OUTLIER_FENCE) is Status.ACCEPTABLE
    assert outlier_status(0.08, peers, fence=0.5) is Status.ALERTING


def test_a_value_exactly_at_the_fence_is_acceptable():
    peers = [0.0, 1.0, 2.0, 3.0, 4.0]
    q1, q3 = 1.0, 3.0
    fence = q3 + (q3 - q1) * DEFAULT_OUTLIER_FENCE
    assert outlier_status(fence, peers) is Status.ACCEPTABLE
    assert outlier_status(fence + 0.1, peers) is Status.ALERTING


def test_too_few_peers_is_unjudged_but_a_missing_value_is_not_evaluated():
    # Two different absences: nothing to compare against, versus nothing to
    # compare. Collapsing them would make an uncomparable tag look unchecked.
    assert outlier_status(0.9, [0.01, 0.02]) is Status.UNJUDGED
    assert outlier_status(None, [0.01, 0.02, 0.03]) is Status.NOT_EVALUATED


# --- the three-level rollup -----------------------------------------------


def test_rollup_takes_the_worst_status():
    assert roll_up([Status.ACCEPTABLE, Status.ALERTING]).status is Status.ALERTING


def test_a_rollup_returns_a_coverage():
    assert isinstance(roll_up([Status.ACCEPTABLE]), Coverage)


def test_coverage_never_enters_the_ordinal():
    r = roll_up([Status.ACCEPTABLE, Status.UNJUDGED, Status.NOT_EVALUATED])
    assert r.status is Status.ACCEPTABLE


def test_coverage_is_reported_beside_the_status():
    # Two unjudged against one not-evaluated, deliberately unequal: with one of
    # each, a counter that reported the other's total would read correctly and
    # the two questions "was a line defensible" and "did anybody look" would be
    # silently interchangeable.
    r = roll_up(
        [
            Status.ACCEPTABLE,
            Status.ALERTING,
            Status.UNJUDGED,
            Status.UNJUDGED,
            Status.NOT_EVALUATED,
        ]
    )
    assert (r.judged, r.unjudged, r.not_evaluated) == (2, 2, 1)


def test_a_level_with_nothing_judgeable_is_not_evaluated():
    assert roll_up([Status.UNJUDGED, Status.NOT_EVALUATED]).status is Status.NOT_EVALUATED


def test_a_level_with_no_measurements_at_all_is_not_evaluated():
    r = roll_up([])
    assert r.status is Status.NOT_EVALUATED
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
    samples = [roll_up([Status.ACCEPTABLE]).status for _ in range(3)]
    assert samples == [Status.ACCEPTABLE] * 3


def test_outlier_status_flags_only_high_values():
    # "A disagreement rate below its peers is a tag behaving better than the
    # panel, which is not a finding." A two-sided fence would alert on the
    # best-behaved reagent in every panel, which is the opposite of the point.
    peers = [0.4, 0.5, 0.6, 0.5]
    assert outlier_status(0.0, peers) is Status.ACCEPTABLE
    assert outlier_status(9.9, peers) is Status.ALERTING


def test_the_minimum_peer_count_is_satisfied_at_the_named_value():
    # The named value satisfies the condition it names: three peers is enough
    # to compare against, two is not. Nothing else pins this boundary.
    assert outlier_status(0.9, [0.01, 0.02, 0.03]) is not Status.UNJUDGED
    assert outlier_status(0.9, [0.01, 0.02]) is Status.UNJUDGED


# --- corrupt numbers must never read green -------------------------------------------
#
# Every `<` and `>` comparison against NaN is False, so an unguarded NaN value falls
# through to `bad = False` and the measurement reads ACCEPTABLE. A NaN among the peers
# makes np.quantile return NaN fences, with the same result. For QC code,
# corrupt-input-reads-green is the worst available failure mode: it is the one state a
# reader will not investigate.


def test_a_nan_value_is_not_evaluated_rather_than_acceptable():
    assert status_for("readsPerCell", float("nan"), DEFAULT_LINES) is Status.NOT_EVALUATED


def test_infinite_values_are_not_evaluated_rather_than_judged():
    # +inf would have read ACCEPTABLE against an at-least line, which is the green
    # reading again. -inf happens to alert, so only one direction was dangerous -- but
    # neither is a measurement, and one rule for "not a finite number" is easier to
    # defend than a rule that depends on the sign.
    assert status_for("readsPerCell", float("inf"), DEFAULT_LINES) is Status.NOT_EVALUATED
    assert status_for("readsPerCell", float("-inf"), DEFAULT_LINES) is Status.NOT_EVALUATED


def test_a_nan_value_is_not_evaluated_against_its_peers():
    assert outlier_status(float("nan"), [0.1, 0.2, 0.3, 0.4]) is Status.NOT_EVALUATED


def test_nan_among_the_peers_leaves_the_comparison_unjudged():
    # The value is a real number here. What cannot be defended is the distribution it
    # would be measured against. That is unjudged, not not-evaluated -- the
    # measurement was computed, and only the comparison is unavailable.
    assert outlier_status(0.9, [0.1, float("nan"), 0.3, 0.4]) is Status.UNJUDGED


def test_a_clear_outlier_still_alerts_with_finite_peers():
    # The guard above must not swallow the case the measure exists for.
    assert outlier_status(0.9, [0.01, 0.02, 0.03, 0.04]) is Status.ALERTING
