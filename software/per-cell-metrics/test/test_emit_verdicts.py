import json
import shutil
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from emit_verdicts import _build_grouping, _identity_properties, _linker_frame
from panel import ANY_SAMPLE, consistent_properties, property_columns
from verdict import DEFAULT_PANEL_MIN_MEMBERS, ReferenceChoice

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(cwd, *args, expect_failure=False):
    """Run the CLI, asserting it succeeded unless the caller wants a failure.

    Success is asserted HERE rather than left to each test, because a crashed run
    is invisible to most assertions in this file: the tool writes into `cwd`, so a
    run that dies before writing leaves the PREVIOUS run's files in place and
    every read of them still succeeds. `test_output_is_byte_stable_across_runs`
    was the live case -- it compares two runs' bytes and asserted neither
    returncode, so a second invocation crashing on startup compared the first
    run's files against themselves and passed.

    stderr rides along in the message because a bare `assert returncode == 0`
    tells you the run died and not why.
    """
    r = subprocess.run(
        [sys.executable, str(SRC / "emit_verdicts.py"), *map(str, args)], cwd=cwd, capture_output=True, text=True
    )
    if expect_failure:
        assert r.returncode != 0, f"expected a non-zero exit, got 0. stdout={r.stdout!r}"
    else:
        assert r.returncode == 0, f"exited {r.returncode}. stderr={r.stderr!r}"
    return r


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
    # comparator tag, so the declared rung is the one it is about; a test that wants a different rung
    # passes its own --reference-source, which argparse takes as the later value.
    "--reference-source",
    "declared",
    "--output-prefix",
    "result",
]


@pytest.fixture
def bed(tmp_path):
    # The antigen counts clear the shipped cutoff of 75 against a reference of
    # 6: specificity_score(500, 6) and specificity_score(600, 6) are both 100,
    # while a silent cell scores specificity_score(0, 6), which is ~7.5e-09.
    # Counts of 50 and 60 score 3.1 and 7.2 and would read *not bound*, which
    # is a fact about the beta score rather than about this pipeline.
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
        "result_cell_scalars.csv",
        "result_offered.csv",
        "result_identity_labels.csv",
        "result_identity_properties.csv",
        "result_panel_mismatch.csv",
        "result_run_meta.json",
    ):
        assert (bed / name).exists(), name


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


def test_reference_source_none_produces_unreliable_not_a_crash(bed):
    r = _run(bed, *BASE, "--reference-source", "none")
    assert r.returncode == 0, r.stderr
    v = pl.read_csv(bed / "result_verdicts.csv")
    assert v.filter(pl.col("identity") == "AAAA").row(0, named=True)["state"] == "unreliable"


def test_a_tag_the_grouping_could_not_place_is_named_in_the_output(bed):
    # A property the panel file does not carry narrows what can be answered,
    # and the narrowing has to be visible where the answers are. Such a tag
    # keeps its own identity rather than vanishing, so a bare barcode sits
    # among the family identities -- inferable from the labels, but only this
    # says why it is there.
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
    # The default grouping places every tag by construction, so the field is
    # present and empty rather than absent -- a reader must be able to tell
    # "none" from "not checked".
    _run(bed, *BASE)
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == []


def test_an_alerting_tag_carries_the_figures_for_the_identities_it_feeds(bed):
    # A noisy reagent whose identities read steady is a reagent to replace, not
    # a run to distrust, and only the two numbers together say which. One tag
    # whose clonotype disagrees with itself, against four that do not: its rate
    # stands clear of its peers, so it alerts and must carry the identity
    # figures beside it.
    tags = [f"T{i:02d}" for i in range(5)]
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        + "".join(f"S1,Ag{i},{t},Target\n" for i, t in enumerate(tags))
        + "S1,Ctrl,CTRL,Control\n"
    )
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("c1", "c2", "c3", "c4"):
        rows.append(f"S1,{cell},CTRL,6")
        # T00 splits the clonotype: two cells bind it, two do not.
        rows.append(f"S1,{cell},{tags[0]},{500 if cell in ('c1', 'c2') else 5}")
        rows.extend(f"S1,{cell},{t},5" for t in tags[1:])
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,c1,K1\nS1,c2,K1\nS1,c3,K1\nS1,c4,K1\n")

    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    tag_rows = qc.filter(pl.col("measurement") == "tagDisagreement")

    alerting = tag_rows.filter(pl.col("status") == "alerting")
    assert alerting.height == 1, "exactly the split tag should stand clear of its peers"
    row = alerting.row(0, named=True)
    assert row["entity"] == tags[0]
    assert row["detail"].startswith("identitiesFed="), row["detail"]
    assert tags[0] in row["detail"]

    # Neither figure is suppressed: the identity rows are still emitted in full.
    assert qc.filter(pl.col("measurement") == "identityDisagreement").height > 0

    # And a tag that did not alert carries no attachment -- the pairing is the
    # answer to a question the alert raised, not decoration on every row.
    quiet = tag_rows.filter(pl.col("status") != "alerting")
    assert quiet.height > 0
    assert not any((d or "").startswith("identitiesFed=") for d in quiet["detail"].to_list())


def test_a_tag_noisy_in_one_panel_does_not_alert_the_panel_it_was_clean_in(bed):
    # Two samples declaring different tag sets are two panels, and both declare T00. T00's clonotype
    # splits itself in S2 and reads steady in S1. A run-global disagreement rate puts S2's noise on S1's
    # row as well, so `outlier_status` alerts inside S1 for a reagent that was clean there -- the reader
    # is sent to re-prepare the wrong panel. The rows are keyed `(tag, panelId)`, so the figure on them
    # has to be that panel's.
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
    # S1: every tag reads the same in every cell -- nothing disagrees with itself.
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
    assert rates[clean] == 0.0, "the panel whose cells never disagreed reads zero"
    assert by_panel[clean]["status"] != "alerting", "a panel must not alert for a reagent clean inside it"
    assert by_panel[max(rates, key=lambda p: rates[p])]["status"] == "alerting"


def test_the_key_only_frames_carry_a_value_column_so_they_can_become_columns(bed):
    # A p-column is built from a CSV's *value* columns, so a file of key columns
    # alone imports as nothing at all -- silently, since the file exists and is
    # well formed. What a sample was offered, and which identity a tag feeds,
    # would simply never leave the block.
    _run(bed, *BASE)
    offered = pl.read_csv(bed / "result_offered.csv", infer_schema_length=0)
    assert offered.columns == ["sampleId", "identity", "offered"]
    assert set(offered["offered"].to_list()) == {"true"}

    linker = pl.read_csv(bed / "result_tag_identity.csv", infer_schema_length=0)
    # (tag, identity). The linker has no sample axis, because neither side of its join has one.
    # A third axis would make the join malformed. Label discovery then refuses to build a spec frame,
    # and the punchcard renders no columns.
    assert linker.columns == ["tag", "identity", "1"]
    assert linker.height == linker.unique().height, "duplicate axis keys break a grid silently"
    assert set(linker["1"].to_list()) == {"1"}


def test_asking_for_a_rung_that_cannot_serve_drops_to_none_and_never_to_another_rung(bed):
    # There is no cascade any more, and its absence is the point. This bed declares no comparator tag
    # and carries a panel large enough to stand in for one, which is exactly the shape a cascade would
    # have rescued: ask for the declared rung and it drops to *none*, leaving every verdict unreliable,
    # rather than quietly serving the panel instead.
    #
    # A baseline nobody chose is a methodology nobody knows they used. The scientist gets the rung they
    # asked for or nothing, and the run says which.
    #
    # Twenty-six tags, against a shipped minimum of twenty-five, so the panel rung IS serviceable here
    # and the second half of the test proves it -- otherwise "dropped to none" would prove nothing.
    tags = [f"T{i:02d}" for i in range(26)]
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n" + "".join(f"S1,Ag{i},{t},Target\n" for i, t in enumerate(tags))
    )
    # Background counts sit *above* the shipped floor of 4. At 3 they would be
    # floored to zero and the panel median would be 0, so every count that cleared
    # the floor would score near 100 against it and read *bound* for a reason that
    # has nothing to do with which comparator was chosen -- hiding the very thing
    # this test exists to check.
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("c1", "c2", "c3"):
        rows.append(f"S1,{cell},{tags[0]},900")
        rows.extend(f"S1,{cell},{t},10" for t in tags[1:])
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")

    # BASE asks for the declared rung, and this panel marks no tag as a comparator.
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.NONE.value
    assert meta["referenceSourceRequested"] == ReferenceChoice.DECLARED.value
    states = set(pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)["state"].to_list())
    assert states == {"unreliable"}

    # The same counts, asked the other way: the panel rung serves them perfectly well. Which is what
    # makes the drop above a choice the scientist made rather than a limit of the data.
    r = _run(bed, *BASE, "--reference-source", "panel")
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    states = set(pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)["state"].to_list())
    assert states != {"unreliable"}


def test_a_panel_too_small_to_serve_still_falls_to_no_comparator(bed):
    # The founding three-antigen case: too small to stand in as its own
    # comparator, so the third rung is right there and must not be skipped.
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AAAA,Target\nS1,AgB,BBBB,Target\nS1,AgC,CCCC,Target\n"
    )
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.NONE.value


def test_no_cell_list_leaves_membership_unknown_and_depth_unevaluated(bed):
    # Which barcodes held a cell is an input. Nothing in the antigen readings
    # separates a cell from an empty droplet, so with neither list input the
    # observed barcodes must NOT stand in: they outnumber cells by one to two
    # orders of magnitude, and `readsPerCell` divides by this, so a healthy
    # library would read undersequenced.
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
    assert depth["status"] == "not evaluated"

    counts = pl.read_csv(bed / "result_cell_counts.csv", infer_schema_length=0)
    assert set(counts["inCellList"].to_list()) == {"unknown"}, "unclassified is not the same as classified 'no'"


def test_the_capture_rollup_gathers_every_sample_when_no_capture_map_is_given(bed):
    # The capture level exists so that nothing hides. Rolling up an empty
    # membership makes it report *not evaluated* over a run whose samples and
    # panels were measured perfectly well, which is the opposite of its job.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    rollups = qc.filter(pl.col("measurement") == "rollup")
    capture = rollups.filter(pl.col("level") == "capture").row(0, named=True)

    def _triple(level):
        r = rollups.filter(pl.col("level") == level)
        return [int(r[c].cast(pl.Int64).sum()) for c in ("judged", "unjudged", "notEvaluated")]

    assert sum(_triple("sample")) > 0, "the bed must have something for the capture to gather"
    assert [int(capture[c]) for c in ("judged", "unjudged", "notEvaluated")] == [
        s + p for s, p in zip(_triple("sample"), _triple("panel"), strict=True)
    ]


def test_contending_groups_reach_the_note(bed):
    (bed / "panel.csv").write_text((bed / "panel.csv").read_text() + "S1,AgB,CCCC,Target\n")
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,CCCC,1\nS1,c2,CCCC,1\nS1,c3,CCCC,1\n")
    _run(bed, *BASE, "--contending", json.dumps([["AAAA", "CCCC"]]))
    # Read without schema inference: the flag is a literal "true"/"false"
    # string, which is what a boolean p-column value has to be here, and
    # polars would otherwise infer the column back into a Boolean and hide
    # whether the file carries the string at all.
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


def test_undeclared_barcode_is_reported_and_does_not_stop_the_reading(bed):
    (bed / "counts.csv").write_text((bed / "counts.csv").read_text() + "S1,c1,TTTT,99\n")
    r = _run(bed, *BASE)
    assert r.returncode == 0
    m = pl.read_csv(bed / "result_panel_mismatch.csv")
    assert "TTTT" in m.filter(pl.col("direction") == "undeclared-in-panel")["tag"].to_list()
    assert pl.read_csv(bed / "result_verdicts.csv").height > 0


def test_empty_join_writes_headers(bed):
    (bed / "linker.csv").write_text("sampleId,cellId,setId\nS1,zzz,K9\n")
    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    assert {"setId", "identity", "state"} <= set(pl.read_csv(bed / "result_verdicts.csv").columns)


def test_a_cutoff_at_the_analytic_floor_is_refused(bed):
    # At or below specificity_score(0, 0) the analytic tally and the dense
    # oracle disagree about a silent admissible cell with no error raised.
    # `silent_tally` states that refusing such a cutoff is this CLI's job.
    # Tested *at* the bound, not merely either side of it. The refusal is
    # "at or below", and 0.04/0.05 alone cannot tell that from "below".
    from verdict import specificity_score

    bound = float(specificity_score(0, 0))

    r = _run(bed, *BASE, "--cutoff", "0.04", expect_failure=True)
    assert "0.042" in (r.stderr + r.stdout)
    _run(bed, *BASE, "--cutoff", repr(bound), expect_failure=True)  # the bound itself is refused
    _run(bed, *BASE, "--cutoff", repr(bound * 1.001))
    _run(bed, *BASE, "--cutoff", "0.05")


def test_rows_are_sorted_on_a_bed_wide_enough_for_order_to_show(bed):
    # The default bed has one set and one identity, where sorted and unsorted
    # are the same frame and a missing sort is invisible. Three identities
    # declared in descending order across two sets make the two differ.
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
        ("result_offered.csv", ["sampleId", "identity"]),
        ("result_tag_identity.csv", ["tag", "identity"]),
    ):
        frame = pl.read_csv(bed / name, infer_schema_length=0)
        assert frame.height > 1, f"{name} is too small for order to mean anything"
        assert frame.equals(frame.sort(keys)), name


def test_run_meta_records_the_comparator_served_not_the_one_requested(bed):
    # `served_source` degrades to `none` where it cannot honour a request --
    # here a panel comparator is asked for and the panel is far too small to
    # stand in as one. Recording the request instead would claim a comparator
    # the run never had, and two runs compared against different things would
    # look like two runs compared against the same thing.
    r = _run(bed, *BASE, "--reference-source", "panel", "--panel-min-members", "50")
    assert r.returncode == 0, r.stderr
    # The flag spelling and the recorded value differ on purpose: "none" is what
    # a caller asks for, `ReferenceChoice.NONE` is what the record says happened.
    from verdict import ReferenceChoice

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.NONE.value
    assert meta["referenceChoice"] != "panel", "the request must not be reported as though it were served"

    verdicts = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    assert verdicts.filter(pl.col("identity") == "AAAA").row(0, named=True)["state"] == "unreliable"


def test_sequencing_depth_divides_by_the_cell_list_not_by_observed_barcodes(bed):
    # The vendor's five thousand is per called cell. Observed barcodes exceed
    # called cells by one to two orders of magnitude in droplet data, because
    # ambient reads land on most barcodes, so dividing by them would let a
    # badly undersequenced run read acceptable. The bed makes the two differ:
    # four barcodes carry counts, three are in the cell list.
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
    assert depth["status"] == "acceptable"


def test_the_dense_oracle_is_not_reachable_from_the_entrypoint():
    # The dense oracle exists to check the analytic tally in tests. On a
    # realistic panel the grid it builds is 11-20x the sparse input, so a
    # production caller is a memory failure waiting for a big panel.
    assert "densify" not in (SRC / "emit_verdicts.py").read_text()


def test_property_grouping_normalises_and_excludes_the_reference(bed):
    # The stray whitespace is the point: `read_panel` normalises tag and
    # sample and leaves properties alone, so a builder reading the column
    # directly makes " Spike " and "Spike" two identities. Built on
    # `consistent_properties` it makes one.
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
    # `panel-file-authority`: whatever the panel says consistently about an identity's tags travels
    # with that identity's verdicts. The bed puts every case in one run -- a property both member tags
    # agree on, one they disagree about, and one that agrees for a single-tag identity while
    # disagreeing for the merged one -- because a property tested alone cannot show that the
    # disagreement is dropped per identity rather than per column.
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
    # Disagreed between the member tags, so it holds of nothing -- neither tag's value wins.
    assert held["Spike"]["Carrier"] == ""
    assert held["Spike"]["Name"] == ""
    # The same two columns still hold for the identity whose single tag settles them, which is what
    # makes the omission above about the identity rather than about the column.
    assert held["Nuc"]["Carrier"] == "Biotin"
    assert held["Nuc"]["Name"] == "AgC"

    # The reference tag declares Cyno and Avidin and is no identity, so neither value can reach the
    # export -- a declaration travelling from a tag that gets no verdict would describe nothing.
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["identityPropertyValues"]["Species"] == ["Human"]
    assert meta["identityPropertyValues"]["Type"] == ["Target"]
    assert "Avidin" not in meta["identityPropertyValues"]["Carrier"]
    # The workflow builds one spec per name in this list, so a name here that is not a column of the
    # CSV -- or the reverse -- is an import of nothing.
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
    # Both barcodes carry Family=Spike in S1 and Family=Nuc in S2. Under `panel-file-authority@3.0`
    # the panel declares per tag AND sample, so that is not a disagreement to fall back from -- it is
    # two declarations, and each barcode joins the family its own sample named. The identities are the
    # two families, and neither barcode stands alone under its raw sequence.
    #
    # This expectation INVERTED with per-sample keying. It previously asserted that both barcodes fell
    # back to their own identity, labelled with the values they declared joined ("Nuc / Spike"), and
    # that the two joined strings had to be kept apart by appending the barcode. That fallback existed
    # only because a dataset-wide map could not hold both declarations at once.
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

    labels = dict(pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    assert set(labels) == {"Spike", "Nuc"}, "each barcode joins the family its own sample declared"
    assert len(set(labels.values())) == 2, f"two identities under one label: {labels}"
    # A property grouping makes the identity the property's value, which is already the name a reader
    # recognises, so no barcode is appended and no name is joined.
    assert labels == {"Spike": "Spike", "Nuc": "Nuc"}

    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == [], "nothing fell back: every pair carried a value"


def test_a_flat_contending_list_is_refused_rather_than_read_as_characters(bed):
    # `["AgA","AgB"]` is valid JSON and the shape a hand-driven run reaches for first. Read as groups it
    # makes `set("AgA")` -- a set of CHARACTERS -- so the run completes, no competitor note ever fires,
    # every `wasCompeted` reads false, and the run record states a contention nothing tested. A silent
    # wrong answer is the worst outcome available here, so the flag is refused instead.
    r = _run(bed, *BASE, "--contending", json.dumps(["AgA", "AgB"]), expect_failure=True)
    assert "--contending" in r.stderr

    # A group of one tests nothing: an identity cannot contend with itself.
    r = _run(bed, *BASE, "--contending", json.dumps([["AgA"]]), expect_failure=True)
    assert "fewer than two members" in r.stderr

    # The valid shape still runs, so the guard rejects the mistake rather than the feature.
    _run(bed, *BASE, "--contending", json.dumps([["AgA", "AgB"]]))


def test_a_non_object_grouping_gets_the_usage_message_not_an_attribute_error(bed):
    # `--grouping '"tag"'` parses as JSON and is not a mapping. Reaching `.get` on it raises an
    # AttributeError -- a stack trace about a str, where a usage message for this exact mistake is
    # already written two lines away.
    r = _run(bed, *BASE, "--grouping", json.dumps("tag"), expect_failure=True)
    assert "--grouping must be" in r.stderr
    assert "AttributeError" not in r.stderr


def test_a_non_integer_umi_count_names_the_file_and_the_column(bed):
    # A blank or a decimal dies in the cast as a raw polars traceback naming neither the file nor the
    # column -- the two things a reader needs. This module's convention is that a bad input exits with a
    # message about the input.
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
    "--high-reference-line",
    "--grouping",
    "--contending",
    "--capture-map",
    "--output-prefix",
)


def test_every_declared_flag_is_reachable_from_the_command_line(bed):
    # Every parameter of the reading is threaded from the workflow, so a
    # parameter that exists only as a module default is one a scientist
    # cannot move. The help text is the cheapest place the whole set is
    # visible at once.
    help_text = _run(bed, "--help").stdout
    for flag in DECLARED_FLAGS:
        assert flag in help_text, flag


def test_output_is_byte_stable_across_runs(bed):
    # `combine_tags_to_identities` groups without maintaining order, so an
    # unsorted frame varies run to run. A p-column's identity is content
    # addressed, so an unstable byte order silently costs every downstream
    # node its dedup.
    _run(bed, *BASE)
    first = {p.name: p.read_bytes() for p in bed.glob("result_*")}
    _run(bed, *BASE)
    second = {p.name: p.read_bytes() for p in bed.glob("result_*")}
    assert first == second

    # Repeating the run is not enough on its own: polars groups deterministically
    # for one input, so an unsorted frame reproduces itself byte for byte and
    # this passes while the sort is missing. Sortedness itself is asserted in
    # `test_rows_are_sorted_on_a_bed_wide_enough_for_order_to_show`, which needs
    # a bed this one is too narrow to provide.


def test_a_computed_but_unjudged_measurement_is_not_reported_as_unchecked(bed):
    # `roll_up` answers *not evaluated* for a level with nothing judgeable in
    # it, which is right for a level and wrong for the measurement itself: a
    # measurement that WAS computed and carries no defensible line is
    # unjudged, and reporting it as not evaluated collapses "nothing was
    # wrong" into "nobody looked" -- the one distinction the status set
    # exists to keep apart. The row keeps its own status; the triple beside
    # it says how much was checked.
    _run(bed, *BASE)
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    floor_row = qc.filter(pl.col("measurement") == "floorRemoved").row(0, named=True)
    assert floor_row["status"] == "unjudged"
    assert (floor_row["judged"], floor_row["unjudged"], floor_row["notEvaluated"]) == ("0", "1", "0")

    deferred = qc.filter(pl.col("measurement") == "sequencingSaturation").row(0, named=True)
    assert deferred["status"] == "not evaluated"
    assert deferred["reason"]  # a deferred measurement says why nothing computed it


def test_a_capture_rollup_sums_the_coverage_it_aggregates(bed):
    # `roll_up_capture` takes statuses, so a sample that was fully computed
    # but had nothing judgeable arrives as *not evaluated* and would
    # increment the capture's not-evaluated count by one, losing every
    # measurement behind it. The counts are summed from the constituent
    # coverages instead.
    _run(bed, *BASE, "--capture-map", json.dumps({"S1": "C1"}))
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    rollups = qc.filter(pl.col("measurement") == "rollup")
    capture = rollups.filter(pl.col("level") == "capture").row(0, named=True)
    assert capture["entity"] == "C1"

    def _triple(level):
        r = rollups.filter(pl.col("level") == level)
        return [int(r[c].cast(pl.Int64).sum()) for c in ("judged", "unjudged", "notEvaluated")]

    assert [int(capture[c]) for c in ("judged", "unjudged", "notEvaluated")] == [
        s + p for s, p in zip(_triple("sample"), _triple("panel"), strict=True)
    ]


def test_a_cell_list_of_its_own_overrides_the_linker_and_is_recorded(bed):
    # The cell list is an input; the linker only says which set a cell
    # belongs to. A list from gene expression covers cells whose receptor
    # never assembled, which the linker structurally cannot, so which list a
    # figure was computed against has to travel with the run.
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
    # Order is visible in the count: the floor works on the sparse per-tag
    # frame, so two readings of one identity in one cell are two floored
    # readings. Combining first would take the highest and floor one.
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
# Every test above writes its own three-line bed, which keeps each one readable and keeps none of
# them realistic: a run whose panel is one size, whose comparator is one tag and whose cells all
# come from one sample cannot show what happens when four samples were stained differently. The
# committed bed at software/test-data/fixtures/verdicts/ carries the awkward panel shapes at once --
# panels of differing size, barcodes recurring under different names, one antigen on two barcodes,
# one comparator and two, and a barcode declared on one sample and read on another.

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
    # The bed's column names are the ones BASE already names, so only the panel file varies across
    # the three shapes: no comparator, one comparator, two.
    # The gate is on for every bed run: it is what sets c11 aside and so what keeps *unreliable*
    # reachable in the bed. 100 is above every other comparator here (6, and 60 on the two-control
    # panel) and below c11's 400, so exactly one cell is set aside.
    return ["counts.csv", panel_csv, *BASE[2:], "--gate-threshold", "100", *extra]


def _bed_shape(bed):
    """The handles these tests need, recovered from the bed by the role each barcode plays.

    Derived rather than written down because the sequences come from a seeded RNG. A bed regenerated
    under a different seed still has four barcodes carrying two antigen names, one antigen carried on
    two barcodes and one barcode declared by a single sample and read only in another; spelling the
    sequences out here would tie every assertion below to the seed instead of to the shape.
    """
    panel = pl.read_csv(bed / "panel_multi_reference.csv", infer_schema_length=0)
    counts = pl.read_csv(bed / "counts.csv", infer_schema_length=0)
    linker = pl.read_csv(bed / "linker.csv", infer_schema_length=0)

    names: dict[str, set[str]] = {}
    declared_in: dict[str, set[str]] = {}
    offered: dict[str, set[str]] = {}
    for row in panel.filter(pl.col("Type") != "Control").iter_rows(named=True):
        names.setdefault(row["Sequence"], set()).add(row["Name"])
        declared_in.setdefault(row["Sequence"], set()).add(row["Samples"])
        offered.setdefault(row["Samples"], set()).add(row["Sequence"])

    tags_of_name: dict[str, set[str]] = {}
    for tag, tag_names in names.items():
        for name in tag_names:
            tags_of_name.setdefault(name, set()).add(tag)

    read_in: dict[str, set[str]] = {}
    for sample, tag in counts.select("sampleId", "tag").iter_rows():
        read_in.setdefault(tag, set()).add(sample)

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

    # A label is not an identity, and two identities under one label are two rows a reader cannot
    # tell apart -- so where two barcodes share a name the label has to carry the barcode as well.
    labels = dict(pl.read_csv(wide_bed / "result_identity_labels.csv", infer_schema_length=0).iter_rows())
    assert set(labels) == shape["antigens"]
    assert len(set(labels.values())) == len(labels)

    # And from the other side: asked to group by the name, the run now PLACES the renamed barcodes,
    # one identity per name the panel declared. Nothing is left unplaced.
    #
    # This expectation INVERTED with per-sample keying, and the inversion is the point of the change.
    # A barcode named differently in two samples used to have "no one name that holds", so it was
    # reported in `tagsWithoutGroupingValue` and stood alone under its raw sequence. Under
    # `panel-file-authority@3.0` those are two declarations -- the same reagent identifier carrying a
    # different antigen in each sample -- so each is placed under the name its own sample gave it.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv", *NAME_GROUPING))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["tagsWithoutGroupingValue"] == [], "a renamed barcode is placed, not left unplaceable"
    named = set(pl.read_csv(wide_bed / "result_verdicts.csv", infer_schema_length=0)["identity"].to_list())
    assert named == shape["names"], "one identity per declared name, across every sample"


def test_the_bed_reaches_all_four_states_in_one_run(wide_bed):
    # A bed that cannot reach a state tests nothing about it. All four come from one run here: bound
    # from counts of 500 and 5000 against a comparator of 6, not bound from counts of 8, never asked
    # from the three-tag panel, and unreliable from the one cell the admissibility gate sets aside.
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

    # A set spanning two samples was offered whatever either panel offered, so the gap closes where
    # the two panels together cover the run. Nothing in it reads never asked.
    spanning = shape["spanning"]
    assert spanning, "the bed needs one set drawn from two samples"
    covered = set().union(*(shape["offered"][s] for s in _samples_of(shape, spanning[0])))
    assert covered == shape["antigens"], "the spanning set's panels must together cover the universe"
    assert not [i for (s, i), state in states.items() if s == spanning[0] and state == "never asked"]


def test_the_panel_mismatch_fires_per_sample_in_both_directions(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["cross"]) == 1, "the bed carries exactly one barcode declared here and read there"
    tag = shape["cross"][0]
    declaring = next(iter(shape["declared_in"][tag]))
    reading = sorted(shape["read_in"][tag])

    # Read against the two-comparator panel, the only one here declaring every barcode the counts
    # carry: on the others the undeclared comparator adds rows and the table is no longer a clean
    # statement about this one barcode.
    #
    # On the panel rung, because this version refuses two declared comparators and this test is about
    # the PANEL FILE rather than about the comparator. The minimum is lowered to the panel it has, for
    # the same reason: neither number is the subject here.
    size = pl.read_csv(wide_bed / "panel_multi_reference.csv", infer_schema_length=0)["Sequence"].n_unique()
    on_panel_rung = ["--reference-source", "panel", "--panel-min-members", str(size)]
    r = _run(wide_bed, *_bed_args("panel_multi_reference.csv", *on_panel_rung))
    assert r.returncode == 0, r.stderr

    m = pl.read_csv(wide_bed / "result_panel_mismatch.csv", infer_schema_length=0)
    rows = {(row["tag"], row["direction"]): row["samples"] for row in m.iter_rows(named=True)}
    assert m.height == 2, f"only the cross declaration should mismatch; got {m.to_dicts()}"
    assert rows[(tag, "declared-never-seen")] == declaring
    assert rows[(tag, "undeclared-in-panel")] == ", ".join(reading)

    # A global check would have cancelled these two against each other. The verdicts show why that
    # matters: the sample that read the barcode never declared it, so its set reads never asked
    # while a real count of 500 sits in the counts file -- the verdict follows the panel.
    states = _states(wide_bed)
    assert states[(_only_set(shape, reading[0]), tag)] == "never asked"

    # And the sample that declared it read nothing: its cells were offered the identity and could be
    # compared, so they answer not bound. A silent cell that can be compared is a negative answer,
    # not an absent one, and reading it as never asked was the earlier revision's bug.
    assert states[(_only_set(shape, declaring), tag)] == "not bound"


def test_one_antigen_on_two_barcodes_is_read_by_its_highest_member(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["shared"]) == 1, "the bed carries exactly one antigen on two barcodes"
    name, (first, second) = shape["shared"][0]
    spanning = shape["spanning"][0]

    # Per barcode the two cells that carry them bind opposite ones, so each barcode splits its set
    # one to one and reads unreliable on the tie.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    per_tag = _states(wide_bed)
    assert per_tag[(spanning, first)] == "unreliable"
    assert per_tag[(spanning, second)] == "unreliable"

    # Read as one antigen the two barcodes combine by the highest member, never by the sum and never
    # by an arbitrary one: each cell's reading becomes 500, both cells bind, and the set is bound.
    # Summing would reach the same verdict here by accident; what the highest rule buys is that a
    # cell's answer does not depend on how many barcodes happened to carry the antigen.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv", *NAME_GROUPING))
    assert r.returncode == 0, r.stderr
    assert _states(wide_bed)[(spanning, name)] == "bound"


def test_two_declared_comparators_are_refused_rather_than_combined(wide_bed):
    # This used to assert the opposite: that the higher of the two served, because several comparator
    # tags combined the way an identity's tags do. `baseline-scope` states that references are never
    # combined, and taking the highest is a combination.
    #
    # The atom's construct scopes each reference to a group of antigens by a declared property. This
    # version has no group-by half, so it cannot say which antigens a second comparator belongs to, and
    # it refuses instead of choosing a rule nobody wrote down. The field does the same -- the ordinary
    # antibody run rejects a second control outright.
    #
    # Refused loudly rather than degraded to no comparator: this is a panel a scientist fixes in a
    # minute, and a silent fall to *unreliable* everywhere would not tell them how.
    r = _run(wide_bed, *_bed_args("panel_multi_reference.csv"), expect_failure=True)
    assert "declares 2 baseline tags" in r.stderr
    assert "one baseline tag or none" in r.stderr

    # The one-comparator panel over the same counts still serves, so the refusal is about the count of
    # comparators and not about anything else in the bed.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv")).returncode == 0
    assert any(state == "bound" for state in _states(wide_bed).values())


def test_the_bed_panel_without_a_declared_comparator_serves_as_its_own(wide_bed):
    # The bed's panel is eight antigens, below the shipped minimum of twenty-five, so the rung is
    # asked for explicitly here. That is not the bed falling short: an eight-antigen panel is what
    # this block's runs actually carry, and the test below this one is what pins the shipped default's
    # answer for one. What this test is about is what the rung DOES once it serves -- which comparator
    # it builds, and that the minimum count spares only a declared one.
    shape = _bed_shape(wide_bed)
    stands_in = ["--reference-source", "panel", "--panel-min-members", str(len(shape["antigens"]))]

    r = _run(wide_bed, *_bed_args("panel.csv", *stands_in))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    states = set(_states(wide_bed).values())
    assert states != {"unreliable"}, "the panel could serve as its own comparator and was not asked to"
    without = meta["readingsFloored"]

    # The floor spares a comparator's reading, and only a declared comparator has one to spare. With
    # no declaration c08's comparator reading of 1 is floored like any other count, so this run
    # floors strictly more than the same counts read against a declared comparator.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv", *stands_in)).returncode == 0
    with_declared = json.loads((wide_bed / "result_run_meta.json").read_text())["readingsFloored"]
    assert without > with_declared > 0


def test_a_panel_of_this_size_no_longer_stands_in_for_its_own_comparator(wide_bed):
    # The shipped minimum answers the bed's own panel, with nothing asked for. Eight antigens is under
    # twenty-five, so the panel cannot be its own background and the run says so rather than comparing
    # a count against seven other antigens and calling the result a background estimate.
    #
    # This is what the minimum moving from 8 to 25 changed, and it is the whole point of the move: an
    # antibody kit caps at fifteen tags, so no such panel reaches this rung. Such a run now asks for
    # the tag-distribution rung instead.
    shape = _bed_shape(wide_bed)
    assert len(shape["antigens"]) < DEFAULT_PANEL_MIN_MEMBERS, "the bed grew past the minimum"

    assert _run(wide_bed, *_bed_args("panel.csv")).returncode == 0
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.NONE.value
    # Never-asked stands beside it and is not a kind of unreliable: those are the identities a set's
    # own samples never offered, and no comparator would have changed them. Every position that WAS
    # asked reads unreliable.
    assert set(_states(wide_bed).values()) == {"unreliable", "never asked"}


def test_a_reading_from_a_sample_that_never_offered_it_is_not_a_vote(bed):
    # The denominator counts only members whose OWN sample offered the identity.
    # If the numerator does not apply the same test, the two are drawn from
    # different populations: a reading from a cell that was never asked
    # displaces a silent cell's real vote. Reachable whenever a sample-keyed
    # panel meets a set spanning two samples and a tag declared for one sample
    # is read in the other -- which is the undeclared-in-panel case this block
    # measures on purpose, not an exotic shape.
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
    assert (row["cellsCouldAnswer"], row["cellsAnswered"]) == ("2", "2")


def test_every_asked_cell_reading_still_counts_when_both_samples_offered_it(bed):
    # The guard above must not throw away legitimate cross-sample votes: with
    # both samples offering the identity, all three cells vote as before.
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
    assert (row["cellsCouldAnswer"], row["cellsAnswered"]) == ("3", "3")
    assert row["state"] == "bound"  # two bound against one silent


def test_no_qc_row_carries_a_null_panel_key(bed):
    # panelId is an AXIS of the imported QC frame, and a null is not a usable
    # p-column key. Sample-level and capture-level rows belong to no panel, so
    # they carry an empty string -- which is a key -- never a null.
    _run(bed, *BASE, "--capture-map", json.dumps({"S1": "C1"}))
    qc = pl.read_csv(bed / "result_qc.csv", infer_schema_length=0)
    assert qc["panelId"].null_count() == 0

    # Both kinds must be present, or the assertion above proves nothing: rows
    # that belong to a panel carry its id, rows that belong to none carry an
    # empty string.
    panels = set(qc["panelId"].to_list())
    assert "" in panels, "sample and capture rows belong to no panel and must carry an empty key"
    assert any(p for p in panels), "tag and identity rows must carry a real panel id"


# --- the shape a real panel file arrives in -----------------------------------------------------
#
# Every bed above declares a role column, so every bed above can name a comparator. A panel file
# observed in the field carries three columns and no fourth: the sample, the barcode sequence, and the
# antigen's name. There is no role column to point `--role-column` at, so the declared rung is not
# reachable on it at all and the panel's own readings have to serve. It also reuses a barcode between
# samples under a different antigen name, which is the tag-inventory reuse the per-sample keying of the
# panel exists for.
#
# These tests fix what that file does today. They deliberately do NOT assert that a set spanning two
# samples should carry one verdict for a barcode that names two different antigens -- that question is
# open, and a test asserting today's answer would have to be deleted to settle it.

# Twenty-six, against a shipped minimum of twenty-five. The count is the only thing this list carries
# that the panel rung cares about; every test below reads its members by position, so widening it
# changes what serves and nothing else. Padded to two digits so the sorted identity list this bed's
# assertions compare against is the list order.
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

    # SEQ01 is strong; the rest sit at 10, above the shipped floor of 4 so nothing is floored away and
    # the panel median stays a real number. A background of 3 would floor to zero, drag the median to
    # zero, and make every identity unreliable for a reason unrelated to the comparator.
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
    # This bed's panel carries no role column, so it has no comparator tag to declare and the rung it is
    # about is the panel's own readings.
    "--reference-source",
    "panel",
    "--output-prefix",
    "result",
]


def test_a_panel_with_no_role_column_still_produces_verdicts(bed):
    # No --role-column and no --reference-values, because the file has no column to name. The run must
    # not fail and must not read unreliable throughout: nine tags clear the minimum of eight, so the
    # panel's own readings serve.
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
    # the raw 15-mer -- which tells a scientist nothing about what happened, at the moment they most need
    # to know -- it carries the names it DID declare, joined. The reagent stays recognisable and the
    # conflict stays visible. This is the per-tag grouping: the panel has no grouping column at all.
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
    # pair of names, which joins to one string -- so the rescue that exists to make a conflict readable
    # would put two identities under one label, the one thing the labeller promises never to do. The
    # per-tag path needs nothing added for this: its existing collision rule appends the barcode to any
    # label that repeats, joined or plain. This test is what keeps that true.
    _customer_bed(bed, renamed=0)
    rows = ["Sample,Sequence,Antigen"]
    for sample, name in (("SmpA", "Shared"), ("SmpB", "Conflict")):
        # The first two barcodes carry the identical pair; the rest agree, as the bed built them.
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
    # Same bed with the renaming removed: every barcode now agrees across both samples, so no label
    # falls back. This is what makes the previous test a statement about disagreement rather than about
    # this bed's barcodes.
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
# The three tests further up use an inline bed to fix what a role-less panel does. These two run the
# committed bed's own projections of the same slots, so they can be compared against each other and
# against the four-column panels — the only thing that varies is the shape of the declaration.

NARROW_COLS = ["--barcode-col", "Sequence", "--feature-col", "Antigen", "--sample-col", "Sample"]
WIDE_COLS = ["--barcode-col", "Sequence", "--feature-col", "Name", "--sample-col", "Samples"]

# The seven-column bed is nine antigens, under the shipped minimum of twenty-five, so a run that wants
# the panel rung asks for it and lowers the minimum to the panel it has. Neither number is the subject
# of the tests below -- they are about the ROLE column -- but the CLI requires a rung to be named, and
# naming one that cannot serve would leave every verdict unreliable and say nothing about roles.
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
    # No role column, so the panel's own readings serve and the grouping is the per-tag one. The panel
    # is below the shipped minimum of twenty-five, so the rung is asked for explicitly: what this test
    # is about is the LABEL a barcode gets, and it needs a run that produced verdicts to look at.
    #
    # A barcode two samples name differently has no agreed name, and the label used to fall through to
    # the raw 15-mer -- the conflict recorded on stderr and shown nowhere a reader would look. It carries
    # the names it DID declare instead, joined, exactly as a property grouping does.
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

    # Read from the narrow panel itself rather than from the bed helper: its OWN feature column is what
    # supplies the label here, and deriving keeps a bed regenerated under another seed asserting the same
    # shape instead of the same sequences.
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
    # And nothing is left standing under a bare barcode, which is the whole point: every identity here
    # was named by the panel, whether the samples agreed about the name or not.
    assert {i for i, label in labels.items() if i == label} == set()


def test_naming_the_off_target_role_as_the_comparator_deletes_the_off_target_questions(wide_bed):
    # The role column says what a member is TO THE QUESTION; the comparator is a different axis. Naming
    # the off-target role as the comparator does not merely move a baseline — reference tags are held
    # out of the identity universe, so the off-targets stop being asked about at all.
    # The role value that marks exactly ONE tag. This version of the block reads counts against one
    # baseline tag or none, so a role value marking two is refused before it can demonstrate anything --
    # and what is demonstrated here is the ROLE axis, not how several comparators combine.
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

    # Without the naming they are questions; with it they are gone.
    assert off_target <= asked_without, "an off-target is an identity when nothing names it a comparator"
    assert not (off_target & asked_with), "naming the role deleted the off-target questions"
    assert asked_with, "and must not delete every question, or the bed says nothing about which went"


def test_a_role_value_differing_only_in_case_is_not_matched(wide_bed):
    # The observed file held six Type values that were three roles. A tag whose role is spelled
    # `Off-target` is not selected by `Off-Target`, silently.
    #
    # The claim is now proved by WHICH tags the run names rather than by which stay questions, and it is
    # a sharper proof: `Off-Target` marks two tags, so this version refuses the panel and says exactly
    # which two it found. A matcher that ignored case would have found three and said so.
    roles = _wide_roles(wide_bed)
    agreed = {tag: next(iter(values)) for tag, values in roles.items() if len(values) == 1}
    exact = {tag for tag, value in agreed.items() if value == "Off-Target"}
    variant = {tag for tag, value in agreed.items() if value != "Off-Target" and value.lower() == "off-target"}
    assert len(exact) > 1, "the bed must declare more than one off-target for the refusal to fire"
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
        expect_failure=True,
    )
    assert f"declares {len(exact)} baseline tags" in r.stderr
    for tag in exact:
        assert tag in r.stderr, "a tag the role value names must be in the refusal"
    for tag in variant:
        assert tag not in r.stderr, "the case-variant tag was matched, and it must not be"


def _states_prefix(bed, prefix):
    v = pl.read_csv(bed / f"{prefix}_verdicts.csv", infer_schema_length=0)
    return {(r["setId"], r["identity"]) for r in v.iter_rows(named=True)}


# --- the punchcard's pivot ---------------------------------------------------------
#
# All of these run against the COMMITTED bed rather than the small inline one, and that is
# load-bearing. On the inline bed every row has cellsAnswered == cellsCouldAnswer and the
# panel yields a single identity, so swapping the two counts and shuffling the column
# order are both invisible: mutating either passed the first version of these tests. The
# committed bed carries several identities, readings whose support is short of what could
# have answered, and *never asked* positions where couldAnswer is zero.
#
# Verified by mutation: swapping the two counts, dropping the state from the value, and
# changing the separator are each caught. Dropping the `select(ordered)` that aligns the
# punch pivot with the state pivot is NOT caught and cannot be here — polars pivots columns
# in order of first appearance, which on this bed already equals sorted order. That
# alignment is enforced by construction rather than observed by a test.


def _punch_bed(bed):
    r = _run(bed, *_bed_args("panel_with_reference.csv"))
    assert r.returncode == 0, r.stderr
    return (
        pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0),
        pl.read_csv(bed / "result_identity_punch.csv", infer_schema_length=0),
    )


def test_punch_bed_can_tell_the_two_counts_apart(wide_bed):
    # The guard on the tests below. If every row answered exactly as many cells as could
    # have, swapping the two counts is undetectable and the agreement test below passes
    # while the punch draws the wrong size everywhere.
    verdicts, punch = _punch_bed(wide_bed)
    differing = verdicts.filter(pl.col("cellsAnswered") != pl.col("cellsCouldAnswer"))
    assert differing.height > 0, "bed no longer distinguishes answered from could-answer"
    assert len([c for c in punch.columns if c != "setId"]) > 1, "bed no longer has several identities"


def test_punch_pivot_agrees_with_the_long_verdicts(wide_bed):
    # The punch cell is the only place its facts meet, so this is the one check that they are the
    # SAME facts the long frame carries. A pivot that dropped a field, swapped the counts, or paired a
    # state with another identity's numbers would still write a well-formed file. Every field is
    # listed here on purpose: adding one to the value has to break this test, or the value's shape
    # would be free to drift from the frame it is built from.
    verdicts, punch = _punch_bed(wide_bed)
    identities = sorted(set(verdicts["identity"].to_list()))
    assert punch.columns == ["setId", *identities]

    expected = {
        (r["setId"], r["identity"]): "|".join(
            [
                r["state"],
                r["cellsAnswered"],
                r["cellsCouldAnswer"],
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
    # Both pivots are gated together and ordered together: the punchcard reads one and lead
    # selection reads the other, and a reader comparing them must not meet a set or an
    # identity present in one and absent from the other -- or in a different column order,
    # which is what makes the two frames comparable side by side at all.
    _punch_bed(wide_bed)
    states = pl.read_csv(wide_bed / "result_identity_summary.csv", infer_schema_length=0)
    punch = pl.read_csv(wide_bed / "result_identity_punch.csv", infer_schema_length=0)
    assert states.columns == punch.columns
    assert states["setId"].to_list() == punch["setId"].to_list()


def test_punch_state_is_the_state_the_long_frame_gives(wide_bed):
    # The state is the half of the cell that carries the answer, so it is asserted on its
    # own: a punch whose counts are right and whose state is another identity's would still
    # draw a glyph, in the wrong colour, with nothing to catch it.
    verdicts, punch = _punch_bed(wide_bed)
    by_key = {(r["setId"], r["identity"]): r["state"] for r in verdicts.iter_rows(named=True)}
    for row in punch.iter_rows(named=True):
        for identity in [c for c in punch.columns if c != "setId"]:
            assert row[identity].split("|")[0] == by_key[(row["setId"], identity)]


def test_cell_scalars_pairs_each_cell_with_its_own_admissibility(tmp_path):
    """Every cell's admissibility must be ITS OWN, not the row next to it.

    The frame this comes from is built in one order and then joined twice before
    the admissibility column is attached. Polars does not promise a left frame's
    row order survives a join (`maintain_order` defaults to "none"), so a
    positional attach can hand cells each other's labels -- and because the file
    is sorted on write, nothing downstream can tell. The keyed assertion below is
    what makes the pairing observable at all: asserting the column's PRESENCE, or
    the multiset of its values, passes just as happily when every label has moved
    one row down.

    Two distinct labels appear, which is every label this bed can reach. Since
    `count-becomes-a-state` deleted the thin-reference branch the vocabulary is
    `admissible`, `cell set aside by the admissibility gate`, and `no comparator
    for this cell` — and the third is unreachable here: with a declared comparator
    `reference_by_cell` zero-fills every analysed cell it read nothing for, so no
    cell in this bed can lack one. That reason needs a run with no comparator at
    all, where it is the answer for every cell and so distinguishes nothing.

    Three of the four cells carry one label and one carries the other, so any
    permutation that moves the gated label is still caught.
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
# Every other bed in this file uses one string on both sides: the panel's sample value
# IS the counts' sampleId. That coincidence hid a defect that made every real run
# answer *never asked* everywhere -- the panel file a scientist uploads names samples
# the way they do ("donor01"), while counts, linker and every emitted axis are keyed by
# the platform's opaque sampleId. Nothing joined, so nothing was offered to any sample
# that existed, and a question nobody was asked is correctly answered *never asked*.
#
# The two beds below therefore differ from each other ONLY in whether the two sides
# share a namespace, which is the one variable that was never varied.

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
    """The set of states a run produced -- deliberately not named `_states`, which
    already exists in this file and returns a per-key mapping."""
    v = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    return set(v["state"].to_list())


def test_a_label_map_joins_the_panel_to_the_counts(labelled_bed):
    # The fix: the run is told which sampleId each label belongs to, so the panel's
    # declarations reach the cells they were written for.
    _run(labelled_bed, *BASE, "--sample-labels", json.dumps({OPAQUE: "donor01"}))
    assert _distinct_states(labelled_bed) == {"bound"}


def test_without_the_map_a_labelled_panel_offers_nothing(labelled_bed):
    # The defect, pinned so it cannot come back silently. This is not a claim that the
    # behaviour is right -- it is the observable shape of the failure, and it is the
    # reason a run can look finished and be empty of answers.
    _run(labelled_bed, *BASE)
    assert _distinct_states(labelled_bed) == {"never asked"}


def test_a_panel_already_keyed_by_sample_id_is_unaffected(bed):
    # The map must not become mandatory: a panel whose sample values already ARE
    # sampleIds is the case every other bed here exercises, and it keeps working with
    # no map and with an irrelevant one.
    _run(bed, *BASE)
    without = _distinct_states(bed)
    _run(bed, *BASE, "--sample-labels", json.dumps({"someone-else": "unrelated"}))
    assert _distinct_states(bed) == without


def test_a_barcode_named_differently_per_sample_becomes_one_identity_per_name(tmp_path):
    """A reused barcode is placed under each name its own sample declared.

    Grouping by a property makes the identity the property's value. Under
    `panel-file-authority@3.0` the panel declares per tag AND sample, so a barcode
    carrying one name here and another there is not a tag that 'has nothing to
    group on' -- it is a reagent identifier reused to cover more antigens than the
    study has tags, and each declaration places it in that sample.

    AAAA is named differently across the two samples and CCCC is not, so the same
    run shows both the reuse case and the ordinary one.

    THIS TEST INVERTED. It previously asserted AAAA stood alone under its raw
    sequence, labelled with the two names joined ('SpikeWT / SpikeWT__alt'), and
    that it was reported in `tagsWithoutGroupingValue`. That behaviour existed
    only because a dataset-wide tag->identity map could not hold two declarations
    for one barcode.
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

    labels = dict(
        pl.read_csv(tmp_path / "result_identity_labels.csv", infer_schema_length=0)
        .select("identity", "label")
        .iter_rows()
    )
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
    # Many-to-many by design: under (tag, sample) grouping T1 feeds A in one sample and B in another,
    # and both pairs are real. Deliberately NOT keyed by sample — the linker joins a tag-keyed figure
    # to an identity-keyed verdict, and neither side has a sample axis. Verdicts are (set, identity)
    # over clonotypes that span samples. The per-tag figures are run-level. An axis no joined table has
    # makes the join malformed rather than more precise, and label discovery rejects it.
    grouping = {("T1", "s1"): "A", ("T1", "s2"): "B", ("T2", "s1"): "A", ("T2", "s2"): "A"}
    frame = _linker_frame(grouping)
    rows = sorted(zip(frame["tag"].to_list(), frame["identity"].to_list()))
    assert rows == [("T1", "A"), ("T1", "B"), ("T2", "A")]
    # T2 feeds A in both samples and appears once. Duplicate axis keys break a grid silently —
    # one row and an ellipsis, no error anywhere.
    assert len(rows) == len(set(rows))
    assert set(frame["1"].to_list()) == {1}
    assert "sample" not in frame.columns


def test_a_global_declaration_adds_no_pair_of_its_own():
    # ANY_SAMPLE feeds the same identity everywhere, so it contributes that one pair and nothing more.
    frame = _linker_frame({("T1", ANY_SAMPLE): "A"})
    assert sorted(zip(frame["tag"].to_list(), frame["identity"].to_list())) == [("T1", "A")]


# --- a grouped-on column is a declaration by construction ------------------------------------


def test_a_grouped_on_column_travels_even_when_a_member_tag_is_reused():
    # `panel-file-authority`: "The columns the scientist grouped on are declarations of it, unique by
    # construction." Identity B exists only because T1 is reused with a different Identity per sample,
    # so tag-grain agreement drops Identity for T1 — and B carried no declaration of the very thing it
    # was grouped on. A passed only because T2 happens to agree across its samples, which is luck.
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

    T1 declares two Channels across its samples, so it has no agreed value of its
    own. T2 declares one. Before the fix T1 reached the agreement test as the
    empty string, was filtered out exactly like a member whose cell was blank,
    and T2 then agreed with nobody but itself -- so the identity came back
    carrying T2's Channel as though it held of both.

    Measured on a real sixteen-row panel grouped on its role column: an identity
    whose five member tags declared six different antigen names between them came
    back carrying ONE member's name, because four of the five had contradicted
    themselves into silence.

    A member that contradicted itself is a disagreement, not a silence.
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

    # Without the disagreements the old answer is still reachable, which is what makes this a
    # threading fix rather than a rewrite of the agreement rule.
    assert _identity_properties(grouping, props, cols, declared)["A"]["Channel"] == "FITC"


def test_a_member_that_declares_nothing_still_does_not_block_its_neighbours():
    """The other silence, and it must keep behaving as it did.

    T1 leaves the cell blank. It never declared anything to contradict, so it has
    no disagreement to propagate and T2's value holds of the identity.
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

    A tag reaches an identity because of its value in the grouping column, so
    that value is not open to an agreement test -- and a reused barcode has no
    tag-grain agreement to test in the first place.
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
    # It groups on no column, so there is nothing to take by construction. Every property still
    # travels by the agreement rule.
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
    # `grouping-belongs-to-the-question`: "Named antigen and concentration together, the identity is
    # the pair, and the same antigen at two concentrations is two identities."
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
    # never a property column. Naming it as the role column exited 0 whenever no role values came with
    # it: the check was gated on the values, so no tag was designated and the baseline fell back to the
    # panel's own readings in silence. A different number reported as the requested one is worse than a
    # dead run, so this is the half of the mistake that had to stop being quiet.
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
    # `the-explore-readout` puts "the clonotype's own cell count beside its name" in the grid, so the
    # grid needs it as a column. It is the set's cells, not its answering cells: it does not vary by
    # identity, which is why it belongs beside the name rather than in every position.
    _run(bed, *BASE)
    counts = pl.read_csv(bed / "result_set_counts.csv", infer_schema_length=0)
    assert "cellCount" in counts.columns
    assert all(int(v) >= 1 for v in counts["cellCount"].to_list())
    # And it is not the answering count: that varies by identity, this one does not.
    verdicts = pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)
    by_set = dict(zip(counts["setId"].to_list(), counts["cellCount"].to_list()))
    for set_id, could in zip(verdicts["setId"].to_list(), verdicts["cellsCouldAnswer"].to_list()):
        assert int(could) <= int(by_set[set_id]), "a set cannot answer with more cells than it has"


def test_set_counts_carry_the_clonotype_s_own_set_aside_cells(bed):
    # 206 states set-aside cells once for the clonotype, because a set-aside cell answers nothing at
    # any identity -- repeating the subtraction at every position would imply a per-identity failure
    # that did not happen. Run-level is the wrong grain for that: the expansion is about one clonotype.
    #
    # The bed's baseline is CTRL at 6 UMIs in every cell, so a gate of 5 sets every cell aside. That
    # gives a real non-zero to assert against rather than a vacuous 0 == 0.
    _run(bed, *BASE, "--gate-threshold", "5")
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert "cellsSetAside" in counts.columns
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert counts["cellsSetAside"].sum() == meta["cellsSetAside"]
    assert meta["cellsSetAside"] > 0, "the gate set nothing aside, so this proves nothing"


def test_set_counts_report_no_set_aside_cells_when_no_gate_is_declared(bed):
    # Off is the default. The column still has to be present and zero, so a reader never has to tell
    # "no gate" apart from "column missing".
    _run(bed, *BASE)
    counts = pl.read_csv(bed / "result_set_counts.csv")
    assert counts["cellsSetAside"].to_list() == [0] * len(counts)


def test_run_meta_carries_set_aside_cells_per_clonotype(bed):
    # 206 states set-aside cells once for the clonotype, and the expansion reads them from the run
    # record rather than from a p-column: a Parquet column's values cannot be read in the model, and a
    # set-grain number joined into the per-identity table would repeat down every row -- which the atom
    # forbids, because it implies a per-identity failure that did not happen.
    #
    # The bed's baseline is CTRL at 6 UMIs in every cell, so a gate of 5 sets every cell aside and the
    # assertion has a real non-zero to bite on.
    _run(bed, *BASE, "--gate-threshold", "5")
    meta = json.loads((bed / "result_run_meta.json").read_text())
    by_set = meta["cellsSetAsideBySet"]
    assert sum(by_set.values()) == meta["cellsSetAside"]
    assert meta["cellsSetAside"] > 0, "the gate set nothing aside, so this proves nothing"
    # Sparse: the run record is parsed on every render, so a clonotype that lost nothing carries no
    # entry. A reader takes an absent key as zero.
    assert all(n > 0 for n in by_set.values())
    # The CSV keeps its own dense rendering, and the two cannot disagree -- one helper produces both.
    counts = pl.read_csv(bed / "result_set_counts.csv")
    dense = dict(zip(counts["setId"].to_list(), counts["cellsSetAside"].to_list()))
    assert by_set == {k: v for k, v in dense.items() if v > 0}


def test_run_meta_omits_set_aside_cells_per_clonotype_when_no_gate_is_declared(bed):
    # 206 shows the count only where a gate is declared. The key is ABSENT rather than an empty object,
    # so the UI branches on one thing -- was a gate declared -- and never has to tell "no gate" apart
    # from "a gate that took nothing".
    _run(bed, *BASE)
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert "cellsSetAsideBySet" not in meta


@pytest.fixture
def two_set_bed(tmp_path):
    # Two clonotypes whose cells read the comparator differently, so a gate can catch one and leave the
    # other untouched. This is the ONLY shape that can falsify a dense map: with a single clonotype, an
    # implementation that emitted every clonotype including the zeros passes every other assertion in
    # this file.
    #
    # K1's cells read CTRL at 6, so a gate of 5 takes both. K2's read it at 2, so the same gate leaves
    # both. The antigen counts clear the shipped cutoff in each case, so both clonotypes still produce
    # verdicts and the run is not degenerate.
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
    # The sparseness claim, tested where it can fail. The run record is parsed on every model render, so
    # a clonotype that lost nothing carries NO entry and a reader takes an absent key as zero. A map that
    # carried `"K2": 0` would defeat that and pass every relative assertion above.
    _run(two_set_bed, *BASE, "--gate-threshold", "5")
    meta = json.loads((two_set_bed / "result_run_meta.json").read_text())
    # Exact, not relative: K1 has two cells and the gate takes both.
    assert meta["cellsSetAsideBySet"] == {"K1": 2}
    assert "K2" not in meta["cellsSetAsideBySet"], "a clonotype the gate did not touch must be absent"
    # The CSV stays DENSE, which is its own contract: a reader of a table must never have to tell "no
    # gate" apart from "column missing". The contrast between the two renderings is the design.
    counts = pl.read_csv(two_set_bed / "result_set_counts.csv")
    dense = dict(zip(counts["setId"].to_list(), counts["cellsSetAside"].to_list()))
    assert dense == {"K1": 2, "K2": 0}


@pytest.fixture
def silent_position_bed(tmp_path):
    # One clonotype, two cells, two antigens, and the shape that separates "asked and silent" from "never
    # asked": c1 carries counts for both antigens, c2 carries counts for AgA only. So (c2, AgB) has no row
    # in `read_states` at all, and every antigen is on the sample's panel -- which makes it a SILENT
    # position rather than an unasked one. An implementation that pivots the sparse frame and stops leaves
    # that position blank, and blank is reserved for never-asked.
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
        # Keyed by the BARCODE, not the display name: with no grouping column each tag stands alone
        # under its own sequence, exactly as the set-level punch keys its columns.
        assert "AAAA" in rows[key] and "BBBB" in rows[key]
        assert rows[key]["setId"] == "K1", "the set travels as a column so the readout can filter on it"


def test_cell_punch_resolves_a_silent_position_rather_than_leaving_it_blank(silent_position_bed):
    # The claim this fixture exists for. (c2, AgB) has no row in the states frame, its sample offered AgB,
    # and c2 can be compared -- so it reads NOT BOUND, exactly as silent_tally counts it when it produces
    # c2's contribution to K1's verdict at AgB. Blank here would contradict that arithmetic.
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
    # c1 bound both antigens; c2 bound AgA and was silent -- so not bound -- at AgB.
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


def test_the_panel_comparator_is_built_from_raw_counts(tmp_path):
    """The production call site passes the raw frame, not the floored one.

    The unit test in test_verdict.py pins what the two frames produce. This
    pins which one production hands over, which is where the defect actually
    was and which no assertion in this file reached: every fixture bed here
    reads well clear of the minimum, so flooring changed no comparator and the
    suite stayed green either way.

    c1's five readings are 1, 1, 2, 9, 9. Raw they median to 2. Floored at the
    shipped minimum of 4 they are 0, 0, 0, 9, 9 and median to 0 — which would
    push every verdict in that cell toward *bound*, since a comparator of zero
    is the easiest bar there is.
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

    Written from a seeded generator: the rung under test is a density, and a
    handful of hand-written counts has no density. The seed is fixed, so the bed
    is the same bytes on every run.

    `SEP` binds in a fifth of the cells. `FLAT` is background everywhere, which
    is a tag the panel declared and nothing bound -- the shape that must come
    back with no comparator rather than an invented one.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    n_binders = n_cells // 20
    sep = np.concatenate([rng.poisson(2, n_cells - n_binders), rng.poisson(binder_rate, n_binders)])
    flat = rng.poisson(2, n_cells)

    rows = ["sampleId,cellId,tag,umiCount"]
    for i in range(n_cells):
        for tag, values in (("SEPS", sep), ("FLAT", flat)):
            if values[i] > 0:
                rows.append(f"S1,c{i},{tag},{values[i]}")
    (root / "counts.csv").write_text("\n".join(rows) + "\n")
    (root / "panel.csv").write_text("Sample,Antigen,Sequence\nS1,AgSep,SEPS\nS1,AgFlat,FLAT\n")
    (root / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,c{i},K{i % 4}\n" for i in range(n_cells)))
    # The cell list is what fixes the fit's population, including the cells that
    # read nothing for a tag. Without it the universe is only the observed cells.
    (root / "cells.csv").write_text("sampleId,cellId\n" + "".join(f"S1,c{i}\n" for i in range(n_cells)))
    return root


def test_the_tag_distribution_rung_serves_and_says_so(tmp_path):
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.DISTRIBUTION.value
    assert meta["referenceSourceRequested"] == ReferenceChoice.DISTRIBUTION.value
    assert meta["distributionMinCells"] == 300


def test_a_tag_that_did_not_separate_has_no_comparator_and_its_identity_alone_is_unreliable(tmp_path):
    # The whole point of a comparator keyed by identity rather than by cell: one
    # tag fails to fit and only the identities built from it lose their verdicts.
    # Under a cell-keyed comparator this run would be all-or-nothing.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert list(meta["distributionUnfitted"]) == ["S1/FLAT"], meta["distributionUnfitted"]

    # The panel declares no grouping column, so every barcode is its own
    # identity and the identity names here are the barcodes.
    v = pl.read_csv(tmp_path / "result_verdicts.csv", infer_schema_length=0)
    states = {
        identity: set(v.filter(pl.col("identity") == identity)["state"].to_list()) for identity in ("FLAT", "SEPS")
    }
    assert states["FLAT"] == {"unreliable"}
    assert "unreliable" not in states["SEPS"], "the tag that separated must still be answerable"

    # The set-level verdict is a majority of its cells, and only a twentieth of
    # them bind, so every clonotype here reads *not bound* and reads it from a
    # comparator that served. The binding is visible one level down, and the bed
    # is worth nothing unless it is there.
    punch = pl.read_csv(tmp_path / "result_cell_punch.csv", infer_schema_length=0)
    assert any(x.startswith("bound|") for x in punch["SEPS"].to_list() if x is not None)


def test_the_comparator_is_the_same_for_every_cell_of_a_sample(tmp_path):
    # It is fitted per (sample, tag), so it cannot vary cell to cell. A per-cell
    # comparator appearing here would mean the cell-keyed path served instead.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    v = pl.read_csv(tmp_path / "result_cell_punch.csv", infer_schema_length=0)
    assert v.height > 0
    # Every position of the unfitted identity reads unreliable, and no position
    # of the fitted one does. Under a cell-keyed comparator a cell is either
    # comparable or not, so no run could produce this pair of columns.
    flat = [x for x in v["FLAT"].to_list() if x is not None]
    fitted = [x for x in v["SEPS"].to_list() if x is not None]
    assert flat and all(x.startswith("unreliable|") for x in flat)
    assert fitted and not any(x.startswith("unreliable|") for x in fitted)


def test_a_sample_below_the_cell_condition_falls_to_no_comparator(tmp_path):
    # 200 cells. Nothing can be fitted, so the run has no comparator at all --
    # reported as the bottom rung rather than as a partly served one.
    _distribution_bed(tmp_path, n_cells=200)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.NONE.value
    assert meta["referenceSourceRequested"] == ReferenceChoice.DISTRIBUTION.value

    v = pl.read_csv(tmp_path / "result_verdicts.csv", infer_schema_length=0)
    assert set(v["state"].to_list()) == {"unreliable"}


def test_the_gate_exposure_is_not_evaluated_where_no_cell_has_a_comparator(tmp_path):
    # There is no per-cell comparator for a gate to read, so the count is not a
    # measurement this run made. None, never 0 -- a zero would report a run with
    # no high background rather than one where the question does not arise.
    _distribution_bed(tmp_path)
    _run(tmp_path, *DISTRIBUTION_ARGS, "--cells", "cells.csv")

    meta = json.loads((tmp_path / "result_run_meta.json").read_text())
    assert meta["cellsHighReference"] is None
    assert meta["cellsSetAside"] == 0


def test_the_baseline_minimum_switch_changes_the_accounting_and_not_one_verdict(wide_bed):
    """The whole claim behind shipping this as a setting, checked end to end.

    Each rung reads its own counts raw, so the comparator is built from the
    unfloored frame whichever way the switch sits. What moves is the run's own
    accounting -- what it reports as removed, and what it reports as emptied.

    If a verdict ever moves here, the setting has stopped being an accounting
    choice and become a scientific one, and it must not ship off-by-default as
    though it were free.
    """
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv"), "--output-prefix", "off").returncode == 0
    assert (
        _run(
            wide_bed,
            *_bed_args("panel_with_reference.csv"),
            "--minimum-applies-to-baseline",
            "true",
            "--output-prefix",
            "on",
        ).returncode
        == 0
    )

    for artifact in ("verdicts", "cell_counts", "cell_scalars"):
        off = (wide_bed / f"off_{artifact}.csv").read_bytes()
        on = (wide_bed / f"on_{artifact}.csv").read_bytes()
        assert off == on, f"{artifact} moved, so the switch is not an accounting choice"

    off_meta = json.loads((wide_bed / "off_run_meta.json").read_text())
    on_meta = json.loads((wide_bed / "on_run_meta.json").read_text())
    assert off_meta["minimumAppliesToBaseline"] is False
    assert on_meta["minimumAppliesToBaseline"] is True
    # The bed carries one below-minimum comparator reading, so switching it on
    # must remove strictly more. Without this the test passes on a bed where the
    # switch reaches nothing, and proves only that nothing happened.
    assert on_meta["readingsFloored"] > off_meta["readingsFloored"]
