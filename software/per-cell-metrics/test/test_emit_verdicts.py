import json
import shutil
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
import qc_rows
from emit_verdicts import _build_grouping, _identity_properties, _linker_frame
from frame_io import undeclared_feature_counts
from identity_tables import CELL_PUNCH_MAX_CELLS, IDENTITY_SUMMARY_MAX_IDENTITIES, REFERENCE_IDENTITY_LABEL
from panel import ANY_SAMPLE, consistent_properties, property_columns
from qc_measures import DEFAULT_LINES, MEASUREMENTS, Line, Measurement
from verdict import DEFAULT_PANEL_MIN_MEMBERS, ReferenceChoice

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(cwd, *args, expect_failure=False):
    """Run the CLI, asserting it succeeded unless the caller wants a failure.

    Success is asserted HERE rather than left to each test: the tool writes into `cwd`, so a run that dies
    before writing leaves the PREVIOUS run's files in place and every read of them still succeeds.

    stderr rides along in the message because a bare `assert returncode == 0` tells you the run died and
    not why.
    """
    r = subprocess.run(
        [sys.executable, str(SRC / "emit_verdicts.py"), *map(str, args)], cwd=cwd, capture_output=True, text=True
    )
    if expect_failure:
        assert r.returncode != 0, f"expected a non-zero exit, got 0. stdout={r.stdout!r}"
    else:
        assert r.returncode == 0, f"exited {r.returncode}. stderr={r.stderr!r}"
    return r


def _identities_only(bed):
    """`result_identity_labels.csv` less the reference tags, which are not identities.

    The file also labels the reagent table's identity axis, and a reference tag takes a reagent row
    keyed on its own barcode. A test about the identity universe must not see it.
    """
    labels = dict(pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    for tag in json.loads((bed / "result_run_meta.json").read_text())["referenceTags"]:
        labels.pop(tag, None)
    return labels


BASE = [
    "counts.csv",
    "panel.csv",
    "--linker",
    "linker.csv",
    "--barcode-col",
    "Sequence",
    "--feature-col",
    "Name",
    "--sample-col",
    "Samples",
    "--role-column",
    "Type",
    "--reference-values",
    "Control",
    # Stated, because the CLI requires it and nothing below the model picks a rung. This bed declares a
    # comparator tag. A test wanting a different rung passes its own --reference-source, which argparse
    # takes as the later value.
    "--reference-source",
    "declared",
    "--output-prefix",
    "result",
]


@pytest.fixture
def bed(tmp_path):
    # The antigen counts clear the shipped cutoff of 75 against a reference of 6:
    # specificity_score(500, 6) and specificity_score(600, 6) are both 100, while a silent cell
    # scores ~7.5e-09. Counts of 50 and 60 would score 3.1 and 7.2 and read *not bound*.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS1,c2,AAAA,600\nS1,c2,CTRL,6\nS1,c3,CTRL,6\n"
    )  # c3 was asked about AAAA and read nothing
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\nS1,c3,K1\n")
    return tmp_path


def test_writes_every_artifact(bed):
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    for name in (
        "result_verdicts.csv",
        "result_set_counts.csv",
        "result_cell_counts.csv",
        "result_cell_raw_counts.csv",
        "result_cell_scalars.csv",
        "result_offered.csv",
        "result_identity_labels.csv",
        "result_tag_labels.csv",
        "result_identity_properties.csv",
        "result_panel_mismatch.csv",
        "result_undeclared_barcodes.csv",
        "result_run_meta.json",
    ):
        assert (bed / name).exists(), name


def test_tag_labels_name_every_tag_including_the_comparator(bed):
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    labels = dict(pl.read_csv(bed / "result_tag_labels.csv", infer_schema_length=0).iter_rows())
    panel = pl.read_csv(bed / "panel.csv", infer_schema_length=0)
    assert set(labels) == set(panel["Sequence"].to_list()), "one row per declared tag"
    # The reference tag has no identity of its own, so an identity-keyed label would miss it. It holds
    # reagent figures, so the reagent table needs its name.
    assert labels["CTRL"] == "Ctrl", f"the comparator kept its barcode: {labels}"
    assert labels["AAAA"] == "AgA"


def test_tag_labels_match_identity_labels_under_the_per_tag_grouping(bed):
    _run(bed, *BASE)
    tags = dict(pl.read_csv(bed / "result_tag_labels.csv", infer_schema_length=0).iter_rows())
    identities = dict(pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    reference = set(json.loads((bed / "result_run_meta.json").read_text())["referenceTags"])
    # An identity IS a tag here, so one rule must give one answer. Two rules would read a tag under one
    # name beside its verdict and another beside its reagent figures.
    #
    # The reference tag is exempt: held out of every identity and carrying no verdict, it reads under
    # its role rather than under its name, so the two tables differ on it by design.
    for identity, label in identities.items():
        if identity in reference:
            continue
        assert tags[identity] == label, f"{identity}: {tags[identity]!r} vs {label!r}"


def test_the_reference_tag_reads_as_the_baseline_reagent(bed):
    _run(bed, *BASE)
    identities = dict(pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    tags = dict(pl.read_csv(bed / "result_tag_labels.csv", infer_schema_length=0).iter_rows())
    meta = json.loads((bed / "result_run_meta.json").read_text())

    # The reagent table keys a tag absent from the grouping on its own barcode. Without a row here that
    # cell renders blank while every other row of the table names an antigen.
    assert identities["CTRL"] == REFERENCE_IDENTITY_LABEL, identities

    # The ROLE, never the reagent's own name: the same reagent row already carries that in its Tag column.
    assert tags["CTRL"] == "Ctrl"
    # And never the grouping value, which would read as an identity the run does not score.
    assert "Control" not in identities.values()
    assert "CTRL" not in meta["identities"]
    assert meta["referenceTags"] == ["CTRL"]

    # The label reaches only the reference tag. Without this the assertion above also passes on a build
    # that labelled every identity "baseline reagent".
    assert identities["AAAA"] == "AgA"


def test_a_silent_cell_votes_not_bound(bed):
    # c3 has no AAAA row in the counts. It was offered AAAA, so it must vote.
    _run(bed, *BASE)
    v = pl.read_csv(bed / "result_verdicts.csv")
    r = v.filter(pl.col("identity") == "AAAA").row(0, named=True)
    assert r["cellsAnswered"] == 3  # not 2
    assert r["state"] == "bound"  # 2 of 3


def test_the_reference_tag_gets_no_verdict(bed):
    _run(bed, *BASE)
    v = pl.read_csv(bed / "result_verdicts.csv")
    assert "CTRL" not in v["identity"].to_list()


def test_cell_counts_carry_the_re_derivation_material(bed):
    _run(bed, *BASE)
    c = pl.read_csv(bed / "result_cell_counts.csv")
    assert {"sampleId", "cellId", "tag", "umiCount", "referenceCount", "inCellList"} <= set(c.columns)


def test_no_score_leaves_the_block(bed):
    _run(bed, *BASE)
    for f in ("result_cell_scalars.csv", "result_verdicts.csv", "result_cell_counts.csv"):
        assert "score" not in pl.read_csv(bed / f).columns


def test_run_meta_records_every_choice(bed):
    _run(bed, *BASE)
    m = json.loads((bed / "result_run_meta.json").read_text())
    for key in (
        "referenceChoice",
        "cellListSource",
        "floor",
        "cutoff",
        "minVoters",
        "gateThreshold",
        "panelMinMembers",
        "grouping",
        "contending",
        "readingsFloored",
        "cellsEmptied",
        "cellsHighReference",
    ):
        assert key in m, key


def test_none_is_no_longer_a_selectable_rung(bed):
    # There is no bottom rung. "none" used to select one, and a baseline is now required, so the value
    # is refused by argparse rather than quietly reinterpreted as some other rung.
    r = _run(bed, *BASE, "--reference-source", "none", expect_failure=True)
    assert r.returncode != 0
    assert "none" in r.stderr


def test_a_tag_the_grouping_could_not_place_is_named_in_the_output(bed):
    # A property the panel file does not carry narrows what can be answered, and the narrowing has to
    # be visible where the answers are. Such a tag keeps its own identity rather than vanishing.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family\n"
        "S1,AgA,AAAA,Target,Spike\n"
        "S1,AgB,CCCC,Target,\n"
        "S1,Ctrl,CTRL,Control,Reference\n"
    )
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,40\nS1,c2,CCCC,40\nS1,c3,CCCC,40\n")
    r = _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))
    assert r.returncode == 0, r.stderr

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == ["CCCC"]

    identities = set(pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)["identity"].to_list())
    assert identities == {"Spike", "CCCC"}, "the unplaceable tag keeps its own identity rather than vanishing"


def test_a_tag_grouping_reports_no_unplaceable_tags(bed):
    # The default grouping places every tag by construction, so the field is present and empty rather
    # than absent: a reader must be able to tell "none" from "not checked".
    _run(bed, *BASE)
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == []


def test_a_tag_noisy_in_one_panel_reads_clean_on_the_panel_it_was_clean_in(bed):
    # Two samples declaring different tag sets are two panels, and both declare T00. T00's clonotype
    # splits itself in S2 and reads steady in S1. A run-global disagreement rate puts S2's noise on S1's
    # row, sending a reader to re-prepare the wrong panel. The rows are keyed `(tag, panelId)`, so the
    # figure on them has to be that panel's.
    shared = [f"T{i:02d}" for i in range(5)]
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        + "".join(f"S1,Ag{i},{t},Target\n" for i, t in enumerate(shared))
        + "S1,Ctrl,CTRL,Control\n"
        # S2 declares the same five plus one more, which is what makes it a different panel.
        + "".join(f"S2,Ag{i},{t},Target\n" for i, t in enumerate(shared))
        + "S2,AgX,TXXX,Target\n"
        + "S2,Ctrl,CTRL,Control\n"
    )
    rows = ["sampleId,cellId,tag,umiCount"]
    # S1: every tag reads the same in every cell, so nothing disagrees with itself.
    for cell in ("a1", "a2", "a3", "a4"):
        rows.append(f"S1,{cell},CTRL,6")
        rows.extend(f"S1,{cell},{t},5" for t in shared)
    # S2: T00 splits its clonotype two against two.
    for cell in ("b1", "b2", "b3", "b4"):
        rows.append(f"S2,{cell},CTRL,6")
        rows.append(f"S2,{cell},{shared[0]},{500 if cell in ('b1', 'b2') else 5}")
        rows.extend(f"S2,{cell},{t},5" for t in shared[1:])
        rows.append(f"S2,{cell},TXXX,5")
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")
    (bed / "linker.csv").write_text(
        "sampleId,cellId,setId\n"
        + "".join(f"S1,a{i},K1\n" for i in range(1, 5))
        + "".join(f"S2,b{i},K2\n" for i in range(1, 5))
    )

    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    t00 = qc.filter((pl.col("measurement") == "tagDisagreement") & (pl.col("entity") == shared[0]))
    assert t00.height == 2, "T00 is declared by both panels, so it carries a row in each"

    by_panel = {r["panelId"]: r for r in t00.iter_rows(named=True)}
    rates = {p: float(r["value"]) for p, r in by_panel.items()}
    assert len(set(rates.values())) == 2, f"one rate on both panels means the run-global figure: {rates}"

    clean = min(rates, key=lambda p: rates[p])
    noisy = max(rates, key=lambda p: rates[p])
    assert rates[clean] == 0.0, "the panel whose cells never disagreed reads zero"
    # Two cells of four sit in the minority of their own set, pooled over the cells of sets that had
    # something to compare: 2 of 4.
    assert rates[noisy] == pytest.approx(0.5)
    # Neither row carries a status. A comparison against the other tags in a panel is not a line, so
    # the value travels instead for a reader to compare.
    assert by_panel[clean]["status"] is None
    assert by_panel[noisy]["status"] is None


def test_the_key_only_frames_carry_a_value_column_so_they_can_become_columns(bed):
    # A p-column is built from a CSV's *value* columns, so a file of key columns alone imports as
    # nothing at all -- silently, since the file exists and is well formed.
    _run(bed, *BASE)
    offered = pl.read_csv(bed / "result_offered.csv", infer_schema_length=0)
    assert offered.columns == ["sampleId", "identity", "offered"]
    assert set(offered["offered"].to_list()) == {"true"}

    linker = pl.read_csv(bed / "result_tag_identity.csv", infer_schema_length=0)
    # (tag, identity). The linker has no sample axis, because neither side of its join has one. A third
    # axis would make the join malformed, label discovery would refuse to build a spec frame, and the
    # punchcard would render no columns.
    assert linker.columns == ["tag", "identity", "1"]
    assert linker.height == linker.unique().height, "duplicate axis keys break a grid silently"
    assert set(linker["1"].to_list()) == {"1"}


def test_asking_for_a_rung_that_cannot_serve_drops_to_none_and_never_to_another_rung(bed):
    # There is no cascade, and its absence is the point. This bed declares no comparator tag and carries
    # a panel large enough to stand in for one: ask for the declared rung and it is refused, rather than
    # quietly serving the panel. The scientist gets the rung they asked for or nothing.
    #
    # Twenty-six tags, against a shipped minimum of twenty-five, so the panel rung IS serviceable here
    # and the second half of the test proves it.
    tags = [f"T{i:02d}" for i in range(26)]
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n" + "".join(f"S1,Ag{i},{t},Target\n" for i, t in enumerate(tags))
    )
    # Background counts sit *above* the shipped floor of 4. At 3 they would be floored to zero and the
    # panel median would be 0, so every count that cleared the floor would score near 100 against it.
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("c1", "c2", "c3"):
        rows.append(f"S1,{cell},{tags[0]},900")
        rows.extend(f"S1,{cell},{t},10" for t in tags[1:])
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")

    # BASE asks for the declared rung, and this panel marks no tag as a comparator. Whether a reference
    # tag is declared is a property of the settings, so the run is refused before anything is read.
    r = _run(bed, *BASE, expect_failure=True)
    assert r.returncode != 0
    assert "declares no baseline tag" in r.stderr
    assert not (bed / "result_verdicts.csv").exists(), "a refused run writes no verdicts"

    # The same counts, asked the other way: the panel rung serves them. Which is what makes the refusal
    # above a choice the scientist made rather than a limit of the data.
    r = _run(bed, *BASE, "--reference-source", "panel")
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    states = set(pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)["state"].to_list())
    assert states != {"unreliable"}


def test_a_panel_too_small_to_serve_still_falls_to_no_comparator(bed):
    # The founding three-antigen case: too small to stand in as its own comparator, so the third rung is
    # right there and must not be skipped.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,AgB,BBBB,Target\nS1,AgC,CCCC,Target\n"
    )
    # Refused, and the message names the rung the scientist should reach for.
    r = _run(bed, *BASE, expect_failure=True)
    assert r.returncode != 0
    assert "declares no baseline tag" in r.stderr


def test_zero_cells_detected_alerts(bed):
    # The categorical route: the alerting condition is a fact -- no cell barcode observed at all -- and
    # not a quantity with a published threshold. A sample that detected none produced nothing for
    # anything downstream to read, which is a run failure a reader needs at a glance.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,0,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "cellsDetected").row(0, named=True)
    assert row["value"] == "0.0"
    assert row["status"] == "alert"


def test_a_positive_cell_count_reads_ok_and_claims_nothing_about_yield(bed):
    # Above zero the status says only that the fact is false. It does not say the yield was good -- how
    # many cells a sample should yield depends on the experiment, and no number for that is published.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,1,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "cellsDetected").row(0, named=True)
    assert row["value"] == "1.0"
    assert row["status"] == "OK"

    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,50000,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "cellsDetected").row(0, named=True)
    assert row["value"] == "50000.0"
    assert row["status"] == "OK"


def test_no_cell_list_leaves_membership_unknown_and_depth_unevaluated(bed):
    # Which barcodes held a cell is an input. With neither list input the observed barcodes must NOT
    # stand in: they outnumber cells by one to two orders of magnitude, and `readsPerCell` divides by
    # this, so a healthy library would read undersequenced.
    no_linker = [a for a in BASE if a not in ("--linker", "linker.csv")]
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,3,2,1200,300,0.82\n"
    )
    r = _run(bed, *no_linker, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["cellListSource"] == "none"
    assert meta["cellsInList"] is None

    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    depth = qc.filter(pl.col("measurement") == "readsPerCell").row(0, named=True)
    # No cell list, so no rate. No number means no status, and the row is still there.
    assert depth["status"] is None
    assert depth["value"] is None

    counts = pl.read_csv(bed / "result_cell_counts.csv", infer_schema_length=0)
    assert set(counts["inCellList"].to_list()) == {"unknown"}, "unclassified is not the same as classified 'no'"


def test_only_the_sample_rolls_up(bed):
    # A panel status assumed its per-tag measurements would mostly carry statuses. They do not: one is
    # categorical and the rest are read only as outliers against the other tags in the same panel. A
    # capture status was then the worst of every sample and every panel, which only repeats the samples.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    rollups = qc.filter(pl.col("measurement") == "rollup")
    levels = set(rollups["level"].to_list())
    assert levels == {"sample"}, f"only the sample rolls up, got {sorted(levels)}"

    def _triple(level):
        r = rollups.filter(pl.col("level") == level)
        return [int(r[c].cast(pl.Int64).sum()) for c in ("judged", "unjudged", "notEvaluated")]

    assert sum(_triple("sample")) > 0, "the sample rollup still counts what was checked"


def test_panel_assigned_fraction_keeps_its_value_and_carries_no_status(bed):
    # The undeclared-barcode line is the barcode's, and it never becomes a sample's. This sample-grain
    # measurement keeps its number and is never judged, so it never reaches the sample's rollup.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction,"
        "cellBarcodeValidFraction\n"
        "S1,20000,18000,0.9,4,2,1200,300,0.82,0.91\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)

    row = qc.filter(pl.col("measurement") == "panelAssignedFraction").row(0, named=True)
    assert row["value"] == "0.82"
    assert row["status"] is None
    # Computed but never judged: it counts as unjudged, never judged, and never not-evaluated.
    assert (row["judged"], row["unjudged"], row["notEvaluated"]) == ("0", "1", "0")

    sample = row["level"], row["entity"]
    rollup = qc.filter(
        (pl.col("measurement") == "rollup") & (pl.col("level") == sample[0]) & (pl.col("entity") == sample[1])
    ).row(0, named=True)
    judged_in_rollup = int(rollup["judged"])
    others = qc.filter(
        (pl.col("level") == "sample")
        & (pl.col("entity") == sample[1])
        & (~pl.col("measurement").is_in(["rollup", "panelAssignedFraction"]))
        & (pl.col("status").is_not_null())
    ).height
    assert judged_in_rollup == others, "the rollup counts every judged sample measurement except this one"


def test_per_tag_measurements_survive_the_removed_panel_rollup(bed):
    # This removes aggregation, not measurement. A reagent finding still lands on its own row, keyed by
    # the panel that has it.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    tags = qc.filter((pl.col("level") == "tag") & (pl.col("measurement") != "rollup"))
    assert tags.height > 0, "per-tag measurement rows are untouched by the rollup removal"
    assert all(p != "" for p in tags["panelId"].to_list()), "each still names its panel"


def test_contending_groups_reach_the_note(bed):
    (bed / "panel.csv").write_text((bed / "panel.csv").read_text() + "S1,AgB,CCCC,Target\n")
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,1\nS1,c2,CCCC,1\nS1,c3,CCCC,1\n")
    _run(bed, *BASE, "--contending", json.dumps([["AAAA", "CCCC"]]))
    # Read without schema inference: the flag is a literal "true"/"false" string, and polars would
    # otherwise infer the column back into a Boolean and hide whether the file carries the string.
    v = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    r = v.filter(pl.col("identity") == "CCCC").row(0, named=True)
    assert r["competedWith"] == "AAAA" and r["wasCompeted"] == "true"


def test_barcode_outside_the_cell_list_is_labelled_not_dropped(bed):
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,zzz,AAAA,99\n")
    _run(bed, *BASE)
    # Without schema inference, for the reason given in the contending test.
    c = pl.read_csv(bed / "result_cell_counts.csv", infer_schema_length=0)
    assert "zzz" in c["cellId"].to_list()
    assert c.filter(pl.col("cellId") == "zzz").row(0, named=True)["inCellList"] == "false"


def _raw_feature_counts(rows):
    return pl.DataFrame(rows, orient="row", schema={"FEATURE": pl.String, "totalWeight": pl.Int64})


def test_undeclared_feature_counts_are_exactly_those_outside_the_declared_set():
    raw = _raw_feature_counts([("AAAA", 10), ("BBBB", 5), ("CCCC", 3)])
    undeclared, share = undeclared_feature_counts(raw, {"AAAA"})
    assert set(undeclared["tag"].to_list()) == {"BBBB", "CCCC"}
    assert share == pytest.approx((5 + 3) / (10 + 5 + 3))


def test_no_undeclared_barcode_is_the_ordinary_empty_case():
    raw = _raw_feature_counts([("AAAA", 10), ("BBBB", 5)])
    undeclared, share = undeclared_feature_counts(raw, {"AAAA", "BBBB"})
    assert undeclared.height == 0
    assert share == 0.0


def test_zero_total_weight_reports_no_share_rather_than_zero():
    raw = _raw_feature_counts([])
    undeclared, share = undeclared_feature_counts(raw, {"AAAA"})
    assert undeclared.height == 0
    assert share is None


def test_the_declared_set_is_read_per_sample_not_pooled_across_samples():
    raw = _raw_feature_counts([("AAAA", 10), ("BBBB", 5), ("CCCC", 3)])
    undeclared_s1, share_s1 = undeclared_feature_counts(raw, {"AAAA"})
    undeclared_s2, share_s2 = undeclared_feature_counts(raw, {"AAAA", "BBBB", "CCCC"})
    assert set(undeclared_s1["tag"].to_list()) == {"BBBB", "CCCC"}
    assert undeclared_s2.height == 0
    assert share_s1 == pytest.approx((5 + 3) / (10 + 5 + 3))
    assert share_s2 == 0.0


def test_undeclared_barcode_is_reported_and_does_not_stop_the_reading(bed):
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,TTTT,99\n")
    r = _run(bed, *BASE)
    assert r.returncode == 0
    m = pl.read_csv(bed / "result_panel_mismatch.csv")
    assert "TTTT" in m.filter(pl.col("direction") == "undeclared-in-panel")["tag"].to_list()
    assert pl.read_csv(bed / "result_verdicts.csv").height > 0


# --- the undeclared-barcode table: keyed by sequence, carrying the field's own status ------
#
# Barcodes the reads carried that no panel declares get their own table, keyed by sequence, and it is
# the one thing on that surface that carries a status -- the share of a sample's reads landing in
# undeclared barcodes. That status is the barcode's, and it never becomes a sample's.


def _write_raw_feature_counts(bed, rows):
    lines = ["sampleId,FEATURE,totalWeight"] + [f"{s},{f},{w}" for s, f, w in rows]
    (bed / "raw_feature_counts.csv").write_text("\n".join(lines) + "\n")


def test_undeclared_barcode_table_is_empty_without_the_raw_feature_counts_input(bed):
    # No pre-refine pass reached this run, so the table reads as the ordinary empty case rather than as
    # an error.
    _run(bed, *BASE)
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert t.height == 0
    assert set(t.columns) == {"sampleId", "tag", "totalWeight", "barcodeShare", "readShare", "status"}


def test_undeclared_barcode_table_is_keyed_by_sequence_with_the_samples_share(bed):
    # The panel declares AAAA and CTRL for S1 (bed's panel.csv). ZZZZ is neither.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 40), ("S1", "CTRL", 10), ("S1", "ZZZZ", 10)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert t["tag"].to_list() == ["ZZZZ"]
    row = t.row(0, named=True)
    assert row["totalWeight"] == 10
    # The sample's whole undeclared share, and this one sequence's own. Here they coincide because ZZZZ
    # is the only undeclared sequence; the next test separates them.
    assert row["readShare"] == pytest.approx(10 / 60)
    assert row["barcodeShare"] == pytest.approx(10 / 60)
    assert row["status"] == "alert"  # this sequence is 16.7% of the sample, above the 5% alert line


def test_each_undeclared_barcode_carries_its_own_share_beside_the_samples(bed):
    # Two undeclared sequences of different weight. The sample's share is one number on both rows; each
    # row's own share is its own weight. A table that carried only the first cannot tell them apart.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 50), ("S1", "ZZZZ", 30), ("S1", "YYYY", 20)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    by_tag = {r["tag"]: r for r in t.iter_rows(named=True)}
    assert set(by_tag) == {"ZZZZ", "YYYY"}
    assert by_tag["ZZZZ"]["barcodeShare"] == pytest.approx(30 / 100)
    assert by_tag["YYYY"]["barcodeShare"] == pytest.approx(20 / 100)
    # One sample-level number, repeated -- 50 of 100 reads are undeclared.
    assert [r["readShare"] for r in by_tag.values()] == [pytest.approx(50 / 100)] * 2


def test_the_own_share_denominator_is_every_pre_refine_read_not_the_undeclared_ones(bed):
    # The share is of the SAMPLE's reads, so declared weight stays in the denominator. Dividing by the
    # undeclared weight alone would make these two sum to 1 and hide how small they are.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 90), ("S1", "ZZZZ", 6), ("S1", "YYYY", 4)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert sum(t["barcodeShare"].to_list()) == pytest.approx(0.10)


def test_undeclared_barcode_status_warns_above_one_percent(bed):
    _write_raw_feature_counts(bed, [("S1", "AAAA", 980), ("S1", "ZZZZ", 20)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert t.row(0, named=True)["status"] == "warn"  # 20/1000 = 2%


def test_undeclared_barcode_status_is_the_rows_own_not_the_samples(bed):
    # The status reads `barcodeShare`, so two rows of one sample can read differently. Read from
    # `readShare` it would be one word repeated, and a sample carrying one heavy sequence among many
    # light ones would say nothing about which sequence to look at.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 900), ("S1", "ZZZZ", 80), ("S1", "YYYY", 20)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    by_tag = {r["tag"]: r for r in t.iter_rows(named=True)}
    assert by_tag["ZZZZ"]["status"] == "alert"  # 8%, above the 5% alert line
    assert by_tag["YYYY"]["status"] == "warn"  # 2%, above the 1% warn line
    # One sample-level number on both rows, and it is no longer what the status reads.
    assert [r["readShare"] for r in by_tag.values()] == [pytest.approx(0.10)] * 2


def test_undeclared_barcode_share_alerts_when_every_read_is_undeclared(bed):
    _write_raw_feature_counts(bed, [("S1", "ZZZZ", 5)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert t.row(0, named=True)["status"] == "alert"


def test_undeclared_barcode_table_stays_empty_when_the_panel_covers_every_sequence(bed):
    # Every barcode the pre-refine pass saw is declared -- the outcome the field wants -- so the table
    # carries no row, not a claim that something failed.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 40), ("S1", "CTRL", 10)])
    _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    t = pl.read_csv(bed / "result_undeclared_barcodes.csv")
    assert t.height == 0


def test_undeclared_barcode_share_never_reaches_the_sample_report_or_rollup(bed):
    # The status is the barcode's, and it does not become a sample's. It must not appear among the
    # sample's own measurements or feed the sample's rolled-up status.
    _write_raw_feature_counts(bed, [("S1", "ZZZZ", 5)])
    r = _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    assert r.returncode == 0, r.stderr
    report = json.loads((bed / "result_qc_by_sample.json").read_text())
    ids = {m["id"] for m in report["S1"]["measurements"]}
    assert "undeclaredBarcodeShare" not in ids


def test_empty_join_writes_headers(bed):
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,zzz,K9\n")
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    assert {"setId", "identity", "state"} <= set(pl.read_csv(bed / "result_verdicts.csv").columns)


def test_a_cutoff_at_the_analytic_floor_is_refused(bed):
    # At or below specificity_score(0, 0) the analytic tally and the dense oracle disagree about a silent
    # admissible cell with no error raised. Tested *at* the bound, not merely either side of it: the
    # refusal is "at or below", and 0.04/0.05 alone cannot tell that from "below".
    from verdict import specificity_score

    bound = float(specificity_score(0, 0))

    r = _run(bed, *BASE, "--cutoff", "0.04", expect_failure=True)
    assert "0.042" in (r.stderr + r.stdout)
    _run(bed, *BASE, "--cutoff", repr(bound), expect_failure=True)  # the bound itself is refused
    _run(bed, *BASE, "--cutoff", repr(bound * 1.001))
    _run(bed, *BASE, "--cutoff", "0.05")


def test_rows_are_sorted_on_a_bed_wide_enough_for_order_to_show(bed):
    # The default bed has one set and one identity, where sorted and unsorted are the same frame and a
    # missing sort is invisible. Three identities declared in descending order across two sets differ.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgZ,ZZZZ,Target\nS1,AgM,MMMM,Target\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n"
    )
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("c1", "c2", "c3"):
        rows.append(f"S1,{cell},CTRL,6")
        for tag in ("ZZZZ", "MMMM", "AAAA"):
            rows.append(f"S1,{cell},{tag},500")
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K2\nS1,c2,K1\nS1,c3,K1\n")

    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    verdicts = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    assert verdicts.height == 6, "two sets by three identities"

    for name, keys in (
        ("result_verdicts.csv", ["setId", "identity"]),
        ("result_cell_counts.csv", ["sampleId", "cellId", "tag"]),
        ("result_cell_raw_counts.csv", ["sampleId", "cellId", "tag"]),
        ("result_offered.csv", ["sampleId", "identity"]),
        ("result_tag_identity.csv", ["tag", "identity"]),
    ):
        frame = pl.read_csv(bed / name, infer_schema_length=0)
        assert frame.height > 1, f"{name} is too small for order to mean anything"
        assert frame.equals(frame.sort(keys)), name


def test_run_meta_records_the_comparator_served_not_the_one_requested(bed):
    # `served_source` refuses a request it cannot honour -- here a panel comparator is asked for and the
    # panel is far too small. Recording the request instead would claim a comparator the run never had.
    r = _run(bed, *BASE, "--reference-source", "panel", "--panel-min-members", "50", expect_failure=True)
    assert r.returncode != 0
    # The message names the condition and the number, so a scientist knows which of the two halves to
    # change.
    assert "below the 50 that rung needs" in r.stderr
    assert not (bed / "result_verdicts.csv").exists(), "nothing is written for a rung that cannot serve"


def test_sequencing_depth_divides_by_the_cell_list_not_by_observed_barcodes(bed):
    # The vendor's five thousand is per called cell. Observed barcodes exceed called cells by one to two
    # orders of magnitude in droplet data, so dividing by them would let a badly undersequenced run read
    # acceptable. The bed makes the two differ: four barcodes carry counts, three are in the cell list.
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,zzz,AAAA,7\n")
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,4,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr

    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    depth = qc.filter(pl.col("measurement") == "readsPerCell").row(0, named=True)
    # 18000 / 3 listed cells = 6000, which clears the 5000 line.
    # 18000 / 4 observed barcodes = 4500, which would not.
    assert float(depth["value"]) == pytest.approx(6000.0)
    assert depth["status"] == "OK"


# Every module the entrypoint reaches, not just the entrypoint file. The check is on source text, so
# a helper moved out of `emit_verdicts.py` leaves it behind unless it is named here.
ENTRYPOINT_MODULES = ("emit_verdicts.py", "frame_io.py", "identity_tables.py", "qc_rows.py")


def test_the_dense_oracle_is_not_reachable_from_the_entrypoint():
    # The dense oracle exists to check the analytic tally in tests. On a realistic panel the grid it
    # builds is 11-20x the sparse input.
    for module in ENTRYPOINT_MODULES:
        assert "densify" not in (SRC / module).read_text(), module


def test_property_grouping_normalises_and_excludes_the_reference(bed):
    # The stray whitespace is the point: `read_panel` normalises tag and sample and leaves properties
    # alone, so a builder reading the column directly makes " Spike " and "Spike" two identities.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family\n"
        "S1,AgA,AAAA,Target, Spike \n"
        "S1,AgB,CCCC,Target,Spike\n"
        "S1,Ctrl,CTRL,Control,Reference\n"
    )
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,40\nS1,c2,CCCC,40\nS1,c3,CCCC,1\n")
    r = _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))
    assert r.returncode == 0, r.stderr
    identities = set(pl.read_csv(bed / "result_verdicts.csv")["identity"].to_list())
    assert identities == {"Spike"}  # one identity, not " Spike " and "Spike"
    assert "Reference" not in identities  # the comparator is never a candidate


def test_the_declarations_that_hold_of_an_identity_travel_with_it(bed):
    # Whatever the panel says consistently about an identity's tags travels with that identity's
    # verdicts. The bed puts every case in one run -- a property both member tags agree on, one they
    # disagree about, and one that agrees for a single-tag identity while disagreeing for the merged one
    # -- because a property tested alone cannot show that the disagreement is dropped per identity.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family,Species,Carrier\n"
        "S1,AgA,AAAA,Target,Spike,Human,Biotin\n"
        "S1,AgB,CCCC,Target,Spike,Human,Streptavidin\n"
        "S1,AgC,GGGG,Target,Nuc,Human,Biotin\n"
        "S1,Ctrl,CTRL,Control,Reference,Cyno,Avidin\n"
    )
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,40\nS1,c1,GGGG,40\n")
    _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))

    held = {
        row["identity"]: row
        for row in pl.read_csv(bed / "result_identity_properties.csv", infer_schema_length=0).iter_rows(named=True)
    }
    assert set(held) == {"Spike", "Nuc"}, "one row per identity, and the comparator is not an identity"

    # Agreed across both member tags, so it holds of the merged identity.
    assert held["Spike"]["Species"] == "Human"
    assert held["Spike"]["Type"] == "Target"
    assert held["Spike"]["Family"] == "Spike"
    # Disagreed between the member tags, so it holds of nothing: neither tag's value wins.
    assert held["Spike"]["Carrier"] == ""
    assert held["Spike"]["Name"] == ""
    # The same two columns still hold for the identity whose single tag settles them, which is what
    # makes the omission above about the identity rather than about the column.
    assert held["Nuc"]["Carrier"] == "Biotin"
    assert held["Nuc"]["Name"] == "AgC"

    # The reference tag declares Cyno and Avidin and is no identity, so neither value can reach the
    # export: a declaration travelling from a tag that gets no verdict would describe nothing.
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["identityPropertyValues"]["Species"] == ["Human"]
    assert meta["identityPropertyValues"]["Type"] == ["Target"]
    assert "Avidin" not in meta["identityPropertyValues"]["Carrier"]
    # The workflow builds one spec per name in this list, so a name here that is not a column of the
    # CSV, or the reverse, is an import of nothing.
    columns = pl.read_csv(bed / "result_identity_properties.csv", infer_schema_length=0).columns
    assert meta["identityProperties"] == [c for c in columns if c != "identity"]


def test_a_property_no_identity_agreed_on_is_left_out_rather_than_exported_blank(bed):
    # One identity, and it disagrees about Carrier. An all-blank column would offer a reader a filter
    # with no values in it, so the column does not ship at all.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family,Carrier\n"
        "S1,AgA,AAAA,Target,Spike,Biotin\n"
        "S1,AgB,CCCC,Target,Spike,Streptavidin\n"
        "S1,Ctrl,CTRL,Control,Reference,Avidin\n"
    )
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,40\n")
    _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))

    columns = pl.read_csv(bed / "result_identity_properties.csv", infer_schema_length=0).columns
    assert "Carrier" not in columns
    assert "Family" in columns, "a column that does hold still ships"
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert "Carrier" not in meta["identityProperties"]


def test_two_identities_that_disagree_the_same_way_do_not_share_a_label(bed):
    # Both barcodes carry Family=Spike in S1 and Family=Nuc in S2. The panel declares per tag AND
    # sample, so that is not a disagreement to fall back from -- it is two declarations, and each barcode
    # joins the family its own sample named.
    #
    # Do not invert this back to a fallback where each barcode stands alone under its raw sequence,
    # labelled with its declared values joined. That shape is only forced by a dataset-wide map.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family\n"
        "S1,AgA,AAAA,Target,Spike\n"
        "S2,AgA,AAAA,Target,Nuc\n"
        "S1,AgB,CCCC,Target,Spike\n"
        "S2,AgB,CCCC,Target,Nuc\n"
        "S1,Ctrl,CTRL,Control,Reference\n"
        "S2,Ctrl,CTRL,Control,Reference\n"
    )
    (bed / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS2,c2,CCCC,500\nS2,c2,CTRL,6\n"
    )
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c2,K2\n")
    _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))

    labels = _identities_only(bed)
    assert set(labels) == {"Spike", "Nuc"}, "each barcode joins the family its own sample declared"
    assert len(set(labels.values())) == 2, f"two identities under one label: {labels}"
    # A property grouping makes the identity the property's value, which is already the name a reader
    # recognises, so no barcode is appended and no name is joined.
    assert labels == {"Spike": "Spike", "Nuc": "Nuc"}

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == [], "nothing fell back: every pair carried a value"


def test_a_flat_contending_list_is_refused_rather_than_read_as_characters(bed):
    # `["AgA","AgB"]` is valid JSON and the shape a hand-driven run reaches for first. Read as groups it
    # makes `set("AgA")` -- a set of CHARACTERS -- so the run completes, no competitor note fires, and the
    # run record states a contention nothing tested.
    r = _run(bed, *BASE, "--contending", json.dumps(["AgA", "AgB"]), expect_failure=True)
    assert "--contending" in r.stderr

    # A group of one tests nothing: an identity cannot contend with itself.
    r = _run(bed, *BASE, "--contending", json.dumps([["AgA"]]), expect_failure=True)
    assert "fewer than two members" in r.stderr

    # The valid shape still runs, so the guard rejects the mistake rather than the feature.
    _run(bed, *BASE, "--contending", json.dumps([["AgA", "AgB"]]))


def test_a_non_object_grouping_gets_the_usage_message_not_an_attribute_error(bed):
    # `--grouping '"tag"'` parses as JSON and is not a mapping. Reaching `.get` on it raises an
    # AttributeError, where a usage message for this exact mistake is written two lines away.
    r = _run(bed, *BASE, "--grouping", json.dumps("tag"), expect_failure=True)
    assert "--grouping must be" in r.stderr
    assert "AttributeError" not in r.stderr


def test_a_non_integer_umi_count_names_the_file_and_the_column(bed):
    # A blank or a decimal dies in the cast as a raw polars traceback naming neither the file nor the
    # column. This module's convention is that a bad input exits with a message about the input.
    (bed / "counts.csv").write_text("sampleId,cellId,tag,umiCount\nS1,c1,AAAA,\nS1,c2,AAAA,3.5\n")
    r = _run(bed, *BASE, expect_failure=True)
    assert "counts.csv" in r.stderr
    assert "umiCount" in r.stderr
    assert "Traceback" not in r.stderr


DECLARED_FLAGS = (
    "--linker",
    "--cells",
    "--barcode-col",
    "--feature-col",
    "--sample-col",
    "--role-column",
    "--reference-values",
    "--reference-source",
    "--panel-min-members",
    "--floor",
    "--cutoff",
    "--min-voters",
    "--min-agreement",
    "--gate-threshold",
    "--grouping",
    "--contending",
    "--capture-map",
    "--output-prefix",
)


def test_every_declared_flag_is_reachable_from_the_command_line(bed):
    # Every parameter of the reading is threaded from the workflow, so a parameter that exists only as a
    # module default is one a scientist cannot move. The help text is the cheapest place the whole set is
    # visible at once.
    help_text = _run(bed, "--help").stdout
    for flag in DECLARED_FLAGS:
        assert flag in help_text, flag


def test_output_is_byte_stable_across_runs(bed):
    # `combine_tags_to_identities` groups without maintaining order, so an unsorted frame varies run to
    # run. A p-column's identity is content addressed, so an unstable byte order costs downstream dedup.
    #
    # Repeating the run is not enough on its own: polars groups deterministically for one input, so an
    # unsorted frame reproduces itself byte for byte. Sortedness itself is asserted in
    # `test_rows_are_sorted_on_a_bed_wide_enough_for_order_to_show`.
    _run(bed, *BASE)
    first = {p.name: p.read_bytes() for p in bed.glob("result_*")}
    _run(bed, *BASE)
    second = {p.name: p.read_bytes() for p in bed.glob("result_*")}
    assert first == second


def test_a_computed_but_unjudged_measurement_is_not_reported_as_unchecked(bed):
    # Both no-status cases leave the status column empty, so the distinction has to survive in the VALUE
    # and the coverage triple beside it. "Computed, and no line stands behind it" and "nothing computed
    # this" are the pair the status set exists to keep apart.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)

    # Computed, no line: a number, and the triple counts it unjudged.
    floor_row = qc.filter(pl.col("measurement") == "floorRemoved").row(0, named=True)
    assert floor_row["status"] is None
    assert floor_row["value"] is not None
    assert (floor_row["judged"], floor_row["unjudged"], floor_row["notEvaluated"]) == ("0", "1", "0")

    # Nothing computed it: no number, the reason in its place, and the triple counts it not-evaluated.
    # Same empty status column, opposite finding.
    deferred = qc.filter(pl.col("measurement") == "aggregateBarcodeFraction").row(0, named=True)
    assert deferred["status"] is None
    assert deferred["value"] is None
    assert (deferred["judged"], deferred["unjudged"], deferred["notEvaluated"]) == ("0", "0", "1")
    assert deferred["reason"]  # a deferred measurement says why nothing computed it


def test_a_capture_map_is_accepted_and_changes_no_row(bed):
    # The capture rollup was the only reader of this map, and only the sample carries an aggregated
    # status now. The argument stays accepted because the capture axis ships on the QC columns. So
    # supplying a map must not fail, and must not put a row anywhere either.
    _run(bed, *BASE, "--capture-map", json.dumps({"S1": "C1"}))
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    assert "capture" not in set(qc["level"].to_list())
    assert "C1" not in set(qc["entity"].to_list())


def test_a_cell_list_of_its_own_overrides_the_linker_and_is_recorded(bed):
    # The cell list is an input. The linker only says which set a cell belongs to. A list from gene
    # expression covers cells whose receptor never assembled, which the linker structurally cannot.
    (bed / "cells.csv").write_text("sampleId,cellId\nS1,c1\nS1,c2\n")
    r = _run(bed, *BASE, "--cells", "cells.csv")
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["cellListSource"] == "cell list" and meta["cellsInList"] == 2
    scalars = pl.read_csv(bed / "result_cell_scalars.csv", infer_schema_length=0)
    assert scalars.filter(pl.col("cellId") == "c3").row(0, named=True)["inCellList"] == "false"


def test_a_gate_sets_cells_aside_and_says_how_many(bed):
    r = _run(bed, *BASE, "--gate-threshold", "5")
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["cellsSetAside"] == 3  # every one of c1, c2 and c3 reads the comparator at 6
    v = pl.read_csv(bed / "result_verdicts.csv")
    assert v.filter(pl.col("identity") == "AAAA").row(0, named=True)["state"] == "unreliable"


def test_the_floor_runs_before_tags_combine(bed):
    # Order is visible in the count: the floor works on the sparse per-tag frame, so two readings of one
    # identity in one cell are two floored readings. Combining first would take the highest and floor one.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family\n"
        "S1,AgA,AAAA,Target,Spike\n"
        "S1,AgB,CCCC,Target,Spike\n"
        "S1,Ctrl,CTRL,Control,Reference\n"
    )
    (bed / "counts.csv").write_text("sampleId,cellId,tag,umiCount\nS1,c1,AAAA,1\nS1,c1,CCCC,1\nS1,c1,CTRL,6\n")
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\n")
    r = _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["readingsFloored"] == 2
    assert meta["cellsEmptied"] == 1
    v = pl.read_csv(bed / "result_verdicts.csv")
    assert v.filter(pl.col("identity") == "Spike").row(0, named=True)["state"] == "not bound"


# ---- the committed fixture bed ---------------------------------------------------------------
#
# Every test above writes its own three-line bed, which keeps each one readable and none of them
# realistic. The committed bed at software/test-data/fixtures/verdicts/ carries the awkward panel
# shapes at once -- panels of differing size, barcodes recurring under different names, one antigen on
# two barcodes, one comparator and two, and a barcode declared on one sample and read on another.

VERDICT_BED = Path(__file__).resolve().parents[2] / "test-data" / "fixtures" / "verdicts"
VERDICT_BED_FILES = (
    "counts.csv",
    "linker.csv",
    "panel.csv",
    "panel_with_reference.csv",
    "panel_multi_reference.csv",
    "panel_narrow.csv",
    "panel_wide.csv",
)

NAME_GROUPING = ("--grouping", json.dumps({"by": "property", "column": "Name"}))


@pytest.fixture
def wide_bed(tmp_path):
    """The committed bed, copied so a run's output files never land in the repository."""
    for name in VERDICT_BED_FILES:
        source = VERDICT_BED / name
        if not source.exists():
            pytest.fail(f"committed bed missing at {source}; regenerate it with generate.py", pytrace=False)
        shutil.copy(source, tmp_path / name)
    return tmp_path


def _bed_args(panel_csv, *extra):
    # The bed's column names are the ones BASE already names, so only the panel file varies across the
    # three shapes: no comparator, one comparator, two. The gate is on for every bed run: it is what sets
    # c11 aside and so what keeps *unreliable* reachable. 100 is above every other comparator here and
    # below c11's 400, so exactly one cell is set aside.
    return ["counts.csv", panel_csv, *BASE[2:], "--gate-threshold", "100", *extra]


def _bed_shape(bed):
    """The handles these tests need, recovered from the bed by the role each barcode plays.

    Derived rather than written down because the sequences come from a seeded RNG. A bed regenerated
    under a different seed still has four barcodes carrying two antigen names, one antigen carried on two
    barcodes and one barcode declared by a single sample and read only in another.
    """
    panel = pl.read_csv(bed / "panel_multi_reference.csv", infer_schema_length=0)
    counts = pl.read_csv(bed / "counts.csv", infer_schema_length=0)
    linker = pl.read_csv(bed / "linker.csv", infer_schema_length=0)

    read_in: dict[str, set[str]] = {}
    for sample, tag in counts.select("sampleId", "tag").iter_rows():
        read_in.setdefault(tag, set()).add(sample)

    names: dict[str, set[str]] = {}
    declared_in: dict[str, set[str]] = {}
    offered: dict[str, set[str]] = {}
    for row in panel.filter(pl.col("Type") != "Control").iter_rows(named=True):
        names.setdefault(row["Sequence"], set()).add(row["Name"])
        declared_in.setdefault(row["Sequence"], set()).add(row["Samples"])
        # `offered` means what a cell could ANSWER at, which needs the declaration AND the reads. A tag
        # the sample's reads never carry was declared and never measured. The key is kept either way so a
        # sample that measured nothing still appears.
        offered.setdefault(row["Samples"], set())
        if row["Samples"] in read_in.get(row["Sequence"], set()):
            offered[row["Samples"]].add(row["Sequence"])

    tags_of_name: dict[str, set[str]] = {}
    for tag, tag_names in names.items():
        for name in tag_names:
            tags_of_name.setdefault(name, set()).add(tag)

    sets_of_sample: dict[str, set[str]] = {}
    samples_of_set: dict[str, set[str]] = {}
    for sample, set_id in linker.select("sampleId", "setId").iter_rows():
        sets_of_sample.setdefault(sample, set()).add(set_id)
        samples_of_set.setdefault(set_id, set()).add(sample)

    # Declared by exactly one sample and read in none of that sample's cells: the only arrangement in
    # which both directions of the panel-versus-reads check fire on the same barcode at once.
    cross = [t for t, samples in declared_in.items() if len(samples) == 1 and not samples & read_in.get(t, set())]
    shared = [(name, sorted(tags)) for name, tags in tags_of_name.items() if len(tags) > 1]
    short_sample = min(offered, key=lambda s: (len(offered[s]), s))
    spanning = sorted(s for s, samples in samples_of_set.items() if len(samples) > 1)

    return {
        "antigens": set(names),
        "names": set(tags_of_name),
        "renamed": {t for t, tag_names in names.items() if len(tag_names) > 1},
        "shared": shared,
        "cross": cross,
        "read_in": read_in,
        "declared_in": declared_in,
        "offered": offered,
        "short_sample": short_sample,
        "sets_of_sample": sets_of_sample,
        "spanning": spanning,
    }


def _states(bed):
    v = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    return {(r["setId"], r["identity"]): r["state"] for r in v.iter_rows(named=True)}


def _only_set(shape, sample):
    sets = shape["sets_of_sample"][sample]
    assert len(sets) == 1, f"the bed must draw one set from {sample} for this assertion to be about that set"
    return next(iter(sets))


def _samples_of(shape, set_id):
    return {s for s, sets in shape["sets_of_sample"].items() if set_id in sets}


def test_the_bed_keys_identity_by_barcode_where_the_names_would_split(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["renamed"]) >= 4, "the bed must carry the case that makes name keying wrong"

    r = _run(wide_bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    identities = set(pl.read_csv(wide_bed / "result_verdicts.csv", infer_schema_length=0)["identity"].to_list())
    assert identities == shape["antigens"], "one identity per declared barcode, whatever it was named"

    # The two keyings do not even agree on how many questions the run asks: keying on the name would
    # split each renamed barcode in two and fuse the antigen carried on two barcodes into one.
    assert len(shape["names"]) > len(shape["antigens"])

    # A label is not an identity, and two identities under one label are two rows a reader cannot tell
    # apart, so where two barcodes share a name the label has to carry the barcode as well.
    labels = _identities_only(wide_bed)
    assert set(labels) == shape["antigens"]
    assert len(set(labels.values())) == len(labels)

    # And from the other side: asked to group by the name, the run PLACES the renamed barcodes, one
    # identity per name the panel declared.
    #
    # A barcode named differently in two samples does NOT have "no one name that holds", and must not be
    # reported in `tagsWithoutGroupingValue` or left standing alone under its raw sequence. Those are two
    # declarations, so each is placed under the name its own sample gave it.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv", *NAME_GROUPING))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == [], "a renamed barcode is placed, not left unplaceable"
    named = set(pl.read_csv(wide_bed / "result_verdicts.csv", infer_schema_length=0)["identity"].to_list())
    assert named == shape["names"], "one identity per declared name, across every sample"


def test_the_bed_reaches_all_four_states_in_one_run(wide_bed):
    # A bed that cannot reach a state tests nothing about it. All four come from one run here: bound from
    # counts of 500 and 5000 against a comparator of 6, not bound from counts of 8, never asked from the
    # three-tag panel, and unreliable from the one cell the admissibility gate sets aside.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    v = pl.read_csv(wide_bed / "result_verdicts.csv", infer_schema_length=0)
    assert set(v["state"].to_list()) == {"bound", "not bound", "never asked", "unreliable"}


def test_the_short_panel_is_where_never_asked_appears(wide_bed):
    shape = _bed_shape(wide_bed)
    short = shape["short_sample"]
    assert len(shape["offered"][short]) < len(shape["antigens"]), "the bed needs panels of differing size"

    r = _run(wide_bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    states = _states(wide_bed)

    unasked = {i for (s, i), state in states.items() if s == _only_set(shape, short) and state == "never asked"}
    assert unasked == shape["antigens"] - shape["offered"][short]
    assert unasked

    # A set spanning two samples could answer wherever either sample both declared and measured a tag, so
    # a panel gap in one sample closes where the other covers it.
    #
    # One barcode is offered nowhere and cannot close: the cross declaration, declared by a single sample
    # and read only in another. That barcode is the bed's deliberate hole -- it exists so both directions
    # of the panel-versus-reads check fire on one tag.
    spanning = shape["spanning"]
    assert spanning, "the bed needs one set drawn from two samples"
    covered = set().union(*(shape["offered"][s] for s in _samples_of(shape, spanning[0])))
    assert covered == shape["antigens"] - set(shape["cross"]), (
        "the spanning set's samples must cover the universe apart from the cross declaration"
    )
    unasked_spanning = {i for (s, i), state in states.items() if s == spanning[0] and state == "never asked"}
    assert unasked_spanning == set(shape["cross"])


def test_the_panel_mismatch_fires_per_sample_in_both_directions(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["cross"]) == 1, "the bed carries exactly one barcode declared here and read there"
    tag = shape["cross"][0]
    declaring = next(iter(shape["declared_in"][tag]))
    reading = sorted(shape["read_in"][tag])

    # Read against the two-comparator panel, the only one here declaring every barcode the counts carry:
    # on the others the undeclared comparator adds rows.
    #
    # On the panel rung, because this version refuses two declared comparators and this test is about the
    # PANEL FILE rather than about the comparator. The minimum is lowered to the panel it has, for the
    # same reason.
    size = pl.read_csv(wide_bed / "panel_multi_reference.csv", infer_schema_length=0)["Sequence"].n_unique()
    on_panel_rung = ["--reference-source", "panel", "--panel-min-members", str(size)]
    r = _run(wide_bed, *_bed_args("panel_multi_reference.csv", *on_panel_rung))
    assert r.returncode == 0, r.stderr

    m = pl.read_csv(wide_bed / "result_panel_mismatch.csv", infer_schema_length=0)
    rows = {(row["tag"], row["direction"]): row["samples"] for row in m.iter_rows(named=True)}
    assert m.height == 2, f"only the cross declaration should mismatch; got {m.to_dicts()}"
    assert rows[(tag, "declared-never-seen")] == declaring
    assert rows[(tag, "undeclared-in-panel")] == ", ".join(reading)

    # A global check would have cancelled these two against each other. The sample that read the barcode
    # never declared it, so its set reads never asked while a real count of 500 sits in the counts file.
    states = _states(wide_bed)
    assert states[(_only_set(shape, reading[0]), tag)] == "never asked"

    # And the sample that DECLARED it read nothing for it, so its cells leave that identity's denominator
    # too: zero reads across a whole sample is a reagent that produced nothing. Both sides read never
    # asked, for opposite reasons: one sample was never offered the barcode, the other was offered it and
    # nobody measured it.
    #
    # This is NOT the per-cell rule. A cell that read nothing for a tag its sample DID measure votes not
    # bound. `test_a_silent_cell_votes_not_bound` pins that.
    assert states[(_only_set(shape, declaring), tag)] == "never asked"


def test_one_antigen_on_two_barcodes_is_read_by_its_highest_member(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["shared"]) == 1, "the bed carries exactly one antigen on two barcodes"
    name, (first, second) = shape["shared"][0]
    spanning = shape["spanning"][0]

    # Per barcode the two cells that carry them bind opposite ones, so each barcode splits its set one to
    # one and reads unreliable on the tie.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    per_tag = _states(wide_bed)
    assert per_tag[(spanning, first)] == "unreliable"
    assert per_tag[(spanning, second)] == "unreliable"

    # Read as one antigen the two barcodes combine by the highest member, never by the sum and never by
    # an arbitrary one: each cell's reading becomes 500, both cells bind, and the set is bound. Summing
    # would reach the same verdict here by accident. What the highest rule buys is that a cell's answer
    # does not depend on how many barcodes happened to carry the antigen.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv", *NAME_GROUPING))
    assert r.returncode == 0, r.stderr
    assert _states(wide_bed)[(spanning, name)] == "bound"


def test_two_declared_comparators_serve_together(wide_bed):
    # A panel declaring two undifferentiated comparators runs. They are replicates of
    # one group, since nothing declared separates them, and replicates combine by taking the highest.
    # It used to be refused, which sent the scientist back to edit a panel file over a case the corpus
    # had already decided.
    r = _run(wide_bed, *_bed_args("panel_multi_reference.csv"))
    assert r.returncode == 0, r.stderr

    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.DECLARED.value
    assert len(meta["referenceTags"]) == 2, "both declared comparators have to be serving"

    # The one-comparator panel over the same counts also serves, so nothing here turns on the count.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv")).returncode == 0
    assert any(state == "bound" for state in _states(wide_bed).values())


def test_the_bed_panel_without_a_declared_comparator_serves_as_its_own(wide_bed):
    # The bed's panel is eight antigens, below the shipped minimum of twenty-five, so the rung is asked
    # for explicitly here. What this test is about is what the rung DOES once it serves -- which
    # comparator it builds, and that the minimum count spares only a declared one.
    shape = _bed_shape(wide_bed)
    stands_in = ["--reference-source", "panel", "--panel-min-members", str(len(shape["antigens"]))]

    r = _run(wide_bed, *_bed_args("panel.csv", *stands_in))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    states = set(_states(wide_bed).values())
    assert states != {"unreliable"}, "the panel could serve as its own comparator and was not asked to"
    without = meta["readingsFloored"]

    # The floor spares a comparator's reading, and only a declared comparator has one to spare. With no
    # declaration c08's comparator reading of 1 is floored like any other count.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv", *stands_in)).returncode == 0
    with_declared = json.loads((wide_bed / "result_run_meta.json").read_text())["readingsFloored"]
    assert without > with_declared > 0


def test_a_panel_of_this_size_no_longer_stands_in_for_its_own_comparator(wide_bed):
    # The shipped minimum answers the bed's own panel, with nothing asked for. Eight antigens is under
    # twenty-five, so the panel cannot be its own background.
    #
    # This is what the minimum moving from 8 to 25 changed: an antibody kit caps at fifteen tags, so no
    # such panel reaches this rung. Such a run asks for the tag-distribution rung instead.
    shape = _bed_shape(wide_bed)
    assert len(shape["antigens"]) < DEFAULT_PANEL_MIN_MEMBERS, "the bed grew past the minimum"

    r = _run(wide_bed, *_bed_args("panel.csv", "--reference-source", "panel"), expect_failure=True)
    assert r.returncode != 0
    assert f"below the {DEFAULT_PANEL_MIN_MEMBERS} that rung needs" in r.stderr
    # Refused before anything is read, rather than answered with a punchcard where every asked position
    # reads unreliable -- honest and useless, costing what a real run costs.
    assert not (wide_bed / "result_verdicts.csv").exists()


def test_a_reading_from_a_sample_that_never_offered_it_is_not_a_vote(bed):
    # The denominator counts only members whose OWN sample offered the identity. If the numerator does
    # not apply the same test, the two are drawn from different populations. Reachable whenever a
    # sample-keyed panel meets a set spanning two samples and a tag declared for one sample is read in
    # the other.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,Ctrl,CTRL,Control\nS2,Ctrl,CTRL,Control\nS2,AgX,XXXX,Target\n"
    )
    (bed / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,CTRL,6\nS1,c1,XXXX,500\n"  # S1 never offered XXXX; this is not a vote
        "S2,c2,CTRL,6\nS2,c2,XXXX,500\n"  # offered and bound
        "S2,c3,CTRL,6\n"  # offered and silent -> not bound
    )
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c2,K1\nS2,c3,K1\n")

    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    row = (
        pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
        .filter(pl.col("identity") == "XXXX")
        .row(0, named=True)
    )
    # One bound and one not-bound among the two cells that were actually asked.
    assert row["state"] == "unreliable"
    assert row["unreliableReason"] == "tie"
    assert (row["cellsAsked"], row["cellsAnswered"]) == ("2", "2")


def test_every_asked_cell_reading_still_counts_when_both_samples_offered_it(bed):
    # The guard above must not throw away legitimate cross-sample votes: with both samples offering the
    # identity, all three cells vote as before.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,Ctrl,CTRL,Control\nS1,AgX,XXXX,Target\nS2,Ctrl,CTRL,Control\nS2,AgX,XXXX,Target\n"
    )
    (bed / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,CTRL,6\nS1,c1,XXXX,500\nS2,c2,CTRL,6\nS2,c2,XXXX,500\nS2,c3,CTRL,6\n"
    )
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c2,K1\nS2,c3,K1\n")

    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    row = (
        pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
        .filter(pl.col("identity") == "XXXX")
        .row(0, named=True)
    )
    assert (row["cellsAsked"], row["cellsAnswered"]) == ("3", "3")
    assert row["state"] == "bound"  # two bound against one silent


def test_no_qc_row_carries_a_null_panel_key(bed):
    # panelId is an AXIS of the imported QC frame, and a null is not a usable p-column key. Sample-level
    # and capture-level rows belong to no panel, so they carry an empty string.
    _run(bed, *BASE, "--capture-map", json.dumps({"S1": "C1"}))
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    assert qc["panelId"].null_count() == 0

    # Both kinds must be present, or the assertion above proves nothing.
    panels = set(qc["panelId"].to_list())
    assert "" in panels, "sample and capture rows belong to no panel and must carry an empty key"
    assert any(p for p in panels), "tag and identity rows must carry a real panel id"


# --- the shape a real panel file arrives in -----------------------------------------------------
#
# Every bed above declares a role column. A panel file observed in the field carries three columns and
# no fourth: the sample, the barcode sequence, and the antigen's name. There is no role column to
# point `--role-column` at, so the declared rung is not reachable on it and the panel's own readings
# have to serve. It also reuses a barcode between samples under a different antigen name.
#
# These tests fix what that file does today. They deliberately do NOT assert what a set spanning two
# samples should carry for a barcode that names two different antigens -- that question is open.

# Twenty-six, against a shipped minimum of twenty-five. The count is the only thing this list carries
# that the panel rung cares about. Every test below reads its members by position. Padded to two
# digits so the sorted identity list matches the list order.
CUSTOMER_TAGS = [f"SEQ{i:02d}" for i in range(1, 27)]


def _customer_bed(root, *, renamed=2, span_samples=True):
    """A three-column panel: sample, sequence, antigen. No role column, no grouping column.

    `renamed` barcodes carry a different antigen name in the second sample. `span_samples` puts every
    cell in one clonotype set, so the set's cells come from both panels.
    """
    rows = ["Sample,Sequence,Antigen"]
    for sample, offset in (("SmpA", 0), ("SmpB", 100)):
        for i, tag in enumerate(CUSTOMER_TAGS):
            name = f"Ag{offset + i:03d}" if i < renamed else f"Ag{i:03d}"
            rows.append(f"{sample},{tag},{name}")
    (root / "panel.csv").write_text("\n".join(rows) + "\n")

    # SEQ01 is strong. The rest sit at 10, above the shipped floor of 4 so nothing is floored away and the
    # panel median stays a real number.
    counts = ["sampleId,cellId,tag,umiCount"]
    linker = ["sampleId,cellId,setId"]
    for sample in ("SmpA", "SmpB"):
        for cell in ("c1", "c2", "c3"):
            counts.append(f"{sample},{cell},{CUSTOMER_TAGS[0]},900")
            counts.extend(f"{sample},{cell},{t},10" for t in CUSTOMER_TAGS[1:])
            linker.append(f"{sample},{cell},{'K1' if span_samples else 'K' + sample}")
    (root / "counts.csv").write_text("\n".join(counts) + "\n")
    (root / "linker.csv").write_text("\n".join(linker) + "\n")
    return root


CUSTOMER_ARGS = [
    "counts.csv",
    "panel.csv",
    "--linker",
    "linker.csv",
    "--barcode-col",
    "Sequence",
    "--feature-col",
    "Antigen",
    "--sample-col",
    "Sample",
    # This bed's panel carries no role column, so the rung it is about is the panel's own readings.
    "--reference-source",
    "panel",
    "--output-prefix",
    "result",
]


def test_a_panel_with_no_role_column_still_produces_verdicts(bed):
    # No --role-column and no --reference-values, because the file has no column to name. The run must
    # not fail and must not read unreliable throughout.
    _customer_bed(bed)
    r = _run(bed, *CUSTOMER_ARGS)
    assert r.returncode == 0, r.stderr

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    assert meta["referenceValues"] == [], "nothing can be declared without a role column"
    # With no grouping column either, every barcode is its own identity.
    assert meta["identities"] == CUSTOMER_TAGS

    states = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    assert set(states["state"].to_list()) != {"unreliable"}, "the panel could serve and was not asked to"
    # The strong barcode reads bound and the background does not, or the bed cannot tell a working
    # comparator from a broken one.
    by_identity = dict(zip(states["identity"].to_list(), states["state"].to_list(), strict=True))
    assert by_identity[CUSTOMER_TAGS[0]] == "bound"
    assert {by_identity[t] for t in CUSTOMER_TAGS[1:]} == {"not bound"}


def test_a_barcode_renamed_between_samples_is_labelled_with_both_names(bed):
    # A barcode the two samples name differently carries no agreed antigen name. Rather than stand under
    # the raw 15-mer it carries the names it DID declare, joined. This is the per-tag grouping: the panel
    # has no grouping column at all.
    _customer_bed(bed, renamed=2)
    assert _run(bed, *CUSTOMER_ARGS).returncode == 0

    labels = pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0)
    by_identity = dict(zip(labels["identity"].to_list(), labels["label"].to_list(), strict=True))

    # The bed names tag i "Ag00i" in the first sample and "Ag10i" in the second, for the first two of
    # them, so the joined label carries both in sorted order.
    for i, tag in enumerate(CUSTOMER_TAGS[:2]):
        assert by_identity[tag] == f"Ag{i:03d} / Ag{100 + i:03d}", f"{tag} disagrees across samples"
    # The consistently-named barcodes keep their plain antigen name. Without this the assertion above
    # would also pass on a build that had started joining names for every identity.
    for i, tag in enumerate(CUSTOMER_TAGS[2:], start=2):
        assert by_identity[tag] == f"Ag{i:03d}", f"{tag} agrees across samples and must show its name"


def test_two_barcodes_disagreeing_about_the_same_pair_of_names_stay_tellable_apart(bed):
    # The uniqueness promise applies to the joined labels too. Two barcodes can disagree about the SAME
    # pair of names, which joins to one string. The per-tag path needs nothing added: its existing
    # collision rule appends the barcode to any label that repeats, joined or plain.
    _customer_bed(bed, renamed=0)
    rows = ["Sample,Sequence,Antigen"]
    for sample, name in (("SmpA", "Shared"), ("SmpB", "Conflict")):
        rows.extend(f"{sample},{tag},{name}" for tag in CUSTOMER_TAGS[:2])
        rows.extend(f"{sample},{tag},Ag{i:03d}" for i, tag in enumerate(CUSTOMER_TAGS[2:], start=2))
    (bed / "panel.csv").write_text("\n".join(rows) + "\n")

    assert _run(bed, *CUSTOMER_ARGS).returncode == 0
    labels = pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0)
    by_identity = dict(zip(labels["identity"].to_list(), labels["label"].to_list(), strict=True))

    for tag in CUSTOMER_TAGS[:2]:
        assert by_identity[tag] == f"Conflict / Shared ({tag})", tag
    # The point of the appending, stated as the property it protects rather than as the strings above.
    assert len(set(by_identity.values())) == len(by_identity), "two identities share a label"


def test_the_label_fallback_is_caused_by_the_disagreement_and_nothing_else(bed):
    # Same bed with the renaming removed: every barcode now agrees across both samples, so no label falls
    # back. This is what makes the previous test a statement about disagreement.
    _customer_bed(bed, renamed=0)
    assert _run(bed, *CUSTOMER_ARGS).returncode == 0

    labels = pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0)
    fell_back = [
        identity
        for identity, label in zip(labels["identity"].to_list(), labels["label"].to_list(), strict=True)
        if identity == label
    ]
    assert fell_back == [], "no barcode disagrees here, so no label should fall back"


# --- the two shapes, run against the committed bed ----------------------------------------------
#
# The three tests further up use an inline bed. These two run the committed bed's own projections of
# the same slots, so they can be compared against each other and against the four-column panels.

NARROW_COLS = ["--barcode-col", "Sequence", "--feature-col", "Antigen", "--sample-col", "Sample"]
WIDE_COLS = ["--barcode-col", "Sequence", "--feature-col", "Name", "--sample-col", "Samples"]

# The seven-column bed is nine antigens, under the shipped minimum of twenty-five, so a run that wants
# the panel rung asks for it and lowers the minimum. Neither number is the subject of the tests below
# -- they are about the ROLE column -- but the CLI requires a rung to be named.
WIDE_PANEL_RUNG = ["--reference-source", "panel", "--panel-min-members", "9"]
WIDE_DECLARED_RUNG = ["--reference-source", "declared"]


def _wide_roles(bed):
    """tag -> the set of Type values it is declared with, from the seven-column panel."""
    panel = pl.read_csv(bed / "panel_wide.csv", infer_schema_length=0)
    roles: dict[str, set[str]] = {}
    for row in panel.iter_rows(named=True):
        roles.setdefault(row["Sequence"], set()).add(row["Type"])
    return roles


def test_the_narrow_shape_labels_every_barcode_the_samples_name_differently_with_both_names(wide_bed):
    # No role column, so the panel's own readings serve and the grouping is the per-tag one. The panel is
    # below the shipped minimum, so the rung is asked for explicitly: this test is about the LABEL a
    # barcode gets, and it needs a run that produced verdicts.
    #
    # A barcode two samples name differently has no agreed name, and its label must never fall through to
    # the raw 15-mer. It carries the names it DID declare, joined.
    narrow_size = pl.read_csv(wide_bed / "panel_narrow.csv", infer_schema_length=0)["Sequence"].n_unique()
    r = _run(
        wide_bed,
        "counts.csv",
        "panel_narrow.csv",
        "--linker",
        "linker.csv",
        *NARROW_COLS,
        "--reference-source",
        "panel",
        "--panel-min-members",
        str(narrow_size),
        "--output-prefix",
        "result",
    )
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    assert meta["groupingId"] == "per-tag", "this test is about the per-tag label path"

    # Read from the narrow panel itself rather than from the bed helper: its OWN feature column supplies
    # the label here, and deriving keeps a bed regenerated under another seed asserting the same shape.
    narrow = pl.read_csv(wide_bed / "panel_narrow.csv", infer_schema_length=0)
    declared: dict[str, set[str]] = {}
    for row in narrow.iter_rows(named=True):
        if row["Antigen"] and row["Antigen"].strip():
            declared.setdefault(row["Sequence"], set()).add(row["Antigen"].strip())
    renamed = {t for t, names in declared.items() if len(names) > 1}
    assert renamed, "the bed must rename at least one barcode or this test asserts nothing"

    labels = dict(pl.read_csv(wide_bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    assert len(renamed) < len(labels), "and must not rename all of them"
    for tag in renamed:
        assert labels[tag] == " / ".join(sorted(declared[tag])), tag
    # And nothing is left standing under a bare barcode: every identity here was named by the panel,
    # whether the samples agreed about the name or not.
    assert {i for i, label in labels.items() if i == label} == set()


def test_naming_the_off_target_role_as_the_comparator_deletes_the_off_target_questions(wide_bed):
    # The role column says what a member is TO THE QUESTION. The comparator is a different axis. Naming
    # the off-target role as the comparator does not merely move a baseline -- reference tags are held out
    # of the identity universe, so the off-targets stop being asked about at all.
    #
    # The role value below marks exactly ONE tag: this version reads counts against one baseline tag or
    # none, and what is demonstrated here is the ROLE axis, not how several comparators combine.
    roles = _wide_roles(wide_bed)
    by_value: dict[str, set[str]] = {}
    for tag, values in roles.items():
        if len(values) == 1:
            by_value.setdefault(next(iter(values)), set()).add(tag)
    single = next(v for v, tags in sorted(by_value.items()) if len(tags) == 1 and "off-target" in v.lower())
    off_target = by_value[single]

    assert (
        _run(
            wide_bed,
            "counts.csv",
            "panel_wide.csv",
            "--linker",
            "linker.csv",
            *WIDE_COLS,
            *WIDE_PANEL_RUNG,
            "--output-prefix",
            "plain",
        ).returncode
        == 0
    )
    asked_without = {identity for _, identity in _states_prefix(wide_bed, "plain")}

    assert (
        _run(
            wide_bed,
            "counts.csv",
            "panel_wide.csv",
            "--linker",
            "linker.csv",
            *WIDE_COLS,
            *WIDE_DECLARED_RUNG,
            "--role-column",
            "Type",
            "--reference-values",
            single,
            "--output-prefix",
            "named",
        ).returncode
        == 0
    )
    asked_with = {identity for _, identity in _states_prefix(wide_bed, "named")}

    # Without the naming they are questions. With it they are gone.
    assert off_target <= asked_without, "an off-target is an identity when nothing names it a comparator"
    assert not (off_target & asked_with), "naming the role deleted the off-target questions"
    assert asked_with, "and must not delete every question, or the bed says nothing about which went"


def test_a_role_value_differing_only_in_case_is_not_matched(wide_bed):
    # The observed file held six Type values that were three roles. A tag whose role is spelled
    # `Off-target` is not selected by `Off-Target`, silently. The claim is proved by WHICH tags the run
    # ends up reading against: the run record names them, `Off-Target` marks two, and a matcher that
    # ignored case would have found three. The role column is asked for the off-target value here rather
    # than the comparator one because that is where the observed file's case variant sits.
    roles = _wide_roles(wide_bed)
    agreed = {tag: next(iter(values)) for tag, values in roles.items() if len(values) == 1}
    exact = {tag for tag, value in agreed.items() if value == "Off-Target"}
    variant = {tag for tag, value in agreed.items() if value != "Off-Target" and value.lower() == "off-target"}
    assert len(exact) > 1, "the bed must declare more than one off-target for the count to discriminate"
    assert variant, "the bed must carry a case variant of the off-target role"

    r = _run(
        wide_bed,
        "counts.csv",
        "panel_wide.csv",
        "--linker",
        "linker.csv",
        *WIDE_COLS,
        *WIDE_DECLARED_RUNG,
        "--role-column",
        "Type",
        "--reference-values",
        "Off-Target",
        "--output-prefix",
        "named",
    )
    assert r.returncode == 0, r.stderr

    matched = set(json.loads((wide_bed / "named_run_meta.json").read_text())["referenceTags"])
    assert matched == exact, "the role value must select exactly the tags spelling it exactly"
    assert not (matched & variant), "the case-variant tag was matched, and it must not be"


def _states_prefix(bed, prefix):
    v = pl.read_csv(bed / f"{prefix}_verdicts.csv", infer_schema_length=0)
    return {(r["setId"], r["identity"]) for r in v.iter_rows(named=True)}


# --- the punchcard's pivot ---------------------------------------------------------
#
# All of these run against the COMMITTED bed rather than the small inline one, and that is
# load-bearing. On the inline bed every row has cellsAnswered == cellsAsked and the panel yields
# a single identity, so swapping the two counts and shuffling the column order are both invisible.
#
# Verified by mutation: swapping the two counts, dropping the state from the value, and changing the
# separator are each caught. Dropping the `select(ordered)` that aligns the punch pivot with the state
# pivot is NOT caught and cannot be here -- polars pivots columns in order of first appearance, which
# on this bed already equals sorted order.


def _punch_bed(bed):
    r = _run(bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    return (
        pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0),
        pl.read_csv(bed / "result_identity_punch.csv", infer_schema_length=0),
    )


def test_punch_bed_can_tell_the_two_counts_apart(wide_bed):
    # The guard on the tests below. If every row answered exactly as many cells as could have, swapping
    # the two counts is undetectable and the agreement test below passes while the punch draws the wrong
    # size everywhere.
    verdicts, punch = _punch_bed(wide_bed)
    differing = verdicts.filter(pl.col("cellsAnswered") != pl.col("cellsAsked"))
    assert differing.height > 0, "bed no longer distinguishes answered from could-answer"
    assert len([c for c in punch.columns if c != "setId"]) > 1, "bed no longer has several identities"


def test_punch_pivot_agrees_with_the_long_verdicts(wide_bed):
    # The punch cell is the only place its facts meet, so this is the one check that they are the SAME
    # facts the long frame carries. Every field is listed here on purpose: adding one to the value has to
    # break this test, or the value's shape would be free to drift from the frame it is built from.
    verdicts, punch = _punch_bed(wide_bed)
    identities = sorted(set(verdicts["identity"].to_list()))
    assert punch.columns == ["setId", *identities]

    expected = {
        (r["setId"], r["identity"]): "|".join(
            [
                r["state"],
                r["cellsAnswered"],
                r["cellsAsked"],
                r["agreement"] or "",
                r["unreliableReason"] or "",
                r["cellsBound"],
            ]
        )
        for r in verdicts.iter_rows(named=True)
    }
    for row in punch.iter_rows(named=True):
        for identity in identities:
            assert row[identity] == expected[(row["setId"], identity)], (row["setId"], identity)


def test_punch_pivot_keys_and_order_match_the_state_pivot(wide_bed):
    # Both pivots are gated together and ordered together: the punchcard reads one and lead selection
    # reads the other, and a reader comparing them must not meet a set or an identity present in one and
    # absent from the other, or in a different column order.
    _punch_bed(wide_bed)
    states = pl.read_csv(wide_bed / "result_identity_summary.csv", infer_schema_length=0)
    punch = pl.read_csv(wide_bed / "result_identity_punch.csv", infer_schema_length=0)
    assert states.columns == punch.columns
    assert states["setId"].to_list() == punch["setId"].to_list()


def test_punch_state_is_the_state_the_long_frame_gives(wide_bed):
    # The state is the half of the cell that carries the answer, so it is asserted on its own: a punch
    # whose counts are right and whose state is another identity's would still draw a glyph.
    verdicts, punch = _punch_bed(wide_bed)
    by_key = {(r["setId"], r["identity"]): r["state"] for r in verdicts.iter_rows(named=True)}
    for row in punch.iter_rows(named=True):
        for identity in [c for c in punch.columns if c != "setId"]:
            assert row[identity].split("|")[0] == by_key[(row["setId"], identity)]


def test_cell_scalars_pairs_each_cell_with_its_own_admissibility(tmp_path):
    """Every cell's admissibility must be ITS OWN, not the row next to it.

    The frame this comes from is built in one order and then joined twice before the admissibility column
    is attached. Polars does not promise a left frame's row order survives a join (`maintain_order`
    defaults to "none"), so a positional attach can hand cells each other's labels -- and because the file
    is sorted on write, nothing downstream can tell. The keyed assertion below is what makes the pairing
    observable at all.

    Two distinct labels appear, which is every label this bed can reach: `admissible` and `cell set aside
    by the admissibility gate`. `no comparator for this cell` is unreachable here, because with a declared
    comparator `reference_by_cell` zero-fills every analysed cell it read nothing for.

    Three of the four cells carry one label and one carries the other, so any permutation that moves the
    gated label is still caught.
    """
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,ok1,AAAA,500\nS1,ok1,CTRL,6\n"  # comparable
        "S1,ok2,AAAA,500\nS1,ok2,CTRL,6\n"  # comparable
        "S1,low,AAAA,500\nS1,low,CTRL,1\n"  # a very low comparator -- still compared, still admissible
        "S1,hi,AAAA,500\nS1,hi,CTRL,400\n"  # comparator above the gate -> set aside
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,ok1,K1\nS1,ok2,K1\nS1,low,K2\nS1,hi,K2\n")
    _run(tmp_path, *BASE, "--gate-threshold", "100")

    scalars = pl.read_csv(tmp_path / "result_cell_scalars.csv", infer_schema_length=0)
    by_cell = {r["cellId"]: r["admissibility"] for r in scalars.iter_rows(named=True)}
    assert by_cell == {
        "ok1": "admissible",
        "ok2": "admissible",
        "low": "admissible",
        "hi": "cell set aside by the admissibility gate",
    }


# --- the panel's sample column is written in LABELS ---------------------------------
#
# Every other bed in this file uses one string on both sides: the panel's sample value IS the counts'
# sampleId. That coincidence hid a defect that made every real run answer *never asked* everywhere --
# the panel file a scientist uploads names samples the way they do ("donor01"), while counts, linker
# and every emitted axis are keyed by the platform's opaque sampleId.
#
# The two beds below differ from each other ONLY in whether the two sides share a namespace.

OPAQUE = "3CXWCXJ3RU3UQD22B72OYXWL"


@pytest.fixture
def labelled_bed(tmp_path):
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        f"{OPAQUE},c1,AAAA,500\n{OPAQUE},c1,CTRL,6\n"
        f"{OPAQUE},c2,AAAA,500\n{OPAQUE},c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\ndonor01,AgA,AAAA,Target\ndonor01,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text(f"sampleId,cellId,setId\n{OPAQUE},c1,K1\n{OPAQUE},c2,K1\n")
    return tmp_path


def _distinct_states(bed):
    """The set of states a run produced. Deliberately not named `_states`, which already exists in this
    file and returns a per-key mapping."""
    v = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    return set(v["state"].to_list())


def test_a_label_map_joins_the_panel_to_the_counts(labelled_bed):
    # The fix: the run is told which sampleId each label belongs to, so the panel's declarations reach the
    # cells they were written for.
    _run(labelled_bed, *BASE, "--sample-labels", json.dumps({OPAQUE: "donor01"}))
    assert _distinct_states(labelled_bed) == {"bound"}


def test_without_the_map_a_labelled_panel_offers_nothing(labelled_bed):
    # The defect, pinned so it cannot come back silently. Not a claim that the behaviour is right -- it is
    # the observable shape of the failure.
    _run(labelled_bed, *BASE)
    assert _distinct_states(labelled_bed) == {"never asked"}


def test_a_panel_already_keyed_by_sample_id_is_unaffected(bed):
    # The map must not become mandatory: a panel whose sample values already ARE sampleIds is the case
    # every other bed here exercises.
    _run(bed, *BASE)
    without = _distinct_states(bed)
    _run(bed, *BASE, "--sample-labels", json.dumps({"someone-else": "unrelated"}))
    assert _distinct_states(bed) == without


def test_a_barcode_named_differently_per_sample_becomes_one_identity_per_name(tmp_path):
    """A reused barcode is placed under each name its own sample declared.

    Grouping by a property makes the identity the property's value. The panel declares per tag AND
    sample, so a barcode carrying one name here and another there is not a tag that "has nothing to group
    on" -- it is a reagent identifier reused to cover more antigens than the study has tags.

    AAAA is named differently across the two samples and CCCC is not, so the same run shows both cases.

    Do not invert this back. AAAA must not stand alone under its raw sequence, labelled with the two names
    joined, nor be reported in `tagsWithoutGroupingValue`. That shape is forced only by a dataset-wide
    tag->identity map, which cannot hold two declarations for one barcode.
    """
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,AAAA,500\nS1,c1,CCCC,7\nS1,c1,CTRL,6\n"
        "S2,c2,AAAA,500\nS2,c2,CCCC,7\nS2,c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,SpikeWT,AAAA,Target\n"
        "S2,SpikeWT__alt,AAAA,Target\n"  # same barcode, two names -> two declarations, one per sample
        "S1,Lysozyme,CCCC,Target\n"
        "S2,Lysozyme,CCCC,Target\n"  # agrees, so it groups normally
        "S1,Ctrl,CTRL,Control\nS2,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c2,K2\n")
    _run(tmp_path, *BASE, *NAME_GROUPING)

    labels = _identities_only(tmp_path)
    assert set(labels) == {"SpikeWT", "SpikeWT__alt", "Lysozyme"}
    assert labels["SpikeWT"] == "SpikeWT"
    assert labels["SpikeWT__alt"] == "SpikeWT__alt"
    assert labels["Lysozyme"] == "Lysozyme"
    # No bare 15-mer anywhere: the barcode is no longer an identity under this grouping.
    assert "AAAA" not in labels

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["identityLabels"]["SpikeWT"] == "SpikeWT"
    assert meta["tagsWithoutGroupingValue"] == [], "nothing was left unplaceable"


# --- the exported tag -> identity linker ----------------------------------------------------


def test_the_linker_carries_every_identity_a_tag_feeds_exactly_once():
    # Many-to-many by design: under (tag, sample) grouping T1 feeds A in one sample and B in another, and
    # both pairs are real. Deliberately NOT keyed by sample -- the linker joins a tag-keyed figure to an
    # identity-keyed verdict, and neither side has a sample axis. An axis no joined table has makes the
    # join malformed rather than more precise, and label discovery rejects it.
    grouping = {("T1", "s1"): "A", ("T1", "s2"): "B", ("T2", "s1"): "A", ("T2", "s2"): "A"}
    frame = _linker_frame(grouping)
    rows = sorted(zip(frame["tag"].to_list(), frame["identity"].to_list()))
    assert rows == [("T1", "A"), ("T1", "B"), ("T2", "A")]
    # T2 feeds A in both samples and appears once. Duplicate axis keys break a grid silently: one row and
    # an ellipsis, no error anywhere.
    assert len(rows) == len(set(rows))
    assert set(frame["1"].to_list()) == {1}
    assert "sample" not in frame.columns


def test_a_global_declaration_adds_no_pair_of_its_own():
    # ANY_SAMPLE feeds the same identity everywhere, so it contributes that one pair and nothing more.
    frame = _linker_frame({("T1", ANY_SAMPLE): "A"})
    assert sorted(zip(frame["tag"].to_list(), frame["identity"].to_list())) == [("T1", "A")]


# --- a grouped-on column is a declaration by construction ------------------------------------


def test_a_grouped_on_column_travels_even_when_a_member_tag_is_reused():
    # The columns the scientist grouped on are declarations, unique by construction. Identity B exists
    # only because T1 is reused with a different Identity per sample, so tag-grain agreement drops Identity
    # for T1 -- and B carried no declaration of the very thing it was grouped on. A passed only because T2
    # happens to agree across its samples, which is luck.
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T1", "T2", "T2"],
            "sample": ["s1", "s2", "s1", "s2"],
            "Identity": ["A", "B", "A", "A"],
            "Channel": ["PE", "PE", "APC", "APC"],
        }
    )
    cols = property_columns(panel)
    props, _ = consistent_properties(panel, cols)
    grouping, _, _, declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, props, reference_tags=set()
    )
    held = _identity_properties(grouping, props, cols, declared)
    assert held["A"]["Identity"] == "A"
    assert held["B"]["Identity"] == "B"


def test_a_column_not_grouped_on_still_needs_agreement_across_the_identity_tags():
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T2"],
            "sample": ["s1", "s1"],
            "Identity": ["A", "A"],
            "Channel": ["PE", "APC"],
        }
    )
    cols = property_columns(panel)
    props, _ = consistent_properties(panel, cols)
    grouping, _, _, declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, props, reference_tags=set()
    )
    held = _identity_properties(grouping, props, cols, declared)
    assert held["A"]["Identity"] == "A"
    assert "Channel" not in held["A"], "the two member tags disagree, so Channel does not hold"


def _disagreements(inconsistent):
    """The (column -> tag -> values) map the production call site builds."""
    out: dict[str, dict[str, list[str]]] = {}
    for tag, column, values in inconsistent:
        out.setdefault(column, {})[tag] = sorted(values)
    return out


def test_a_member_that_contradicts_itself_blocks_the_property():
    """The one that inverted a real panel, and the reason `disagreed` is threaded down at all.

    T1 declares two Channels across its samples, so it has no agreed value of its own. T2 declares one.
    Before the fix T1 reached the agreement test as the empty string, was filtered out exactly like a
    member whose cell was blank, and T2 then agreed with nobody but itself.

    Measured on a real sixteen-row panel: an identity whose five member tags declared six different
    antigen names came back carrying ONE member's name, because four had contradicted themselves into
    silence. A member that contradicted itself is a disagreement, not a silence.
    """
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T1", "T2"],
            "sample": ["s1", "s2", "s1"],
            "Identity": ["A", "A", "A"],
            "Channel": ["PE", "APC", "FITC"],
        }
    )
    cols = property_columns(panel)
    props, inconsistent = consistent_properties(panel, cols)
    assert props["T1"].get("Channel") is None, "T1 must have no agreed Channel or the bed proves nothing"
    grouping, _, _, declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, props, reference_tags=set()
    )

    held = _identity_properties(grouping, props, cols, declared, _disagreements(inconsistent))
    assert "Channel" not in held["A"], "T2's Channel was reported as the identity's"

    # Without the disagreements the old answer is still reachable, which is what makes this a threading fix
    # rather than a rewrite of the agreement rule.
    assert _identity_properties(grouping, props, cols, declared)["A"]["Channel"] == "FITC"


def test_a_member_that_declares_nothing_still_does_not_block_its_neighbours():
    """The other silence, and it must keep behaving as it did.

    T1 leaves the cell blank. It never declared anything to contradict, so T2's value holds.
    """
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T2"],
            "sample": ["s1", "s1"],
            "Identity": ["A", "A"],
            "Channel": ["", "FITC"],
        }
    )
    cols = property_columns(panel)
    props, inconsistent = consistent_properties(panel, cols)
    assert inconsistent == [], "a blank cell is not a disagreement"
    grouping, _, _, declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, props, reference_tags=set()
    )
    held = _identity_properties(grouping, props, cols, declared, _disagreements(inconsistent))
    assert held["A"]["Channel"] == "FITC"


def test_a_contradicting_member_does_not_block_the_column_it_was_grouped_on():
    """Grouped-on columns are settled by construction and stay that way.

    A tag reaches an identity because of its value in the grouping column, so that value is not open to
    an agreement test -- and a reused barcode has no tag-grain agreement to test in the first place.
    """
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T1"],
            "sample": ["s1", "s2"],
            "Identity": ["A", "A"],
            "Channel": ["PE", "APC"],
        }
    )
    cols = property_columns(panel)
    props, inconsistent = consistent_properties(panel, cols)
    grouping, _, _, declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, props, reference_tags=set()
    )
    held = _identity_properties(grouping, props, cols, declared, _disagreements(inconsistent))
    assert held["A"]["Identity"] == "A"
    assert "Channel" not in held["A"]


def test_the_per_tag_grouping_declares_nothing_of_its_identities():
    # It groups on no column, so there is nothing to take by construction. Every property still travels by
    # the agreement rule.
    panel = pl.DataFrame({"tag": ["T1"], "sample": ["s1"], "Channel": ["PE"]})
    cols = property_columns(panel)
    props, _ = consistent_properties(panel, cols)
    grouping, rule_id, _, declared = _build_grouping(None, panel, props, reference_tags=set())
    assert rule_id == "per-tag"
    assert declared == {}
    held = _identity_properties(grouping, props, cols, declared)
    assert held["T1"]["Channel"] == "PE"


# --- a grouping may name several columns -----------------------------------------------------


def test_two_grouping_columns_make_the_identity_the_combination():
    # Named antigen and concentration together, the identity is the pair, and the same antigen at two
    # concentrations is two identities.
    panel = pl.DataFrame(
        {
            "tag": ["T1", "T2", "T3"],
            "sample": ["s1", "s1", "s1"],
            "Antigen": ["Spike", "Spike", "Nuc"],
            "Dose": ["low", "high", "low"],
        }
    )
    grouping, rule_id, ungrouped, declared = _build_grouping(
        {"by": "property", "columns": ["Antigen", "Dose"]}, panel, {}, reference_tags=set()
    )
    assert grouping[("T1", "s1")] == "Spike | low"
    assert grouping[("T2", "s1")] == "Spike | high"
    assert grouping[("T3", "s1")] == "Nuc | low"
    assert rule_id == "property:Antigen|Dose"
    assert ungrouped == []
    # Both grouped-on columns are declarations of the identity, and both travel.
    assert declared["Spike | high"] == {"Antigen": "Spike", "Dose": "high"}


def test_the_legacy_single_column_rule_still_works():
    # A project stored before the rule took a list carries `column`. It must keep running.
    panel = pl.DataFrame({"tag": ["T1"], "sample": ["s1"], "Antigen": ["Spike"]})
    grouping, rule_id, _, declared = _build_grouping(
        {"by": "property", "column": "Antigen"}, panel, {}, reference_tags=set()
    )
    assert grouping[("T1", "s1")] == "Spike"
    assert rule_id == "property:Antigen"
    assert declared["Spike"] == {"Antigen": "Spike"}


def test_a_blank_in_any_named_column_falls_back_to_the_tag():
    # A combination missing one component is not that combination.
    panel = pl.DataFrame({"tag": ["T1"], "sample": ["s1"], "Antigen": ["Spike"], "Dose": ["  "]})
    grouping, _, ungrouped, declared = _build_grouping(
        {"by": "property", "columns": ["Antigen", "Dose"]}, panel, {}, reference_tags=set()
    )
    assert grouping[("T1", "s1")] == "T1"
    assert ungrouped == ["T1"]
    assert "T1" not in declared, "a pair that fell back declares nothing"


def test_a_column_the_panel_does_not_declare_ends_the_run():
    panel = pl.DataFrame({"tag": ["T1"], "sample": ["s1"], "Antigen": ["Spike"]})
    with pytest.raises(SystemExit) as e:
        _build_grouping({"by": "property", "columns": ["Antigen", "Nope"]}, panel, {}, reference_tags=set())
    assert "Nope" in str(e.value)


def test_a_role_column_the_reader_consumes_as_a_key_ends_the_run_with_no_role_values(bed):
    # `Sequence` is the barcode column, so panel.py strips it before the properties are read and it is
    # never a property column. Naming it as the role column exited 0 whenever no role values came with it:
    # the check was gated on the values, so no tag was designated and the baseline fell back to the panel's
    # own readings in silence.
    r = _run(
        bed,
        "counts.csv",
        "panel.csv",
        "--linker",
        "linker.csv",
        "--barcode-col",
        "Sequence",
        "--feature-col",
        "Name",
        "--sample-col",
        "Samples",
        "--role-column",
        "Sequence",
        "--reference-source",
        "declared",
        "--output-prefix",
        "result",
        expect_failure=True,
    )
    assert "Sequence" in r.stderr


def test_a_value_carrying_the_join_separator_is_reported_and_the_run_continues(capsys):
    panel = pl.DataFrame({"tag": ["T1"], "sample": ["s1"], "Antigen": ["Spike | odd"], "Dose": ["low"]})
    grouping, _, _, _ = _build_grouping(
        {"by": "property", "columns": ["Antigen", "Dose"]}, panel, {}, reference_tags=set()
    )
    assert grouping[("T1", "s1")] == "Spike | odd | low"
    assert "may share one identity key" in capsys.readouterr().err


def test_the_set_counts_carry_the_clonotype_cell_count(bed):
    # The clonotype's own cell count goes beside its name in the grid, so the grid needs it as a column.
    # It is the set's cells, not its answering cells: it does not vary by identity.
    _run(bed, *BASE)
    counts = pl.read_csv(bed / "result_set_counts.csv", infer_schema_length=0)
    assert "cellCount" in counts.columns
    assert all(int(v) >= 1 for v in counts["cellCount"].to_list())
    # And it is not the answering count: that varies by identity, this one does not.
    verdicts = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    by_set = dict(zip(counts["setId"].to_list(), counts["cellCount"].to_list()))
    for set_id, could in zip(verdicts["setId"].to_list(), verdicts["cellsAsked"].to_list()):
        assert int(could) <= int(by_set[set_id]), "a set cannot answer with more cells than it has"


def test_set_counts_carry_the_clonotype_s_own_set_aside_cells(bed):
    # Set-aside cells are stated once for the clonotype, because a set-aside cell answers nothing at any
    # identity. Run-level is the wrong grain: the expansion is about one clonotype.
    #
    # The bed's baseline is CTRL at 6 UMIs in every cell, so a gate of 5 sets every cell aside and gives a
    # real non-zero to assert against.
    _run(bed, *BASE, "--gate-threshold", "5")
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert "cellsSetAside" in counts.columns
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert counts["cellsSetAside"].sum() == meta["cellsSetAside"]
    assert meta["cellsSetAside"] > 0, "the gate set nothing aside, so this proves nothing"


def test_set_counts_report_no_set_aside_cells_when_no_gate_is_declared(bed):
    # Off is the default. The column still has to be present and zero, so a reader never has to tell "no
    # gate" apart from "column missing".
    _run(bed, *BASE)
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert counts["cellsSetAside"].to_list() == [0] * len(counts)


def test_run_meta_carries_set_aside_cells_per_clonotype(bed):
    # Set-aside cells are stated once for the clonotype, and the expansion reads them from the run record
    # rather than from a p-column: a Parquet column's values cannot be read in the model, and a set-grain
    # number joined into the per-identity table would repeat down every row.
    #
    # The bed's baseline is CTRL at 6 UMIs in every cell, so a gate of 5 sets every cell aside.
    _run(bed, *BASE, "--gate-threshold", "5")
    meta = json.loads((bed / "result_run_meta.json").read_text())
    by_set = meta["cellsSetAsideBySet"]
    assert sum(by_set.values()) == meta["cellsSetAside"]
    assert meta["cellsSetAside"] > 0, "the gate set nothing aside, so this proves nothing"
    # Sparse: the run record is parsed on every render, so a clonotype that lost nothing carries no entry.
    # A reader takes an absent key as zero.
    assert all(n > 0 for n in by_set.values())
    # The CSV keeps its own dense rendering, and the two cannot disagree -- one helper produces both.
    counts = pl.read_csv(bed / "result_set_counts.csv")
    dense = dict(zip(counts["setId"].to_list(), counts["cellsSetAside"].to_list()))
    assert by_set == {k: v for k, v in dense.items() if v > 0}


def test_set_counts_carry_the_clonotype_s_cells_that_read_nothing(bed):
    # Carried per clonotype, not per identity: a cell with nothing left is empty at every identity, and
    # repeating the subtraction per position would report a per-identity failure that did not happen.
    #
    # At the shipped minimum nothing in this bed falls, so the column has to be present and zero rather
    # than absent.
    _run(bed, *BASE)
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert "cellsReadingNothing" in counts.columns
    assert counts["cellsReadingNothing"].to_list() == [0] * len(counts)


def test_a_cell_carrying_only_its_comparator_has_not_read_nothing(bed):
    # c3 was asked about AgA and read nothing of it, while its comparator read 6. That cell took up reagent
    # and none of it was antigen, which is a real negative and a real vote. A minimum of 7 removes its AgA
    # reading -- there is none to remove -- and leaves the exempt comparator standing.
    _run(bed, *BASE, "--floor", "7")
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert counts["cellsReadingNothing"].to_list() == [0]


def test_cells_that_read_nothing_change_no_verdict(bed):
    # Both shortcuts this number invites are forbidden: dropping such cells from the vote shrinks the
    # denominator and turns a minority into a majority, and filtering them out of the cell list is the same
    # effect by another route. Raising the minimum must change nothing else in the run.
    _run(bed, *BASE, "--floor", "1")
    low = (bed / "result_verdicts.csv").read_bytes()
    low_cells = (bed / "result_cell_counts.csv").read_bytes()
    _run(bed, *BASE, "--floor", "7")
    assert (bed / "result_verdicts.csv").read_bytes() == low
    assert (bed / "result_cell_counts.csv").read_bytes() == low_cells


def test_a_clonotype_never_reads_nothing_in_more_cells_than_it_has(bed):
    # The universe passed to the tally is the clonotype's own membership, so this cannot be violated by
    # construction -- which is why it is worth pinning: a later refactor reading the population off the
    # counts frame would break it silently.
    _run(bed, *BASE, "--floor", "7")
    counts = pl.read_csv(bed / "result_set_counts.csv")
    for empty, total in zip(counts["cellsReadingNothing"].to_list(), counts["cellCount"].to_list()):
        assert 0 <= empty <= total


def test_run_meta_omits_set_aside_cells_per_clonotype_when_no_gate_is_declared(bed):
    # The count shows only where a gate is declared. The key is ABSENT rather than an empty object, so the
    # UI branches on one thing -- was a gate declared.
    _run(bed, *BASE)
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert "cellsSetAsideBySet" not in meta


@pytest.fixture
def two_set_bed(tmp_path):
    # Two clonotypes whose cells read the comparator differently, so a gate can catch one and leave the
    # other untouched. This is the ONLY shape that can falsify a dense map: with a single clonotype, an
    # implementation that emitted every clonotype including the zeros passes every other assertion here.
    #
    # K1's cells read CTRL at 6, so a gate of 5 takes both. K2's read it at 2, so the same gate leaves both.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,AAAA,500\nS1,c1,CTRL,6\n"
        "S1,c2,AAAA,600\nS1,c2,CTRL,6\n"
        "S1,d1,AAAA,500\nS1,d1,CTRL,2\n"
        "S1,d2,AAAA,600\nS1,d2,CTRL,2\n"
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\nS1,d1,K2\nS1,d2,K2\n")
    return tmp_path


def test_run_meta_omits_a_clonotype_the_gate_did_not_touch(two_set_bed):
    # The sparseness claim, tested where it can fail. A clonotype that lost nothing carries NO entry and a
    # reader takes an absent key as zero. A map that carried `"K2": 0` would defeat that and pass every
    # relative assertion above.
    _run(two_set_bed, *BASE, "--gate-threshold", "5")
    meta = json.loads((two_set_bed / "result_run_meta.json").read_text())
    assert meta["cellsSetAsideBySet"] == {"K1": 2}
    assert "K2" not in meta["cellsSetAsideBySet"], "a clonotype the gate did not touch must be absent"
    # The CSV stays DENSE, which is its own contract: a reader of a table must never have to tell "no gate"
    # apart from "column missing".
    counts = pl.read_csv(two_set_bed / "result_set_counts.csv")
    dense = dict(zip(counts["setId"].to_list(), counts["cellsSetAside"].to_list()))
    assert dense == {"K1": 2, "K2": 0}


@pytest.fixture
def silent_position_bed(tmp_path):
    # One clonotype, two cells, two antigens, and the shape that separates "asked and silent" from "never
    # asked": c1 carries counts for both antigens, c2 carries counts for AgA only. So (c2, AgB) has no row
    # in `read_states`, and every antigen is on the sample's panel -- a SILENT position rather than an
    # unasked one. An implementation that pivots the sparse frame and stops leaves it blank.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,BBBB,400\nS1,c1,CTRL,2\nS1,c2,AAAA,600\nS1,c2,CTRL,2\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,AgB,BBBB,Target\nS1,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\n")
    return tmp_path


def _cell_punch(bed):
    frame = pl.read_csv(bed / "result_cell_punch.csv")
    return {(row["sampleId"], row["cellId"]): row for row in frame.iter_rows(named=True)}


def test_cell_punch_gives_every_cell_a_row_and_every_identity_a_column(silent_position_bed):
    _run(silent_position_bed, *BASE)
    rows = _cell_punch(silent_position_bed)
    assert set(rows) == {("S1", "c1"), ("S1", "c2")}, "one row per cell of the set"
    # The columns are the PANEL, not what this run happened to ask. The comparator is not an identity.
    for key in rows:
        assert "AAAA" in rows[key] and "BBBB" in rows[key]
        assert rows[key]["setId"] == "K1", "the set travels as a column so the readout can filter on it"


def test_cell_punch_resolves_a_silent_position_rather_than_leaving_it_blank(silent_position_bed):
    # The claim this fixture exists for. (c2, AgB) has no row in the states frame, its sample offered AgB,
    # and c2 can be compared -- so it reads NOT BOUND, exactly as silent_tally counts it.
    _run(silent_position_bed, *BASE)
    rows = _cell_punch(silent_position_bed)
    silent = rows[("S1", "c2")]["BBBB"]
    assert silent is not None, "an asked-and-silent position must not be blank"
    assert silent.split("|")[0] == "not bound", silent
    # And the position that DID carry counts reads bound, so the test is not passing on a frame where
    # everything is not-bound.
    assert rows[("S1", "c1")]["BBBB"].split("|")[0] == "bound", rows[("S1", "c1")]["BBBB"]


def test_cell_punch_counts_the_identities_a_cell_read_bound(silent_position_bed):
    _run(silent_position_bed, *BASE)
    rows = _cell_punch(silent_position_bed)
    # c1 bound both antigens. C2 bound AgA and was silent, so not bound, at AgB.
    assert rows[("S1", "c1")]["boundIdentities"] == 2
    assert rows[("S1", "c2")]["boundIdentities"] == 1


def test_cell_punch_marks_a_gated_cell_unreliable_at_every_identity(silent_position_bed):
    # A gate reading the comparator at 2 takes both cells. A gated cell was not measured at all, so no
    # position of it is bound or not bound -- and its bound count is zero rather than absent.
    _run(silent_position_bed, *BASE, "--gate-threshold", "1")
    rows = _cell_punch(silent_position_bed)
    for key, row in rows.items():
        for identity in ("AAAA", "BBBB"):
            assert row[identity].split("|")[0] == "unreliable", (key, identity, row[identity])
        assert row["boundIdentities"] == 0, key


def test_run_meta_says_whether_the_cell_punch_was_emitted(silent_position_bed):
    _run(silent_position_bed, *BASE)
    meta = json.loads((silent_position_bed / "result_run_meta.json").read_text())
    assert meta["cellPunchEmitted"] is True
    assert meta["cellPunchCells"] == 2


def test_run_meta_carries_both_gate_limits(silent_position_bed):
    # Each gate's count is only readable against its own limit. The identity limit bounds the pivot's width
    # and the cell limit bounds its rows.
    _run(silent_position_bed, *BASE)
    meta = json.loads((silent_position_bed / "result_run_meta.json").read_text())
    assert meta["identitySummaryLimit"] == IDENTITY_SUMMARY_MAX_IDENTITIES
    assert meta["cellPunchLimit"] == CELL_PUNCH_MAX_CELLS


def test_the_panel_comparator_is_built_from_raw_counts(tmp_path):
    """The production call site passes the raw frame, not the floored one.

    The unit test in test_verdict.py pins what the two frames produce. This pins which one production
    hands over, which no assertion in this file reached: every fixture bed here reads well clear of the
    minimum, so flooring changed no comparator and the suite stayed green either way.

    c1's five readings are 1, 1, 2, 9, 9. Raw they median to 2. Floored at the shipped minimum of 4 they
    are 0, 0, 0, 9, 9 and median to 0 -- which would push every verdict in that cell toward *bound*.
    """
    tags = ["AAAA", "CCCC", "GGGG", "TTTT", "ACAC"]
    (tmp_path / "panel.csv").write_text(
        "Sample,Antigen,Sequence\n" + "".join(f"S1,Ag{i},{t}\n" for i, t in enumerate(tags))
    )
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        + "".join(f"S1,c1,{t},{n}\n" for t, n in zip(tags, [1, 1, 2, 9, 9], strict=True))
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\n")

    _run(tmp_path, *CUSTOMER_ARGS, "--panel-min-members", "5")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value

    counts = pl.read_csv(tmp_path / "result_cell_counts.csv")
    refs = set(counts.filter(pl.col("cellId") == "c1")["referenceCount"].to_list())
    assert refs == {2}, "the comparator was built from floored readings"


DISTRIBUTION_ARGS = [
    "counts.csv",
    "panel.csv",
    "--linker",
    "linker.csv",
    "--barcode-col",
    "Sequence",
    "--feature-col",
    "Antigen",
    "--sample-col",
    "Sample",
    "--reference-source",
    "distribution",
    "--output-prefix",
    "result",
]


def _distribution_bed(root, n_cells=400, binder_rate=300, seed=7):
    """A sample whose first tag separates and whose second does not.

    Written from a seeded generator: the rung under test is a density, and a handful of hand-written
    counts has no density. The seed is fixed, so the bed is the same bytes on every run.

    `SEPS` binds in a twentieth of the cells. `FLAT` reads the SAME count in every cell, which is one
    population by construction and the shape that cannot be fitted at all.

    A flat tag is deliberately not a background-shaped one: a tag nothing bound still fits and still calls
    its upper tail bound, so a background-shaped tag no longer demonstrates an unfittable one. Identical
    counts do, and they are still a tag the reads carry.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    n_binders = n_cells // 20
    sep = np.concatenate([rng.poisson(2, n_cells - n_binders), rng.poisson(binder_rate, n_binders)])
    flat = np.full(n_cells, 5)

    rows = ["sampleId,cellId,tag,umiCount"]
    for i in range(n_cells):
        for tag, values in (("SEPS", sep), ("FLAT", flat)):
            if values[i] > 0:
                rows.append(f"S1,c{i},{tag},{values[i]}")
    (root / "counts.csv").write_text("\n".join(rows) + "\n")
    (root / "panel.csv").write_text("Sample,Antigen,Sequence\nS1,AgSep,SEPS\nS1,AgFlat,FLAT\n")
    (root / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,c{i},K{i % 4}\n" for i in range(n_cells)))
    # The cell list is what fixes the fit's population, including the cells that read nothing for a
    # tag. Without it the universe is only the observed cells.
    (root / "cells.csv").write_text("sampleId,cellId\n" + "".join(f"S1,c{i}\n" for i in range(n_cells)))
    return root


def test_the_tag_distribution_rung_serves_and_says_so(tmp_path):
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.DISTRIBUTION.value
    assert meta["referenceSourceRequested"] == ReferenceChoice.DISTRIBUTION.value
    assert meta["distributionMinCells"] == 300


def _distribution_bed_with_a_baseline_tag(root, n_cells=400, sticky=40, seed=7):
    """The distribution bed, plus a declared baseline tag reading high in a few cells.

    The rung's comparator is the fit. The baseline tag is here only in its other role, which is what
    the two roles are kept apart. Its readings sit either side of the gate used below, and the
    minimum never touches a baseline tag, so the low ones survive as the measurement they are.
    """
    _distribution_bed(root, n_cells=n_cells, seed=seed)
    rows = (root / "counts.csv").read_text().rstrip("\n").split("\n")
    rows += [f"S1,c{i},CTRL,{200 if i < sticky else 3}" for i in range(n_cells)]
    (root / "counts.csv").write_text("\n".join(rows) + "\n")
    (root / "panel.csv").write_text(
        "Sample,Antigen,Sequence,Role\nS1,AgSep,SEPS,Target\nS1,AgFlat,FLAT,Target\nS1,Ctrl,CTRL,Control\n"
    )
    return sticky


def _distribution_bed_with_a_short_sample(root, n_cells=400, short_cells=250, seed=7):
    """The distribution bed, plus a second sample too small for the rung to fit.

    S2 holds fewer cells than the rung needs, so no tag fits there and none of its cells has a
    comparator for any identity. One S2 cell reads SEPS so the tag is measured in that sample and the
    question was put -- every other S2 cell is SILENT for it, which is the position under test.
    """
    _distribution_bed(root, n_cells=n_cells, seed=seed)
    rows = (root / "counts.csv").read_text().rstrip("\n").split("\n")
    cells = (root / "cells.csv").read_text().rstrip("\n").split("\n")
    linker = (root / "linker.csv").read_text().rstrip("\n").split("\n")
    for i in range(short_cells):
        rows.append(f"S2,d{i},FLAT,5")
        cells.append(f"S2,d{i}")
        linker.append(f"S2,d{i},KS2")
    rows.append("S2,d0,SEPS,50")
    (root / "counts.csv").write_text("\n".join(rows) + "\n")
    (root / "cells.csv").write_text("\n".join(cells) + "\n")
    (root / "linker.csv").write_text("\n".join(linker) + "\n")
    (root / "panel.csv").write_text(
        "Sample,Antigen,Sequence\nS1,AgSep,SEPS\nS1,AgFlat,FLAT\nS2,AgSep,SEPS\nS2,AgFlat,FLAT\n"
    )


def test_cell_punch_marks_a_position_with_no_fitted_background_unreliable(tmp_path):
    # A cell whose sample the rung could not fit has no comparator for any identity, so its silent
    # positions are unreliable. They used to render *not bound*: the punchcard corrected a silent
    # position only through a per-(sample, identity) comparator, which nothing in production sets, and
    # never through the fitted rung's per-cell probabilities -- so every such position fell through to
    # the not-bound default and contradicted the set verdict above it.
    _distribution_bed_with_a_short_sample(tmp_path)
    r = _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")
    assert r.returncode == 0, r.stderr

    rows = _cell_punch(tmp_path)
    silent = rows[("S2", "d1")]["SEPS"]
    assert silent.split("|")[0] == "unreliable", silent
    assert "no comparator" in silent

    # S1 fitted, so the same identity resolves there. Without this the test would pass over a run where
    # nothing resolved anywhere.
    assert rows[("S1", "c0")]["SEPS"].split("|")[0] in ("bound", "not bound")


def test_a_declared_gate_acts_under_the_tag_distribution_rung(tmp_path):
    # The gate reads a declared baseline tag; the comparator is whatever rung was selected. They are
    # separate roles, so which rung serves must not reach the gate. It used to: the fitted rung handed
    # `gate_cells` an empty reading map, so a stored threshold set nothing aside and reported nothing,
    # silently, from the moment a scientist switched the baseline source.
    sticky = _distribution_bed_with_a_baseline_tag(tmp_path)
    r = _run(
        tmp_path,
        *DISTRIBUTION_ARGS,
        "--cells",
        "cells.csv",
        "--role-column",
        "Role",
        "--reference-values",
        "Control",
        "--gate-threshold",
        "100",
    )
    assert r.returncode == 0, r.stderr

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.DISTRIBUTION.value, "the comparator is still the fit"
    assert meta["cellsSetAside"] == sticky, "the gate has to have acted"

    # And the exposure is a count rather than a reason, because the population now exists.
    qc = pl.read_csv(tmp_path / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "highReferenceCells").row(0, named=True)
    assert float(row["value"]) == sticky
    assert "gate=100" in row["detail"]


def test_the_sticky_measurement_says_why_where_no_cell_carries_a_baseline_reading(tmp_path):
    # Both forms of this measurement read a cell's own baseline reading, which only a declared baseline
    # tag supplies. Under the tag-distribution rung no cell has one, so a gated count is taken over an
    # empty population and comes out 0.0 -- reporting the sample as checked and clean on a question the
    # run never asked. The run record already reports None for the same condition, so a zero here also
    # makes the two artefacts disagree.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv", "--gate-threshold", "50")

    qc = pl.read_csv(tmp_path / "result_qc.csv", infer_schema_length=0)
    rows = qc.filter(pl.col("measurement") == "highReferenceCells")
    assert rows.height > 0, "the measurement keeps its row whether or not the run could compute it"
    for row in rows.iter_rows(named=True):
        assert not row["value"], "a zero would read as a sample carrying no sticky cells"
        assert row["reason"] == "no cell in this sample carries a comparator reading"
        assert row["detail"] == "cellsWithAComparator=0"


@pytest.fixture
def ambient_bed(tmp_path, n_cells=3, n_ambient=300, cell_count=40):
    """Three cells reading a tag properly, beside a crowd of ambient barcodes reading it once.

    The shape every droplet run has: ambient reagent reaches most barcodes, so observed barcodes
    outnumber cells by one to two orders of magnitude while carrying one or two counts each. Which of
    those barcodes held a cell is an input, and the cell list carries it.
    """
    counts = ["sampleId,cellId,tag,umiCount"]
    cells = ["sampleId,cellId"]
    linker = ["sampleId,cellId,setId"]
    for i in range(n_cells):
        counts += [f"S1,c{i},AAAA,{cell_count}", f"S1,c{i},CTRL,6"]
        cells.append(f"S1,c{i}")
        linker.append(f"S1,c{i},K1")
    counts += [f"S1,amb{i},AAAA,1" for i in range(n_ambient)]
    (tmp_path / "counts.csv").write_text("\n".join(counts) + "\n")
    (tmp_path / "cells.csv").write_text("\n".join(cells) + "\n")
    (tmp_path / "linker.csv").write_text("\n".join(linker) + "\n")
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    return tmp_path


def test_the_reagent_figures_count_cells_rather_than_observed_barcodes(ambient_bed):
    # The reagent table's two count columns and its median are about CELLS. Taken over every observed
    # barcode instead, ambient droplets set the median -- and a median below the minimum is how this
    # table reports a reagent delivering under the level at which anything is credited, so every tag in
    # the panel reads as a failed reagent.
    _run(ambient_bed, *BASE, "--cells", "cells.csv")
    qc = pl.read_csv(ambient_bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter((pl.col("measurement") == "perAntigen") & (pl.col("entity") == "AAAA")).row(0, named=True)

    assert "cellsWithCount=3" in row["detail"], row["detail"]
    assert "medianCountPerCell=40.0" in row["detail"], row["detail"]
    # And the figure says which list it was computed against, since two runs whose lists came from
    # different sources do not share a denominator.
    assert "cellList=cell list" in row["detail"], row["detail"]


def test_the_reagent_figures_fall_back_to_the_linker_as_the_cell_list(ambient_bed):
    # With no `--cells` the clonotype linker supplies the list, which is the narrower of the two sources
    # and the ordinary case for this block. The figures are scoped to it and say so, so a reader can
    # tell a run counted against one list from a run counted against the other.
    _run(ambient_bed, *BASE)
    qc = pl.read_csv(ambient_bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter((pl.col("measurement") == "perAntigen") & (pl.col("entity") == "AAAA")).row(0, named=True)

    assert "cellsWithCount=3" in row["detail"], row["detail"]
    assert "cellList=clonotype linker" in row["detail"], row["detail"]


def test_the_sticky_measurement_is_a_spread_when_no_gate_is_declared(bed):
    # The default, and therefore the first run every scientist sees. Where no threshold is declared there
    # is no *high* to count, and the measurement is the distribution of those readings instead -- which is
    # what a scientist reads in order to declare a gate.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "highReferenceCells").row(0, named=True)

    assert "noGateDeclared" in row["detail"]
    assert "gate=" not in row["detail"]
    # Eleven decile points ride in the detail, and the value is their median.
    points = [p.split(":")[0] for p in row["detail"].split("|")[2:]]
    assert points == [str(p) for p in range(0, 101, 10)]


def test_the_sticky_measurement_counts_the_cells_the_gate_set_aside(bed):
    # With a gate declared the two jobs are one number: the cells counted high are the cells set aside, by
    # construction. A second line used to let those two sets differ.
    _run(bed, *BASE, "--gate-threshold", "1")
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "highReferenceCells").row(0, named=True)

    assert "gate=1" in row["detail"]
    assert "noGateDeclared" not in row["detail"]
    meta = json.loads((bed / "result_run_meta.json").read_text())
    # Same cells, counted once. The per-sample rows sum to the run's set-aside total.
    per_sample = qc.filter(pl.col("measurement") == "highReferenceCells")["value"].to_list()
    assert sum(int(float(v)) for v in per_sample if v is not None) == meta["cellsSetAside"]


def test_no_observation_line_parameter_survives(bed):
    # One threshold, not two. The parameter set lists seven parameters and a sticky line is not among
    # them, so a run must not accept one.
    assert "--high-reference-line" not in _run(bed, "--help").stdout
    meta_run = _run(bed, *BASE)
    assert meta_run.returncode == 0
    assert "highReferenceLine" not in json.loads((bed / "result_run_meta.json").read_text())


def test_the_distributions_are_emitted_as_plottable_frames(bed):
    # Three distributions go last in the readout, and a scientist settles the cutoff and the gate by
    # looking at them. A decile encoded inside a measurement's detail string is a number nobody can plot.
    _run(bed, *BASE)

    deciles = pl.read_csv(bed / "result_qc_deciles.csv", infer_schema_length=0)
    kinds = set(deciles["distribution"].to_list())
    assert kinds == {"score", "referenceReading"}
    for kind in kinds:
        points = deciles.filter(pl.col("distribution") == kind)["decile"].to_list()
        assert [int(p) for p in points] == list(range(0, 101, 10)), kind

    # Header-only rather than absent where a run fitted no background: a consumer meeting a header knows
    # the step ran and found nothing.
    backgrounds = pl.read_csv(bed / "result_qc_backgrounds.csv", infer_schema_length=0)
    assert backgrounds.columns == [
        "sampleId",
        "tag",
        "backgroundMean",
        "signalMean",
        "backgroundWeight",
    ]
    assert backgrounds.height == 0, "a declared baseline fits no background"


def test_the_spreads_are_taken_over_the_cell_list_not_over_observed_barcodes(bed):
    # The cutoff and the gate act on cells, and the count plots beside these two on the same page are
    # already narrowed to the cell list. In droplet data observed barcodes outnumber cells by one to two
    # orders of magnitude. `zzz` is observed and unlisted, and its reference reading of 999 is the only
    # one in the run that is not 6, so an unnarrowed spread cannot pass.
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,zzz,AAAA,7\nS1,zzz,CTRL,999\n")
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["cellsInList"] == 3 and meta["cellsAnalysed"] == 4, "the bed no longer separates the two"

    bins = json.loads((bed / "result_qc_tag_bins.json").read_text())
    # Two scored positions, not three: a silent admissible cell carries no row, so c3 never reaches the
    # score spread. Unnarrowed this would be three, c1 and c2 plus zzz.
    assert sum(bins["spreads"]["score"]["weights"]) == 2
    # Three readings: every listed cell carries a comparator whether or not it read an antigen.
    assert sum(bins["spreads"]["referenceReading"]["weights"]) == 3

    deciles = pl.read_csv(bed / "result_qc_deciles.csv", infer_schema_length=0)
    readings = deciles.filter(pl.col("distribution") == "referenceReading")["value"].to_list()
    assert {float(v) for v in readings} == {6.0}, readings


@pytest.fixture
def sample_decile_bed(tmp_path):
    # Three samples: S1 and S2 each hold cells with an antigen count, at different scales so their decile
    # series cannot coincide by accident. S3 is declared in the panel but carries no counted reading.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,AAAA,500\nS1,c1,CTRL,6\nS1,c2,AAAA,900\nS1,c2,CTRL,6\n"
        "S2,c1,AAAA,50\nS2,c1,CTRL,6\nS2,c2,AAAA,80\nS2,c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n"
        "S2,AgA,AAAA,Target\nS2,Ctrl,CTRL,Control\n"
        "S3,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\nS2,c1,K2\nS2,c2,K2\n")
    return tmp_path


def test_sample_deciles_reach_a_frame_keyed_by_sample(sample_decile_bed):
    # The distribution's deciles must reach a p-frame keyed by sample, not only a decile string buried in
    # the measurement's detail field.
    _run(sample_decile_bed, *BASE)
    deciles = pl.read_csv(sample_decile_bed / "result_qc_sample_deciles.csv", infer_schema_length=0)
    assert set(deciles.columns) == {"sampleId", "decile", "value"}
    assert set(deciles.filter(pl.col("sampleId") == "S1")["decile"].to_list()) == set(str(p) for p in range(0, 101, 10))


def test_two_samples_carry_different_decile_series(sample_decile_bed):
    # S1's cells hold 500-900 antigen counts, S2's hold 50-80. Their decile series must differ -- a plot
    # showing this sample alone is the point.
    _run(sample_decile_bed, *BASE)
    deciles = pl.read_csv(sample_decile_bed / "result_qc_sample_deciles.csv")
    s1 = deciles.filter(pl.col("sampleId") == "S1").sort("decile")["value"].to_list()
    s2 = deciles.filter(pl.col("sampleId") == "S2").sort("decile")["value"].to_list()
    assert s1 != s2
    assert max(s1) > max(s2)


def test_a_sample_with_no_antigen_counts_yields_no_decile_rows(sample_decile_bed):
    # S3 is declared in the panel but no read ever carried a count for it. A flat run of zeros would read
    # as a real, narrow distribution; the right answer is no rows for S3 at all, with the sample's own
    # measurement carrying the reason instead.
    _run(sample_decile_bed, *BASE)
    deciles = pl.read_csv(sample_decile_bed / "result_qc_sample_deciles.csv", infer_schema_length=0)
    assert deciles.filter(pl.col("sampleId") == "S3").height == 0

    qc = pl.read_csv(sample_decile_bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter((pl.col("entity") == "S3") & (pl.col("measurement") == "antigenCountDistribution")).row(
        0, named=True
    )
    assert row["value"] is None or row["value"] == ""
    assert row["reason"]


def test_the_fitted_backgrounds_are_emitted_at_the_fits_own_grain(tmp_path):
    # One row per (sample, tag) the fit scored. Aggregating to the tag would hide a reagent that separated
    # in one sample and not in another.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    backgrounds = pl.read_csv(tmp_path / "result_qc_backgrounds.csv")
    # SEPS fits, FLAT does not, so exactly one pair contributes.
    assert backgrounds.height == 1
    row = backgrounds.row(0, named=True)
    assert row["tag"] == "SEPS"
    assert row["backgroundMean"] < row["signalMean"]
    assert 0.0 < row["backgroundWeight"] < 1.0


def test_a_population_baseline_emits_no_score_deciles(tmp_path):
    # No score exists under that rung, so the frame carries the reference-reading rows and nothing claiming
    # to be a score.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    deciles = pl.read_csv(tmp_path / "result_qc_deciles.csv", infer_schema_length=0)
    assert "score" not in set(deciles["distribution"].to_list())


def test_the_run_carries_its_score_spread(bed):
    # At the run grain because the cutoff is one number for the run, and carried so a scientist can move
    # that cutoff to where their own scores separate. A cutoff set with no sight of the scores is blind.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)

    rows = qc.filter(pl.col("measurement") == "scoreDistribution")
    assert rows.height == 1, "one figure for the run, not one per sample"
    row = rows.row(0, named=True)
    assert row["level"] == "run"
    assert row["value"] is not None
    # Eleven decile points, 0 through 100 by 10.
    points = [p.split(":")[0] for p in row["detail"].split("|")]
    assert points == [str(p) for p in range(0, 101, 10)]
    # A score is 0 to 100, and the deciles are ordered.
    values = [float(p.split(":")[1]) for p in row["detail"].split("|")]
    assert values == sorted(values)
    assert 0.0 <= values[0] and values[-1] <= 100.0
    # No line stands behind it: the spread is carried so a scientist places the cutoff, and a line here
    # would be the block placing it instead.
    assert row["status"] is None


def test_the_run_score_spread_stays_out_of_every_sample_rollup(bed):
    # It is emitted outside the sample loop, and a sample's rollup covers its OWN measurements. A run
    # figure folded into a sample would say something about that sample it does not know.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    assert qc.filter((pl.col("measurement") == "rollup") & (pl.col("level") == "run")).height == 0


def test_a_population_baseline_has_no_score_to_spread(tmp_path):
    # The declared rung scores; the distribution rung yields a probability, which is not on the same scale.
    # The row is there and says so, rather than going missing or printing a number from the wrong rule.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    qc = pl.read_csv(tmp_path / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "scoreDistribution").row(0, named=True)
    assert row["value"] is None
    assert "yields no score" in row["detail"]


def test_the_fitted_background_reaches_the_measurement_set(tmp_path):
    # The fit's parameters used to die inside the function that made them, so a scientist could not see
    # whether a tag's counts separated -- which has to be read BEFORE the baseline is settled. SEPS
    # separates and FLAT does not. Both rows exist: absence and non-separation are different facts.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    qc = pl.read_csv(tmp_path / "result_qc.csv", infer_schema_length=0)
    rows = {r["entity"]: r for r in qc.filter(pl.col("measurement") == "fittedBackground").iter_rows(named=True)}
    assert set(rows) == {"SEPS", "FLAT"}

    seps = rows["SEPS"]
    assert seps["value"] is not None
    assert "samplesFitted=1" in seps["detail"]
    assert "medianSignalMean=" in seps["detail"]
    # The background sits below the signal it was separated from. Read together they are the finding: a
    # background alone says nothing about whether the counts separated.
    signal = float(seps["detail"].split("medianSignalMean=")[1].split("|")[0])
    assert float(seps["value"]) < signal

    flat = rows["FLAT"]
    assert flat["value"] is None
    assert "fitted in no sample" in flat["detail"]

    # No line stands behind either, so neither carries a status.
    assert seps["status"] is None
    assert flat["status"] is None


def test_a_declared_baseline_fits_no_background_and_the_rows_say_so(bed):
    # Every declared measurement keeps its place. A reader must not have to tell "this run did not fit one"
    # apart from "nothing here measures that" by the row being missing.
    _run(bed, *BASE)

    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    rows = qc.filter(pl.col("measurement") == "fittedBackground")
    assert rows.height > 0, "a declared-baseline run still carries a row for every declared tag"
    assert set(rows["value"].to_list()) == {None}
    assert all("no population baseline served" in d for d in rows["detail"].to_list())


def test_a_tag_that_could_not_be_fitted_leaves_its_identity_alone_unreliable(tmp_path):
    # The whole point of a comparator keyed by identity rather than by cell: one tag fails to fit and only
    # the identities built from it lose their verdicts. Under a cell-keyed comparator this run would be
    # all-or-nothing.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert list(meta["distributionUnfitted"]) == ["S1/FLAT"], meta["distributionUnfitted"]

    # The panel declares no grouping column, so every barcode is its own identity and the identity names
    # here are the barcodes.
    v = pl.read_csv(tmp_path / "result_verdicts.csv", infer_schema_length=0)
    states = {
        identity: set(v.filter(pl.col("identity") == identity)["state"].to_list()) for identity in ("FLAT", "SEPS")
    }
    assert states["FLAT"] == {"unreliable"}
    assert "unreliable" not in states["SEPS"], "the tag that separated must still be answerable"

    # The set-level verdict is a majority of its cells, and only a twentieth of them bind, so every
    # clonotype here reads *not bound* from a comparator that served. The binding is visible one level down.
    punch = pl.read_csv(tmp_path / "result_cell_punch.csv", infer_schema_length=0)
    assert any(x.startswith("bound|") for x in punch["SEPS"].to_list() if x is not None)


def test_the_fit_is_per_tag_and_not_per_cell(tmp_path):
    # It is fitted per (sample, tag), so whether a position can be answered turns on the TAG. A cell-keyed
    # comparator would make a cell either comparable or not, and no run could then produce one
    # all-unreliable column beside one with none.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    v = pl.read_csv(tmp_path / "result_cell_punch.csv", infer_schema_length=0)
    assert v.height > 0
    # Every position of the unfitted identity reads unreliable, and no position of the fitted one does.
    flat = [x for x in v["FLAT"].to_list() if x is not None]
    fitted = [x for x in v["SEPS"].to_list() if x is not None]
    assert flat and all(x.startswith("unreliable|") for x in flat)
    assert fitted and not any(x.startswith("unreliable|") for x in fitted)


def test_a_sample_below_the_cell_condition_finishes_and_establishes_no_baseline(tmp_path):
    # 200 cells, against the three hundred this rung needs. This is the ONE refusal that cannot be caught
    # from the settings: whether a sample holds enough cells whose counts separate is a property of the
    # data. So the run FINISHES, says that no baseline could be established, and draws no punchcard.
    _distribution_bed(tmp_path, n_cells=200)
    r = _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")
    assert r.returncode == 0, r.stderr

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["baselineEstablished"] is False
    assert "no baseline could be established" in meta["noBaselineReason"]
    # The rung that was asked for is still what is recorded. Nothing substituted for it -- there is no rung
    # below to fall to.
    assert meta["referenceChoice"] == ReferenceChoice.DISTRIBUTION.value
    assert meta["referenceSourceRequested"] == ReferenceChoice.DISTRIBUTION.value

    # The answer frames keep their headers and carry no rows. A reader still finds its columns, and a
    # consumer that reads them anyway finds nothing rather than a grid of non-answers.
    v = pl.read_csv(tmp_path / "result_verdicts.csv", infer_schema_length=0)
    assert v.height == 0
    assert "state" in v.columns

    # The structural frames are written in full: they describe the run rather than answering it, and a
    # reader working out why no baseline could be established needs them.
    assert pl.read_csv(tmp_path / "result_tag_identity.csv", infer_schema_length=0).height > 0


def test_the_gate_exposure_is_not_evaluated_where_no_cell_has_a_comparator(tmp_path):
    # There is no per-cell comparator for a gate to read, so the count is not a measurement this run made.
    # None, never 0 -- a zero would report a run with no high background rather than one where the question
    # does not arise.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["cellsHighReference"] is None
    assert meta["cellsSetAside"] == 0


def test_the_minimum_never_reaches_the_comparator(wide_bed):
    """The exemption, checked end to end on a bed carrying a below-minimum comparator reading.

    The exemption is a rule rather than a preference, so there is no switch to compare against. Raising
    the minimum must remove antigen readings and leave every comparator reading standing, because the
    minimum asks whether a count is evidence of binding and a tag declared to be bound by nothing never is.
    """
    assert (
        _run(wide_bed, *_bed_args("panel_with_reference.csv"), "--floor", "1", "--output-prefix", "low").returncode == 0
    )
    assert (
        _run(wide_bed, *_bed_args("panel_with_reference.csv"), "--floor", "7", "--output-prefix", "high").returncode
        == 0
    )

    low = pl.read_csv(wide_bed / "low_cell_scalars.csv").sort(["sampleId", "cellId"])
    high = pl.read_csv(wide_bed / "high_cell_scalars.csv").sort(["sampleId", "cellId"])
    assert low["referenceCount"].to_list() == high["referenceCount"].to_list(), (
        "a comparator reading moved when the minimum rose, so the exemption is not holding"
    )

    # The guard: without it this passes on a bed where the minimum reaches nothing, and proves only that
    # nothing happened.
    low_meta = json.loads((wide_bed / "low_run_meta.json").read_text())
    high_meta = json.loads((wide_bed / "high_run_meta.json").read_text())
    assert high_meta["readingsFloored"] > low_meta["readingsFloored"]

    # The switch is gone from the contract, not merely defaulted off.
    assert "minimumAppliesToBaseline" not in high_meta


def test_a_tag_holding_no_cell_is_not_blamed_on_its_siblings(bed):
    # Three tags on one identity. AAAA and BBBB agree in every cell; CCCC is declared and holds no row
    # anywhere. Its siblings did reach a majority, so the row must not say they failed. The reason is the
    # only thing separating the two absences: both carry no rate.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type,Family\n"
        "S1,AgA,AAAA,Target,Fam\n"
        "S1,AgB,BBBB,Target,Fam\n"
        "S1,AgC,CCCC,Target,Fam\n"
        "S1,Ctrl,CTRL,Control,Reference\n"
    )
    (bed / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,AAAA,500\nS1,c1,BBBB,500\nS1,c1,CTRL,6\n"
        "S1,c2,AAAA,600\nS1,c2,BBBB,600\nS1,c2,CTRL,6\n"
    )
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\n")

    _run(bed, *BASE, "--grouping", json.dumps({"by": "property", "column": "Family"}))
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    by_tag = {r["entity"]: r for r in qc.filter(pl.col("measurement") == "siblingDisagreement").iter_rows(named=True)}

    assert by_tag["AAAA"]["value"] == "0.0"
    assert by_tag["BBBB"]["value"] == "0.0"
    assert by_tag["CCCC"]["value"] is None
    assert by_tag["CCCC"]["detail"] == "this tag holds no cell beside a sibling"
    assert by_tag["AAAA"]["detail"] is None


def test_a_tag_that_is_the_only_one_on_its_identity_says_so(bed):
    # The shipped bed groups per tag, so AAAA's identity is AAAA and carries nothing else. The reason has
    # to name the missing sibling, not the siblings' failure to agree.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter((pl.col("measurement") == "siblingDisagreement") & (pl.col("entity") == "AAAA")).row(0, named=True)
    assert row["value"] is None
    assert row["detail"] == "this identity carries one tag, so it has no sibling"


REAGENT_COLUMNS = [
    "panelId",
    "tag",
    "identity",
    "samplesSeenIn",
    "samplesInPanel",
    "seenIn",
    "samplesSeenInNames",
    "samplesInPanelNames",
    "cellsWithCount",
    "cellsAboveTheLine",
    "medianCountPerCell",
    "siblingDisagreement",
    "siblingDisagreementShown",
    "selfDisagreement",
    "selfDisagreementShown",
    "reason",
]


def test_the_reagent_table_names_every_absent_figure(bed):
    # The columns are fixed and a status is forbidden. A blank and a zero are opposite findings, so a
    # figure with no value says which case it is.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,AgD,DEAD,Target\nS1,Ctrl,CTRL,Control\n"
    )
    _run(bed, *BASE)
    reagents = pl.read_csv(bed / "result_reagents.csv", infer_schema_length=0)
    assert reagents.columns == REAGENT_COLUMNS
    assert "status" not in reagents.columns

    control = reagents.filter(pl.col("tag") == "CTRL").row(0, named=True)
    assert control["cellsAboveTheLine"] is None
    assert "cellsAboveTheLine=none asked, this tag supplies the baseline" in control["reason"]

    # A tag no read carried: zero under the counts, and the median names its own absence rather than
    # leaving a blank beside them.
    dead = reagents.filter(pl.col("tag") == "DEAD").row(0, named=True)
    assert int(dead["cellsWithCount"]) == 0
    assert int(dead["samplesSeenIn"]) == 0
    assert dead["medianCountPerCell"] is None
    assert "medianCountPerCell=no cell holds a count of this tag" in dead["reason"]

    target = reagents.filter(pl.col("tag") == "AAAA").row(0, named=True)
    assert int(target["cellsWithCount"]) == 2
    assert int(target["samplesSeenIn"]) == 1
    # One tag under this identity, so no sibling comparison exists and the row says so.
    assert target["siblingDisagreement"] is None
    assert "siblingDisagreement=this identity carries one tag" in target["reason"]


def test_the_reagent_table_prints_a_ratio_and_the_words_for_an_absent_rate(bed):
    # The quality view's own table shows "1/4" under Seen in and "no sibling" under the sibling column. A
    # blank there reads as a figure that failed to load, and a single-tag identity is the common case, so
    # the shown column carries the words while the rate beside it stays numeric.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,AgD,DEAD,Target\nS1,Ctrl,CTRL,Control\n"
    )
    _run(bed, *BASE)
    reagents = pl.read_csv(bed / "result_reagents.csv", infer_schema_length=0)
    rows = {r["tag"]: r for r in reagents.iter_rows(named=True)}

    assert rows["AAAA"]["seenIn"] == "1/1"
    assert rows["DEAD"]["seenIn"] == "0/1"

    assert rows["AAAA"]["siblingDisagreementShown"] == "no sibling"
    assert rows["DEAD"]["siblingDisagreementShown"] == "no sibling"
    # The reference tag is held out of the verdict read, which is a different cause and says so.
    assert rows["CTRL"]["siblingDisagreementShown"] == "held out of the read"

    # A tag no cell set could be read on has nothing to compare against itself either.
    assert rows["DEAD"]["selfDisagreementShown"] == "nothing to compare"

    rates = [r for r in reagents.iter_rows(named=True) if r["selfDisagreement"] is not None]
    assert rates, "at least one tag should carry a self-disagreement rate"
    for row in rates:
        assert row["selfDisagreementShown"] == f"{float(row['selfDisagreement']):.2f}"


def test_a_barcode_reused_for_two_antigens_takes_a_row_under_each(tmp_path):
    # One row could not name both identities, and putting the two side by side is the comparison the table
    # exists for: a barcode that worked where it carried one antigen and failed where it carried another.
    # Each row's figures are scoped to the samples where the tag carried that identity.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,AAAA,500\nS1,c1,CTRL,6\nS1,c2,AAAA,600\nS1,c2,CTRL,6\n"
        "S2,c1,AAAA,500\nS2,c1,CTRL,6\nS2,c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n"
        "S2,AgB,AAAA,Target\nS2,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\nS2,c1,K2\nS2,c2,K2\n")
    _run(tmp_path, *BASE, "--grouping", json.dumps({"by": "property", "column": "Name"}))

    reagents = pl.read_csv(tmp_path / "result_reagents.csv", infer_schema_length=0)
    rows = reagents.filter(pl.col("tag") == "AAAA")
    assert sorted(rows["identity"].to_list()) == ["AgA", "AgB"]

    figures = {r["identity"]: r for r in rows.iter_rows(named=True)}
    # Two cells of S1 hold the barcode, one cell of S2 does. The figures are per (tag, identity) and not
    # per tag, so the two rows carry the reagent's two behaviours rather than one number repeated.
    assert int(figures["AgA"]["cellsWithCount"]) == 2
    assert int(figures["AgB"]["cellsWithCount"]) == 1
    # The denominator is the roster for the identity, not the panel's.
    assert int(figures["AgA"]["samplesInPanel"]) == 1
    assert int(figures["AgB"]["samplesInPanel"]) == 1


def test_a_staged_reagent_reads_apart_from_a_dead_one(tmp_path):
    # STAGE is declared on S1 and S2 only, and carries a count on both. DEAD is declared on all four
    # samples and carries a count on none. Declaring DEAD on every sample splits it across two panels, so
    # it takes one row per panel; both must read empty under samplesSeenInNames.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\n"
        "S1,c1,STAGE,500\nS1,c1,CTRL,6\n"
        "S2,c1,STAGE,500\nS2,c1,CTRL,6\n"
        "S3,c1,CTRL,6\nS4,c1,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgStage,STAGE,Target\nS2,AgStage,STAGE,Target\n"
        "S1,AgDead,DEAD,Target\nS2,AgDead,DEAD,Target\nS3,AgDead,DEAD,Target\nS4,AgDead,DEAD,Target\n"
        "S1,Ctrl,CTRL,Control\nS2,Ctrl,CTRL,Control\nS3,Ctrl,CTRL,Control\nS4,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c1,K2\nS3,c1,K3\nS4,c1,K4\n")
    _run(tmp_path, *BASE)

    reagents = pl.read_csv(tmp_path / "result_reagents.csv", infer_schema_length=0)

    stage = reagents.filter(pl.col("tag") == "STAGE").row(0, named=True)
    assert stage["samplesInPanelNames"] == "S1, S2"
    assert stage["samplesSeenInNames"] == "S1, S2"

    dead_rows = reagents.filter(pl.col("tag") == "DEAD")
    assert dead_rows.height == 2
    assert sorted(dead_rows["samplesInPanelNames"].to_list()) == ["S1, S2", "S3, S4"]
    # Empty, not null: a blank and a zero are opposite findings, and no read carried this tag.
    assert set(dead_rows["samplesSeenInNames"].fill_null("").to_list()) == {""}


def test_reagent_sample_names_come_from_the_run_labels_not_the_id(labelled_bed):
    # `label_of_sample` is reachable here, so a raw sampleId never has to reach this user-facing column.
    _run(labelled_bed, *BASE, "--sample-labels", json.dumps({OPAQUE: "donor01"}))
    reagents = pl.read_csv(labelled_bed / "result_reagents.csv", infer_schema_length=0)
    row = reagents.filter(pl.col("tag") == "AAAA").row(0, named=True)
    assert row["samplesInPanelNames"] == "donor01"
    assert row["samplesSeenInNames"] == "donor01"


def _sample_report(bed, sample: str = "S1") -> dict:
    return json.loads((bed / "result_qc_by_sample.json").read_text())[sample]


def test_the_sample_report_lists_every_sample_measurement(bed):
    # A measurement that did not run takes a row rather than being omitted, so a reader meets it instead of
    # noticing an absence. The set is the declaration order of every sample-level measurement.
    _run(bed, *BASE)
    report = _sample_report(bed)
    listed = [m["id"] for m in report["measurements"]]
    assert listed == [m.id for m in MEASUREMENTS if m.level == "sample"]

    # The shape both the model and the UI are typed against. A field renamed on this side reaches them as
    # an undefined, which renders as a blank rather than as an error.
    assert set(report) == {"status", "judged", "unjudged", "notEvaluated", "measurements"}
    for row in report["measurements"]:
        assert set(row) == {
            "id",
            "label",
            "value",
            "detail",
            "reason",
            "status",
            "counts",
            "implies",
            "rollsUp",
        }


def test_a_sample_measurement_with_no_value_states_why(bed):
    # This bed passes no --qc-summary, so panelAssignedFraction never arrived. A blank and a zero are
    # opposite findings, so the row carries the reason where its number would have been. No line can be
    # applied to a value that does not exist, so it carries no status either.
    _run(bed, *BASE)
    row = {m["id"]: m for m in _sample_report(bed)["measurements"]}["panelAssignedFraction"]

    assert row["value"] is None
    assert row["reason"], "a measurement with no value names the reason in its place"
    assert row["status"] is None


def test_no_sample_measurement_is_blank_without_a_reason(bed):
    # The invariant, over the whole set rather than one row: every entry either carries a number or says
    # why it does not.
    _run(bed, *BASE)
    for row in _sample_report(bed)["measurements"]:
        if row["value"] is None:
            assert row["reason"], f"{row['id']} has no value and no reason"
        assert row["label"] and row["counts"], row["id"]


def test_a_valueless_measurement_names_the_input_that_is_actually_missing(bed):
    # Truthiness is not the test. A measurement with more than one route to having no number has to name
    # the one that happened. This bed passes a linker, so a cell list exists and depth is missing its
    # NUMERATOR, not its denominator.
    _run(bed, *BASE)
    rows = {m["id"]: m for m in _sample_report(bed)["measurements"]}

    assert rows["readsTotal"]["reason"] == "no read QC summary row reached this sample"
    assert rows["readsPerCell"]["reason"] == "no read count reached this sample, so depth has no numerator"
    assert "cellsInList=3" in rows["readsPerCell"]["detail"]


def test_a_run_with_no_cell_list_gives_its_two_cell_rows_one_account(tmp_path):
    # `in_list` is empty both when no list arrived and when one arrived holding nothing, so these two rows
    # are reachable together on every run without a linker or a cell file. They named different causes, and
    # a reader cannot act on two accounts of one fact.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS1,c2,AAAA,600\nS1,c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    args = [a for a in BASE if a not in ("--linker", "linker.csv")]
    _run(tmp_path, *args)

    rows = {m["id"]: m for m in _sample_report(tmp_path)["measurements"]}
    assert rows["readsPerCell"]["reason"] == "no cell list supplied, so depth has no denominator"
    assert rows["uniqueCountsPerCell"]["reason"] == "no cell list supplied, so no cell of this sample is listed"


def test_an_empty_cell_list_is_the_zero_cells_finding_and_not_a_missing_read_count(tmp_path):
    # A cell list that arrived and holds no cell of this sample is a different finding from no list at all,
    # and `reads_per_cell` returns no number for both. Naming the read count would point a reader at an
    # input that is present.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS2,c1,AAAA,500\nS2,c1,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n"
        "S2,AgA,AAAA,Target\nS2,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c1,K2\n")
    # The list covers S1 only, so S2 gets a list that answers "no cell here".
    (tmp_path / "cells.csv").write_text("sampleId,cellId\nS1,c1\n")
    (tmp_path / "read_qc.csv").write_text("sampleId,readsTotal,readsMatched\nS1,2000,1800\nS2,2000,1800\n")
    _run(tmp_path, *BASE, "--cells", "cells.csv", "--qc-summary", "read_qc.csv")

    report = json.loads((tmp_path / "result_qc_by_sample.json").read_text())
    row = {m["id"]: m for m in report["S2"]["measurements"]}["readsPerCell"]
    assert row["value"] is None
    assert row["reason"] == "no cell of this sample is in the cell list, so depth has no denominator"
    # The read count IS present, which is what makes the missing-numerator reason false here.
    assert {m["id"]: m for m in report["S2"]["measurements"]}["readsTotal"]["value"] == 2000


def test_the_sample_report_carries_the_rollup_the_qc_frame_carries(bed):
    # The Main grid's tag is this rollup, and the report beside it lists the measurements it was taken
    # over. One number in two places would let the tag and the list disagree about one sample.
    _run(bed, *BASE)
    report = _sample_report(bed)

    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    rollup = qc.filter(
        (pl.col("measurement") == "rollup") & (pl.col("level") == "sample") & (pl.col("entity") == "S1")
    ).row(0, named=True)

    assert report["status"] == rollup["status"]
    assert report["judged"] == int(rollup["judged"])
    assert report["unjudged"] == int(rollup["unjudged"])
    assert report["notEvaluated"] == int(rollup["notEvaluated"])
    # Coverage accounts for every measurement the rollup was taken over.
    counted = [m for m in report["measurements"] if m["rollsUp"]]
    assert report["judged"] + report["unjudged"] + report["notEvaluated"] == len(counted)


def test_usable_read_fraction_reads_blank_where_the_counts_file_carries_no_total_weight(bed):
    # `usableReadFraction` has a call site wired from `counts.csv`'s `totalWeight` column. `bed`'s
    # counts.csv predates that column, so the row reads a stated blank rather than a value -- never
    # `UNSUPPLIED_REASON`, since a call site ran and found the column absent.
    _run(bed, *BASE)
    row = {m["id"]: m for m in _sample_report(bed)["measurements"]}["usableReadFraction"]

    assert row["value"] is None
    assert row["status"] is None
    assert row["reason"] == "the counts file carries no totalWeight column"


def test_usable_read_fraction_computes_a_real_value_end_to_end(tmp_path):
    # c1 and c2 are in the cell list; c3 is not. Only c1 and c2's totalWeight counts toward the numerator,
    # over readsTotal from --qc-summary.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount,totalWeight\n"
        "S1,c1,AAAA,500,80\n"
        "S1,c1,CTRL,6,3\n"
        "S1,c2,AAAA,600,90\n"
        "S1,c2,CTRL,6,3\n"
        "S1,c3,CTRL,6,3\n"
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "cells.csv").write_text("sampleId,cellId\nS1,c1\nS1,c2\n")
    (tmp_path / "qc.csv").write_text("sampleId,readsTotal\nS1,1000\n")
    no_linker = [a for a in BASE if a not in ("--linker", "linker.csv")]

    _run(tmp_path, *no_linker, "--cells", "cells.csv", "--qc-summary", "qc.csv")
    row = {m["id"]: m for m in _sample_report(tmp_path)["measurements"]}["usableReadFraction"]

    assert row["value"] == pytest.approx((80 + 3 + 90 + 3) / 1000)
    assert row["detail"] == "cellsInList=2"


def test_usable_read_fraction_with_no_cell_list_reads_a_stated_blank(tmp_path):
    # totalWeight and readsTotal both arrive; only the cell list is missing, so the reason is
    # `usable_read_fraction`'s own -- the called-cell condition, and nothing else.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount,totalWeight\nS1,c1,AAAA,500,80\nS1,c1,CTRL,6,3\n"
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "qc.csv").write_text("sampleId,readsTotal\nS1,1000\n")
    no_linker = [a for a in BASE if a not in ("--linker", "linker.csv")]

    _run(tmp_path, *no_linker, "--qc-summary", "qc.csv")
    row = {m["id"]: m for m in _sample_report(tmp_path)["measurements"]}["usableReadFraction"]

    assert row["value"] is None
    assert row["status"] is None
    assert row["reason"] == "no cell list supplied, so the called-cell condition cannot be evaluated"


def test_usable_read_fraction_with_an_empty_cell_list_reads_zero(tmp_path):
    # A present cells.csv with a header and no rows is a checked, empty list -- distinct from no list at
    # all, so the outcome is the real finding 0.0 rather than a blank.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount,totalWeight\nS1,c1,AAAA,500,80\nS1,c1,CTRL,6,3\n"
    )
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n")
    (tmp_path / "cells.csv").write_text("sampleId,cellId\n")
    (tmp_path / "qc.csv").write_text("sampleId,readsTotal\nS1,1000\n")
    no_linker = [a for a in BASE if a not in ("--linker", "linker.csv")]

    _run(tmp_path, *no_linker, "--cells", "cells.csv", "--qc-summary", "qc.csv")
    row = {m["id"]: m for m in _sample_report(tmp_path)["measurements"]}["usableReadFraction"]

    assert row["value"] == 0.0
    assert row["detail"] == "cellsInList=0"


def test_a_declared_sample_measurement_nothing_computes_still_takes_a_row(monkeypatch):
    # The walk is over the DECLARATION, not over the rows a run happened to emit. Every declared
    # measurement has a call site today, so an implementation iterating the rows passes every other test in
    # this file byte for byte.
    extra = Measurement("neverComputed", "Never computed", "sample", "nothing computes this")
    monkeypatch.setattr(qc_rows, "MEASUREMENTS", MEASUREMENTS + (extra,))

    rows = []
    qc_rows._add(rows, "sample", "S1", "readsTotal", 10.0)
    entries, coverage = qc_rows.sample_report_rows("S1", rows)

    declared = [m for m in MEASUREMENTS if m.level == "sample"]
    assert len(entries) == len(declared) + 1, "one row per declared measurement, not per emitted row"

    row = {e["id"]: e for e in entries}["neverComputed"]
    assert row["value"] is None
    assert row["status"] is None
    assert row["reason"] == qc_rows.UNSUPPLIED_REASON
    # Nothing computed it, so it counts as unchecked rather than as checked and fine.
    assert coverage.not_evaluated >= 1


def test_qc_frame_carries_the_line_and_route_for_an_inherited_measurement():
    # cellBarcodeValidFraction is on the inherited route with a published warn/error pair. A reader who
    # sees `warn` in the frame must be able to see the number it warned against.
    rows = []
    qc_rows._add(rows, "sample", "S1", "cellBarcodeValidFraction", 0.6)
    frame = qc_rows._qc_frame(rows).row(0, named=True)

    assert frame["lineWarn"] == pytest.approx(0.75)
    assert frame["lineAlert"] == pytest.approx(0.50)
    assert frame["route"] == "inherited"


def test_qc_frame_leaves_the_numbers_null_for_the_categorical_route():
    # cellsDetected carries a route -- its status is a fact, not a threshold -- but no numeric line:
    # `route` is non-null while `lineWarn` and `lineAlert` stay null.
    rows = []
    qc_rows._add(rows, "sample", "S1", "cellsDetected", 12.0)
    frame = qc_rows._qc_frame(rows).row(0, named=True)

    assert frame["lineWarn"] is None
    assert frame["lineAlert"] is None
    assert frame["route"] == "categorical"


def test_qc_frame_leaves_all_three_null_where_no_line_backs_the_measurement():
    # readsTotal has no line on any route.
    rows = []
    qc_rows._add(rows, "sample", "S1", "readsTotal", 1000.0)
    frame = qc_rows._qc_frame(rows).row(0, named=True)

    assert frame["lineWarn"] is None
    assert frame["lineAlert"] is None
    assert frame["route"] is None


def test_qc_frame_reads_the_lines_it_was_given_not_the_shipped_default():
    # `_qc_frame` renders whatever `lines` the caller passes, the same dict `_add` used to score the row --
    # an operator override must show up here, not the shipped default.
    overridden = dict(DEFAULT_LINES)
    overridden["cellBarcodeValidFraction"] = Line(warn=0.9, error=0.6)

    rows = []
    qc_rows._add(rows, "sample", "S1", "cellBarcodeValidFraction", 0.8, lines=overridden)
    frame = qc_rows._qc_frame(rows, lines=overridden).row(0, named=True)

    assert frame["lineWarn"] == pytest.approx(0.9)
    assert frame["lineAlert"] == pytest.approx(0.6)
    # And the status was scored against the override too: 0.8 is below the raised warn line.
    assert frame["status"] == "warn"


def test_cli_flags_move_a_line_end_to_end(bed):
    # 0.91 reads OK against the shipped 0.75 warn line, and alert against a raised 0.95 one. This is the
    # CLI surface an operator actually reaches, not the Python function alone.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction,"
        "cellBarcodeValidFraction\n"
        "S1,20000,18000,0.9,4,2,1200,300,0.82,0.91\n"
    )
    r = _run(
        bed, *BASE, "--qc-summary", "qc.csv", "--cell-barcode-valid-warn", "0.95", "--cell-barcode-valid-error", "0.92"
    )
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)

    row = qc.filter(pl.col("measurement") == "cellBarcodeValidFraction").row(0, named=True)
    assert row["status"] == "alert"
    assert row["lineWarn"] == "0.95"
    assert row["lineAlert"] == "0.92"


def test_qc_frame_rollup_row_carries_no_line_or_route():
    # The rollup measurement has no declaration at all, so all three fields are null there too.
    rows = [qc_rows.QcRow("sample", "S1", qc_rows.ROLLUP, None, "", "", None, qc_rows.roll_up([]))]
    frame = qc_rows._qc_frame(rows).row(0, named=True)

    assert frame["lineWarn"] is None
    assert frame["lineAlert"] is None
    assert frame["route"] is None


def test_a_value_that_is_not_a_finite_number_is_no_value_and_says_which(monkeypatch):
    # A NaN is not the absence the caller's reason describes: that reason names a missing input, and here
    # the input arrived.
    rows = []
    qc_rows._add(
        rows,
        "sample",
        "S1",
        "readsPerCell",
        float("nan"),
        reason="no cell list supplied, so depth has no denominator",
    )
    entries, coverage = qc_rows.sample_report_rows("S1", rows)

    row = {e["id"]: e for e in entries}["readsPerCell"]
    assert row["value"] is None
    assert row["reason"] == qc_rows.NOT_A_NUMBER_REASON
    assert row["status"] is None
    # Counted the way `is_computed` counts it, so the entry and the triple cannot disagree.
    assert coverage.judged == 0


def test_the_wide_summary_carries_every_sample_in_the_roster_including_one_with_nothing(tmp_path):
    # S2 is declared on the panel and nowhere else: no counts row, no linker row, no cell-list entry.
    # `main` still puts it in the sample roster, so the wide table must still carry its row.
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS1,c2,AAAA,600\nS1,c2,CTRL,6\n"
    )
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgA,AAAA,Target\nS1,Ctrl,CTRL,Control\n"
        "S2,AgA,AAAA,Target\nS2,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\n")
    _run(tmp_path, *BASE)

    summary = pl.read_csv(tmp_path / "result_qc_summary.csv")
    assert sorted(summary["sampleId"].to_list()) == ["S1", "S2"]

    s2 = summary.filter(pl.col("sampleId") == "S2")
    assert s2.height == 1
    # Nothing computed a value for S2, so its cells read null rather than 0 -- a blank and a zero are
    # opposite findings.
    assert s2["readsTotal"].item() is None


def test_the_wide_summary_carries_every_sample_level_measurement_as_a_column(bed):
    _run(bed, *BASE)
    summary = pl.read_csv(bed / "result_qc_summary.csv")
    declared = {m.id for m in MEASUREMENTS if m.level == "sample"}
    missing = declared - set(summary.columns)
    assert not missing, f"sample-level measurement(s) with no column: {missing}"
    # The rename ban: these two ids are p-column names AND measurement-axis values elsewhere in the run,
    # so they must survive under their own name.
    assert "panelAssignedFraction" in summary.columns
    assert "cellBarcodeValidFraction" in summary.columns


def test_the_wide_summary_carries_no_rollup_column(bed):
    """The rollup lives in one place, and this table is not it.

    `roll_up`'s result travels in `result_qc_by_sample.json` and reaches a reader as the Main grid's
    Quality tag and as the heading of a sample's own Quality Checks tab. It used to be copied here as
    well, and the test that stood here pinned the two copies against each other -- because two copies of
    a status can disagree, and the one a reader happens to be looking at decides what they believe.

    One copy cannot disagree with itself. What needs pinning now is that a second one does not come
    back: this table is measurements, and a status column here would need its own cell renderer and its
    own agreement test all over again.
    """
    _run(bed, *BASE)
    summary = pl.read_csv(bed / "result_qc_summary.csv")
    assert "status" not in summary.columns
    # The rollup is still computed and still reported -- just not from here.
    by_sample = json.loads((bed / "result_qc_by_sample.json").read_text())
    assert by_sample, "the bed produced no per-sample report"
    assert all("status" in report for report in by_sample.values())


def test_a_missing_read_qc_row_names_the_row_not_the_denominator(bed):
    # No --qc-summary at all: no row reached this sample. Naming the denominator here would be false --
    # there is no row to read a denominator from.
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "aggregateBarcodeFraction").row(0, named=True)
    assert row["value"] in ("", None)
    assert "reached this sample" in row["reason"]


def test_a_present_read_qc_row_with_no_reads_names_the_denominator_not_the_row(bed):
    # A row present with readsTotal zero, reachable through parse_gate.py's empty-input path. Naming the
    # row as missing would be false.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction,"
        "aggregateBarcodeFraction,aggregateBarcodesFlagged,aggregateBarcodeThreshold\n"
        "S1,0,0,0.0,0,0,0,0,,,,\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "aggregateBarcodeFraction").row(0, named=True)
    assert row["value"] in ("", None)
    assert "no denominator" in row["reason"]
    assert "reached this sample" not in row["reason"]


def test_a_present_read_qc_row_with_reads_but_no_figure_names_neither_the_row_nor_the_denominator(bed):
    # A row present with nonzero readsTotal, but the aggregate-barcode columns absent -- a real figure
    # computed upstream that did not survive into the combined QC summary. Neither NO_READ_QC nor
    # NO_READS_TO_DIVIDE is true of this row, so a third reason is required.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,3,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "aggregateBarcodeFraction").row(0, named=True)
    assert row["value"] in ("", None)
    assert "no denominator" not in row["reason"]
    assert "reached this sample" not in row["reason"]


def test_a_read_qc_row_with_no_read_count_names_the_missing_count(bed):
    # A row present, the aggregate columns absent, and readsTotal blank. "reports nonzero reads" is false
    # of this row and "reports no reads" states a count of zero it does not carry, so the fourth case names
    # the absent count itself.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,,18000,0.9,3,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "aggregateBarcodeFraction").row(0, named=True)
    assert row["value"] in ("", None)
    assert "carries no read count" in row["reason"]
    assert "nonzero reads" not in row["reason"]
    assert "reached this sample" not in row["reason"]


def test_a_valueless_usable_fraction_carries_its_reason_and_no_detail(bed):
    # QcRow's invariant: a detail rides alongside a number, a reason stands in place of one. A row with
    # neither must not carry the same string twice.
    (bed / "qc.csv").write_text(
        "sampleId,readsTotal,readsMatched,matchedFraction,cellsDetected,"
        "featuresDetected,totalUniqueUmis,medianUmisPerCell,panelAssignedFraction\n"
        "S1,20000,18000,0.9,3,2,1200,300,0.82\n"
    )
    r = _run(bed, *BASE, "--qc-summary", "qc.csv")
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    row = qc.filter(pl.col("measurement") == "usableReadFraction").row(0, named=True)
    assert row["value"] in ("", None)
    assert row["reason"]
    assert row["detail"] in ("", None)


def test_a_reused_barcode_reads_as_its_joined_names_not_its_sequence(tmp_path):
    """A tag whose panels name it differently reads as both names, under any grouping.

    The label rungs are: the agreed name, else the disagreeing names joined, else the barcode. A property
    grouping labels its IDENTITIES from the grouped-on column, and reading the tag's name from that scope
    dropped every reused barcode to the third rung.
    """
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgA,AAAA,Target\n"
        "S2,AgA_alt,AAAA,Target\n"
        "S1,Ctrl,CTRL,Control\n"
        "S2,Ctrl,CTRL,Control\n"
    )
    (tmp_path / "counts.csv").write_text(
        "sampleId,cellId,tag,umiCount\nS1,c1,AAAA,500\nS1,c1,CTRL,6\nS2,c2,AAAA,500\nS2,c2,CTRL,6\n"
    )
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS2,c2,K2\n")
    r = _run(tmp_path, *BASE, "--grouping", json.dumps({"by": "property", "columns": ["Type"]}))
    assert r.returncode == 0, r.stderr

    labels = dict(pl.read_csv(tmp_path / "result_tag_labels.csv", infer_schema_length=0).iter_rows())
    assert labels["AAAA"] == "AgA / AgA_alt", f"the reused barcode fell back to its sequence: {labels}"
    assert labels["CTRL"] == "Ctrl", "a tag its panels agree on keeps its plain name"


# `rescued_share`: the reads a sequence off the panel carried that refine-tags then snapped onto it.
# The undeclared-barcode table is the PRE-refine pass, so its rows are not reads the run lost, and this
# is how much of that table was recovered.


def test_rescued_share_is_the_undeclared_reads_correction_recovered():
    # 30% of reads sat on an undeclared sequence; refine-tags kept 80%, so it dropped 20%. The 10%
    # between them corrected onto a panel entry.
    assert qc_rows.rescued_share(0.30, 0.80) == pytest.approx(0.10)


def test_rescued_share_is_zero_where_correction_recovered_nothing():
    # Every undeclared read was too far from the panel to correct. Zero is a measurement, not an absence.
    assert qc_rows.rescued_share(0.30, 0.70) == pytest.approx(0.0)


def test_rescued_share_has_no_value_without_both_sides():
    # No pre-refine pass, or no refine-tags report. Either way the subtraction has no second term.
    assert qc_rows.rescued_share(None, 0.80) is None
    assert qc_rows.rescued_share(0.30, None) is None


def test_rescued_share_refuses_a_negative_rather_than_publishing_one():
    # The two figures come from different files. A drop count exceeding the undeclared count means they
    # disagree, and a negative share of reads would read as a quantity rather than as that disagreement.
    assert qc_rows.rescued_share(0.10, 0.70) is None


def test_the_rescued_share_reaches_the_samples_own_report(bed):
    # It is a sample's measurement, unlike the undeclared share beside it, so it belongs in the sample's
    # report. It carries no line, so it is computed and unjudged rather than green.
    _write_raw_feature_counts(bed, [("S1", "AAAA", 40), ("S1", "CTRL", 10), ("S1", "ZZZZ", 10)])
    r = _run(bed, *BASE, "--raw-feature-counts", "raw_feature_counts.csv")
    assert r.returncode == 0, r.stderr
    report = json.loads((bed / "result_qc_by_sample.json").read_text())
    entry = next(m for m in report["S1"]["measurements"] if m["id"] == "refineRescuedShare")
    assert entry["status"] is None


def test_the_rescued_share_says_why_where_no_pre_refine_pass_reached_the_run(bed):
    # Without the pre-refine input the row stays and carries its reason, so "nothing checked this" never
    # reads like "checked and found nothing rescued".
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    report = json.loads((bed / "result_qc_by_sample.json").read_text())
    entry = next(m for m in report["S1"]["measurements"] if m["id"] == "refineRescuedShare")
    assert entry["value"] is None
    assert entry["reason"]


# --- the pre-floor per-cell per-tag table -------------------------------------------------------
#
# The bed's non-reference readings are 500 and 600, so a floor has to sit between them to bite. 550 is
# chosen for that and for nothing else: it zeroes exactly one reading, which is what these tests read.


@pytest.mark.slow
def test_raw_counts_are_taken_before_the_floor(bed):
    """The point of the second table: the floor is not visible in it.

    `result_cell_counts.csv` is evidence of binding, so `apply_floor` has zeroed every count below the
    minimum there. `result_cell_raw_counts.csv` is capture, so the same reading keeps the value the reads
    carried. Asserted against each other, because either table read alone cannot show that a count was
    floored -- afterwards a floored count and a true zero are the same number.
    """
    assert _run(bed, *BASE, "--floor", "550").returncode == 0
    floored = pl.read_csv(bed / "result_cell_counts.csv", infer_schema_length=0)
    raw = pl.read_csv(bed / "result_cell_raw_counts.csv", infer_schema_length=0)

    assert raw.columns == ["sampleId", "cellId", "tag", "umiCount"]
    floored_values = sorted(int(v) for v in floored["umiCount"].to_list())
    raw_values = sorted(int(v) for v in raw["umiCount"].to_list())

    # The 500 was zeroed for the verdicts and kept here.
    assert 0 in floored_values, "the floor bit nothing, so this test proves nothing"
    assert 500 in raw_values
    assert 0 not in raw_values, "a pre-floor table holds no manufactured zeros"


@pytest.mark.slow
def test_raw_counts_keep_the_comparator_the_floored_table_drops(bed):
    # `cell_counts` is written from the NON-reference readings, and `apply_floor` exempts the comparator
    # from the floor besides. The capture table applies neither rule, so a cell's tags sum to what that
    # cell held -- which is what a composition plot needs.
    assert _run(bed, *BASE).returncode == 0
    floored = pl.read_csv(bed / "result_cell_counts.csv", infer_schema_length=0)
    raw = pl.read_csv(bed / "result_cell_raw_counts.csv", infer_schema_length=0)
    assert "CTRL" not in set(floored["tag"].to_list())
    assert "CTRL" in set(raw["tag"].to_list())


@pytest.mark.slow
def test_raw_counts_do_not_move_when_the_floor_does(bed):
    # The floor reaches the verdicts and must not reach this table. Byte equality on one side, and a
    # required DIFFERENCE on the other: without the second half the test would pass on a run where the
    # floor changed nothing at all.
    _run(bed, *BASE, "--floor", "1")
    raw_low = (bed / "result_cell_raw_counts.csv").read_bytes()
    floored_low = (bed / "result_cell_counts.csv").read_bytes()
    _run(bed, *BASE, "--floor", "550")
    assert (bed / "result_cell_raw_counts.csv").read_bytes() == raw_low
    assert (bed / "result_cell_counts.csv").read_bytes() != floored_low, "the floor moved nothing"
