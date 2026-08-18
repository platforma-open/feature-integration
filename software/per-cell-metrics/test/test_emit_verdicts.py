import json
import shutil
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from verdict import DEFAULT_PANEL_MIN_MEMBERS, ReferenceChoice

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SRC / "emit_verdicts.py"), *map(str, args)], cwd=cwd, capture_output=True, text=True
    )


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
        "referenceThinLine",
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
    assert linker.columns == ["tag", "identity", "1"]
    assert set(linker["1"].to_list()) == {"1"}


def test_a_panel_with_no_declared_reference_falls_to_the_panel_not_to_nothing(bed):
    # Three rungs in order: a declared reagent, else the panel's own readings
    # where the panel carries enough members, else nothing. Skipping the middle
    # rung makes every identity unreliable in a twenty-antigen run that could
    # be read perfectly well -- which is the configuration the ordering exists
    # for. Ten tags here, against a shipped minimum of eight.
    tags = [f"T{i:02d}" for i in range(10)]
    (bed / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n" + "".join(f"S1,Ag{i},{t},Target\n" for i, t in enumerate(tags))
    )
    # Background counts sit *above* the shipped floor of 4. At 3 they would be
    # floored to zero, the panel median would be 0, every cell would fall below
    # the thin line, and the run would read unreliable for a reason that has
    # nothing to do with which comparator was chosen -- hiding the very thing
    # this test exists to check.
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("c1", "c2", "c3"):
        rows.append(f"S1,{cell},{tags[0]},900")
        rows.extend(f"S1,{cell},{t},10" for t in tags[1:])
    (bed / "counts.csv").write_text("\n".join(rows) + "\n")

    r = _run(bed, *BASE)
    assert r.returncode == 0, r.stderr
    meta = json.loads((bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value

    states = set(pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)["state"].to_list())
    assert states != {"unreliable"}, "the panel could serve as its own comparator and was not asked to"


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

    r = _run(bed, *BASE, "--cutoff", "0.04")
    assert r.returncode != 0
    assert "0.042" in (r.stderr + r.stdout)
    assert _run(bed, *BASE, "--cutoff", repr(bound)).returncode != 0, "the bound itself must be refused"
    assert _run(bed, *BASE, "--cutoff", repr(bound * 1.001)).returncode == 0
    assert _run(bed, *BASE, "--cutoff", "0.05").returncode == 0


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
    "--reference-thin-line",
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
    return ["counts.csv", panel_csv, *BASE[2:], *extra]


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

    # And from the other side: asked to group by the name, the run cannot place exactly the renamed
    # barcodes, because no one name holds for them. It says so rather than dropping them.
    r = _run(wide_bed, *_bed_args("panel_with_reference.csv", *NAME_GROUPING))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert set(meta["tagsWithoutGroupingValue"]) == shape["renamed"]


def test_the_bed_reaches_all_four_states_in_one_run(wide_bed):
    # A bed that cannot reach a state tests nothing about it. All four come from one run here: bound
    # from counts of 500 and 5000 against a comparator of 6, not bound from counts of 8, never asked
    # from the three-tag panel, and unreliable from the one cell whose comparator reads 1 -- below
    # the thin line of 2, so that cell cannot be compared at all.
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
    r = _run(wide_bed, *_bed_args("panel_multi_reference.csv"))
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


def test_the_higher_of_two_declared_comparators_serves(wide_bed):
    # Several comparator tags combine as any identity's tags do: by the highest. The bed's second
    # comparator reads 60 against the first's 6, and specificity_score(500, 6) is 100 while
    # specificity_score(500, 60) is 0.1 -- so a count of 500 binds against the lower comparator and
    # fails against the higher. Taking the lower, or an arbitrary one, would make the two runs
    # identical; taking the higher can only ever withdraw a binding.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv")).returncode == 0
    one = _states(wide_bed)
    assert _run(wide_bed, *_bed_args("panel_multi_reference.csv")).returncode == 0
    two = _states(wide_bed)

    assert set(one) == set(two), "the two panels declare the same identities"
    bound_one = {key for key, state in one.items() if state == "bound"}
    bound_two = {key for key, state in two.items() if state == "bound"}
    assert bound_two < bound_one, "the higher comparator must withdraw at least one binding"
    assert bound_two, "and must not withdraw them all, or the bed says nothing about which one served"
    # Withdrawn, not made unanswerable: the comparison was made against a bigger number and failed.
    assert {two[key] for key in bound_one - bound_two} == {"not bound"}


def test_the_bed_panel_without_a_declared_comparator_serves_as_its_own(wide_bed):
    shape = _bed_shape(wide_bed)
    assert len(shape["antigens"]) >= DEFAULT_PANEL_MIN_MEMBERS, "a panel this small cannot stand in"

    r = _run(wide_bed, *_bed_args("panel.csv"))
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value
    states = set(_states(wide_bed).values())
    assert states != {"unreliable"}, "the panel could serve as its own comparator and was not asked to"
    without = meta["readingsFloored"]

    # The floor spares a comparator's reading, and only a declared comparator has one to spare. With
    # no declaration the thin comparator reading of 1 is floored like any other count, so this run
    # floors strictly more than the same counts read against a declared comparator.
    assert _run(wide_bed, *_bed_args("panel_with_reference.csv")).returncode == 0
    with_declared = json.loads((wide_bed / "result_run_meta.json").read_text())["readingsFloored"]
    assert without > with_declared > 0


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

CUSTOMER_TAGS = [f"SEQ{i:02d}" for i in range(1, 10)]  # nine, against a shipped minimum of eight


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


def test_a_barcode_renamed_between_samples_falls_back_to_its_sequence_as_a_label(bed):
    # A barcode the two samples name differently carries no agreed antigen name, so the label column
    # has nothing to show and falls back to the barcode itself. A scientist then reads a raw 15-mer
    # where every other row shows an antigen.
    _customer_bed(bed, renamed=2)
    assert _run(bed, *CUSTOMER_ARGS).returncode == 0

    labels = pl.read_csv(bed / "result_identity_labels.csv", infer_schema_length=0)
    by_identity = dict(zip(labels["identity"].to_list(), labels["label"].to_list(), strict=True))

    renamed = CUSTOMER_TAGS[:2]
    for tag in renamed:
        assert by_identity[tag] == tag, f"{tag} disagrees across samples, so it has no name to show"
    # The consistently-named barcodes DO keep their antigen name. Without this the assertion above
    # would also pass on a build that had simply stopped emitting labels at all.
    for i, tag in enumerate(CUSTOMER_TAGS[2:], start=2):
        assert by_identity[tag] == f"Ag{i:03d}", f"{tag} agrees across samples and must show its name"


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


def _wide_roles(bed):
    """tag -> the set of Type values it is declared with, from the seven-column panel."""
    panel = pl.read_csv(bed / "panel_wide.csv", infer_schema_length=0)
    roles: dict[str, set[str]] = {}
    for row in panel.iter_rows(named=True):
        roles.setdefault(row["Sequence"], set()).add(row["Type"])
    return roles


def test_the_narrow_shape_loses_a_label_for_every_barcode_the_samples_name_differently(wide_bed):
    # Nine tags and no role column: the panel's own readings serve, and every barcode two samples name
    # differently has no agreed name to show. The count is derived from the bed rather than written
    # down, so a bed regenerated under another seed still asserts the same shape.
    shape = _bed_shape(wide_bed)
    r = _run(
        wide_bed, "counts.csv", "panel_narrow.csv", "--linker", "linker.csv", *NARROW_COLS, "--output-prefix", "result"
    )
    assert r.returncode == 0, r.stderr
    meta = json.loads((wide_bed / "result_run_meta.json").read_text())
    assert meta["referenceChoice"] == ReferenceChoice.PANEL.value

    labels = pl.read_csv(wide_bed / "result_identity_labels.csv", infer_schema_length=0)
    fell_back = {r["identity"] for r in labels.iter_rows(named=True) if r["identity"] == r["label"]}
    # Exactly the renamed barcodes, and nothing else: the ones the samples agree on keep their names.
    assert fell_back == shape["renamed"]
    assert fell_back, "the bed must rename at least one barcode or this test asserts nothing"
    assert len(fell_back) < labels.height, "and must not rename all of them"


def test_naming_the_off_target_role_as_the_comparator_deletes_the_off_target_questions(wide_bed):
    # The role column says what a member is TO THE QUESTION; the comparator is a different axis. Naming
    # the off-target role as the comparator does not merely move a baseline — reference tags are held
    # out of the identity universe, so the off-targets stop being asked about at all.
    roles = _wide_roles(wide_bed)
    off_target = {tag for tag, values in roles.items() if values == {"Off-Target"}}
    assert off_target, "the bed must declare at least one off-target for this test to mean anything"

    assert (
        _run(
            wide_bed, "counts.csv", "panel_wide.csv", "--linker", "linker.csv", *WIDE_COLS, "--output-prefix", "plain"
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
            "--role-column",
            "Type",
            "--reference-values",
            "Off-Target",
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
    # `Off-target` is not selected by `Off-Target`, silently — so it stays a question while its
    # identically-roled siblings become comparators.
    roles = _wide_roles(wide_bed)
    variant = {
        tag
        for tag, values in roles.items()
        if len(values) == 1 and (v := next(iter(values))) != "Off-Target" and v.lower() == "off-target"
    }
    assert variant, "the bed must carry a case variant of the off-target role"

    assert (
        _run(
            wide_bed,
            "counts.csv",
            "panel_wide.csv",
            "--linker",
            "linker.csv",
            *WIDE_COLS,
            "--role-column",
            "Type",
            "--reference-values",
            "Off-Target",
            "--output-prefix",
            "named",
        ).returncode
        == 0
    )
    asked = {identity for _, identity in _states_prefix(wide_bed, "named")}
    assert variant <= asked, "the case-variant tag was silently left as a question"


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
    # The punchcard's cell is the only place its three facts meet, so this is the one check
    # that they are the SAME three facts the long frame carries. A pivot that dropped a
    # field, swapped the counts, or paired a state with another identity's numbers would
    # still write a well-formed file.
    verdicts, punch = _punch_bed(wide_bed)
    identities = sorted(set(verdicts["identity"].to_list()))
    assert punch.columns == ["setId", *identities]

    expected = {
        (r["setId"], r["identity"]): f"{r['state']}|{r['cellsAnswered']}|{r['cellsCouldAnswer']}"
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
