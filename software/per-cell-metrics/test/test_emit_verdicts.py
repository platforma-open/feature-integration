import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from verdict import ReferenceChoice

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
