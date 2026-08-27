"""The spec's acceptance scenarios, each driven from files through the CLI.

Every scenario writes a counts CSV, a panel CSV and a linker CSV, runs `emit_verdicts.py` as a
subprocess, and asserts on the CSVs it wrote. Nothing here builds a per-cell state frame, calls
`read_states`, or reaches into a module. An earlier revision did exactly that and passed while the
pipeline read a mutant whose cells all failed to bind as *never asked*.

**Absence in the counts file means two different things.** A tag the SAMPLE's reads never carry is a
reagent that produced nothing: it removes its cells from what could answer, and the position reads
*never asked*. A tag the sample did measure that a particular CELL read nothing for is a reading that
happened and failed, and that cell votes *not bound*. So a bed testing a failure to bind gives the tag
ambient counts, and only the dead-reagent bed leaves a tag out entirely.

Four numbers are load-bearing in the beds below.

*The cutoff is 75 and the score is a beta function, not a ratio.* Against a reference of 6, a count of
500 scores 100 and binds. Counts of 50 and 60 score 3.1 and 7.2 and read *not bound*. Against a
reference of 20 a count of 500 still scores 99.85. `specificity_score(count, reference)` in verdict.py
answers directly.

*The floor is 4.* Any antigen reading of 1-3 is zeroed before anything else runs, so background counts
here sit at 5 or above. A floored reading can also drag a panel-derived comparator to zero, against
which every surviving count scores near 100.

*A cell with no comparator reading is inadmissible and votes nowhere.* Every bed declares a comparator
tag whose role value matches `--reference-values`, and gives every one of its cells a count for it.

Tags are the identities under the default per-tag grouping, so they are named for the part they play
(`TARGET`, `OFF1`) rather than written as barcode sequences.
"""

import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

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
    # Stated, because the CLI requires it: nothing below the model picks a rung. This bed declares a
    # comparator tag, which is the rung every scenario here is read under.
    "--reference-source",
    "declared",
    "--output-prefix",
    "result",
]

# A comparator reading every cell shares. Above the floor of 4 so it survives it, and well below the
# high-reference observation line of 100.
COMPARATOR = 6

# Clears the cutoff of 75 against a comparator of 6: the score is 100.
BINDING = 500

# Survives the floor of 4 and scores 0.0 against a comparator of 6. A reading that is present and
# settles *not bound*, as distinct from a cell that was asked and produced no row at all.
BACKGROUND = 5

# The cutoff bracket. Every other count in this file scores about 0 or about 100, so the cutoff has no
# reachable neighbourhood here and a bed cannot tell 50 from 75 from 90. These two sit either side of it
# against COMPARATOR: 126 scores 69.02 and 140 scores 79.36. Asserting one on each side pins the default
# the ENTRYPOINT applies to the band (69.02, 79.36], which no other test in the repository does -- the
# module constant is asserted directly elsewhere, and the argparse default is a second copy of it.
JUST_BELOW_CUTOFF = 126
JUST_ABOVE_CUTOFF = 140


def _verdicts(bed):
    # Read without schema inference throughout. `unreliableReason` is null on a settled row, and polars
    # would otherwise infer the counts back into integers and the nulls into something a test cannot tell
    # from an empty string.
    return pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)


def _row(bed, set_id, identity):
    got = _verdicts(bed).filter((pl.col("setId") == set_id) & (pl.col("identity") == identity))
    assert got.height == 1, f"expected exactly one ({set_id}, {identity}) row, got {got.height}"
    return got.row(0, named=True)


# ---------------------------------------------------------------------------
# Epitope loss: the finding is a failure, and the failure comes from silence.
# ---------------------------------------------------------------------------


# Ambient. A reagent that is present and bound nothing still returns counts, because ambient material
# reaches every droplet -- so a mutant the clone failed to bind reads LOW, not empty. 2 sits under the
# floor of 4, so the reading is zeroed and the position settles *not bound*. Deliberately not 0 rows:
# zero rows means a reagent that never worked, a different finding with a different state.
AMBIENT = 2


@pytest.fixture
def epitope_bed(tmp_path):
    """One clonotype against an unmutated antigen and four point mutants.

    The fourth mutant carries **ambient counts only** -- present in the reads, below the floor. The clone
    failed to bind M4, and a failure to bind is not the reagent going missing.

    Giving M4 no rows at all would make this the dead-reagent bed below, where the same absence means the
    question was never put and the state is *never asked*. The two are told apart by whether the tag
    appears in the sample's reads, never by how low its counts are.
    """
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgWT,WT,Target\n"
        "S1,AgM1,M1,Target\n"
        "S1,AgM2,M2,Target\n"
        "S1,AgM3,M3,Target\n"
        "S1,AgM4,M4,Target\n"
        "S1,Ctrl,CTRL,Control\n"
    )
    cells = ("c1", "c2", "c3", "c4")
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in cells:
        rows.append(f"S1,{cell},CTRL,{COMPARATOR}")
        # The clone binds the unmutated antigen and the first three mutants.
        for tag in ("WT", "M1", "M2", "M3"):
            rows.append(f"S1,{cell},{tag},{BINDING}")
        # M4 is on the panel, in the reads, and bound by nothing.
        rows.append(f"S1,{cell},M4,{AMBIENT}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,{cell},K1\n" for cell in cells))
    return tmp_path


@pytest.fixture
def dead_reagent_bed(tmp_path):
    """The same panel, with M4's reagent having produced nothing at all.

    M4 has **no rows in the counts file for any cell of the sample**. Zero reads is categorical and cannot
    arise from biology: ambient reagent reaches every cell, so a tag that bound nothing still returns
    counts. Zero reads means a reagent never added, a barcode mis-declared, or a library that failed.
    """
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgWT,WT,Target\nS1,AgM4,M4,Target\nS1,Ctrl,CTRL,Control\n"
    )
    cells = ("c1", "c2", "c3", "c4")
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in cells:
        rows.append(f"S1,{cell},CTRL,{COMPARATOR}")
        rows.append(f"S1,{cell},WT,{BINDING}")
        # M4 absent for every cell: the reagent produced nothing.
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,{cell},K1\n" for cell in cells))
    return tmp_path


def test_the_mutant_no_cell_bound_reads_not_bound_not_never_asked(epitope_bed):
    # The scientist's statement is "binds the unmutated antigen and fails on the fourth mutant", so *not
    # bound* is the finding and the run must produce it from silence. Reading M4 as *never asked* turns the
    # finding into a gap.
    r = _run(epitope_bed, *BASE)
    assert r.returncode == 0, r.stderr

    wt = _row(epitope_bed, "K1", "WT")
    assert wt["state"] == "bound"

    m4 = _row(epitope_bed, "K1", "M4")
    assert m4["state"] == "not bound", "the cells were offered M4 and were silent; silence is a failure to bind"
    assert m4["unreliableReason"] is None, "a settled reading carries no reason for not settling"

    # Every one of the four cells was offered M4 and every one of them voted. A reading resting on the
    # four silences is what makes the failure a finding rather than an absence of data.
    assert (int(m4["cellsAsked"]), int(m4["cellsAnswered"])) == (4, 4)


def test_a_live_mutant_nothing_bound_is_still_reported_as_seen(epitope_bed):
    # The quality row and the verdict say different things and neither substitutes for the other. Here the
    # reagent worked, because it returned ambient counts in every cell, so the panel-versus-reads check must
    # NOT report it as a tag the reads never show.
    assert _run(epitope_bed, *BASE).returncode == 0
    qc = pl.read_csv(epitope_bed / "result_qc.csv", infer_schema_length=0)
    never_seen = qc.filter((pl.col("measurement") == "declaredNeverSeen") & (pl.col("entity") == "M4"))
    assert never_seen.height == 1
    assert float(never_seen.row(0, named=True)["value"]) > 0.0, "M4 returned reads, so it was seen"
    assert _row(epitope_bed, "K1", "M4")["state"] == "not bound"


def test_a_dead_reagent_reads_never_asked_not_a_confident_negative(dead_reagent_bed):
    # The headline failure, arriving by the one route the states were not watching. The antigen was
    # declared, so *never asked* does not fire from the panel. Zero counts fall below the minimum, so every
    # cell settles *not bound* -- a confident clean negative on every clone in the run.
    #
    # A tag the reads never show removes its cells from what could answer. The file declares what was
    # offered, the reads say which cells were actually measured. No line is drawn and no threshold is
    # chosen, and a real negative cannot trigger it, since a real negative still has reads.
    r = _run(dead_reagent_bed, *BASE)
    assert r.returncode == 0, r.stderr

    assert _row(dead_reagent_bed, "K1", "WT")["state"] == "bound", "the live antigen still answers"

    m4 = _row(dead_reagent_bed, "K1", "M4")
    assert m4["state"] == "never asked", "zero reads means nobody could answer, not that nobody bound"
    assert int(m4["cellsAsked"]) == 0, "cells in a sample where the tag returned nothing do not vote"

    # And the reagent finding is still stated on its own row, for the reagent's sake rather than the
    # answer's.
    qc = pl.read_csv(dead_reagent_bed / "result_qc.csv", infer_schema_length=0)
    never_seen = qc.filter((pl.col("measurement") == "declaredNeverSeen") & (pl.col("entity") == "M4"))
    assert never_seen.height == 1
    assert float(never_seen.row(0, named=True)["value"]) == 0.0


# ---------------------------------------------------------------------------
# Unasked off-target: one clonotype left unsettled, one positively disqualified.
# ---------------------------------------------------------------------------


@pytest.fixture
def off_target_bed(tmp_path):
    """Two clonotypes under "binds the target and nothing on the off-target list".

    The list is OFF1, OFF2, OFF3. Neither sample's panel carries all three, and the two samples omit
    different ones, which is what puts the two clonotypes on opposite sides of the statement.

    KA comes from S1, whose panel omits OFF3: a clean reading on the off-targets it was asked about and an
    unsettled position on the one it was not. KB comes from S2, whose panel omits OFF2 -- but KB **binds**
    OFF1, which S2 did ask.
    """
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\n"
        "S1,AgTarget,TARGET,Target\n"
        "S1,AgOff1,OFF1,Target\n"
        "S1,AgOff2,OFF2,Target\n"
        "S1,Ctrl,CTRL,Control\n"
        "S2,AgTarget,TARGET,Target\n"
        "S2,AgOff1,OFF1,Target\n"
        "S2,AgOff3,OFF3,Target\n"
        "S2,Ctrl,CTRL,Control\n"
    )
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in ("a1", "a2", "a3"):
        rows.append(f"S1,{cell},CTRL,{COMPARATOR}")
        rows.append(f"S1,{cell},TARGET,{BINDING}")
        # Two routes to *not bound* in one clonotype, so the clean off-target list does not rest on either
        # route alone. OFF1 reads low in every cell. OFF2 reads ambient in a1 only and is silent in a2 and
        # a3 -- a tag the SAMPLE measured, which some of its cells read nothing for.
        #
        # OFF2 must appear in at least one of S1's cells. A tag absent from the whole sample is a reagent
        # that produced nothing, which reads *never asked* -- the dead reagent bed above.
        rows.append(f"S1,{cell},OFF1,{BACKGROUND}")
        if cell == "a1":
            rows.append(f"S1,{cell},OFF2,{AMBIENT}")
    for cell in ("b1", "b2", "b3"):
        rows.append(f"S2,{cell},CTRL,{COMPARATOR}")
        rows.append(f"S2,{cell},TARGET,{BINDING}")
        rows.append(f"S2,{cell},OFF1,{BINDING}")
        rows.append(f"S2,{cell},OFF3,{BACKGROUND}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text(
        "sampleId,cellId,setId\n"
        + "".join(f"S1,{cell},KA\n" for cell in ("a1", "a2", "a3"))
        + "".join(f"S2,{cell},KB\n" for cell in ("b1", "b2", "b3"))
    )
    return tmp_path


def test_an_off_target_the_panel_omitted_is_present_and_reads_never_asked(off_target_bed):
    # The row has to exist. Dropping it discards a lead for a question nobody asked. Keeping it as
    # anything settled asserts a clean off-target the run never produced.
    r = _run(off_target_bed, *BASE)
    assert r.returncode == 0, r.stderr

    unasked = _row(off_target_bed, "KA", "OFF3")
    assert unasked["state"] == "never asked"
    assert unasked["unreliableReason"] == "never-offered"
    assert int(unasked["cellsAsked"]) == 0  # no cell of KA was ever offered OFF3

    # And the positions S1 did ask are settled, so the clonotype is unsettled by exactly one position.
    assert _row(off_target_bed, "KA", "TARGET")["state"] == "bound"
    assert _row(off_target_bed, "KA", "OFF1")["state"] == "not bound"
    assert _row(off_target_bed, "KA", "OFF2")["state"] == "not bound"


def test_a_bound_off_target_survives_beside_an_unasked_one(off_target_bed):
    # The other half of the check. The obvious way to satisfy the scenario above is to let any unsettled
    # position make the whole statement unsettled, which sends a demonstrated off-target binder back as a
    # maybe. KB's bound OFF1 must reach the output so a downstream statement can fail KB on it.
    r = _run(off_target_bed, *BASE)
    assert r.returncode == 0, r.stderr

    assert _row(off_target_bed, "KB", "OFF2")["state"] == "never asked"

    bound_off_target = _row(off_target_bed, "KB", "OFF1")
    assert bound_off_target["state"] == "bound"
    assert int(bound_off_target["cellsAnswered"]) == 3

    # The same identity settled in opposite directions for the two clonotypes, each from its own cells. A
    # pipeline that folded the unasked position into the whole statement would make these two look alike.
    assert _row(off_target_bed, "KA", "OFF1")["state"] == "not bound"

    # `set_counts` is what a ranked list is built from, so KB's disqualifying bind has to be countable
    # there too: target plus off-target, two bound.
    counts = pl.read_csv(off_target_bed / "result_set_counts.csv", infer_schema_length=0)
    assert int(counts.filter(pl.col("setId") == "KB").row(0, named=True)["boundCount"]) == 2


# ---------------------------------------------------------------------------
# Support travels with the reading.
# ---------------------------------------------------------------------------


@pytest.fixture
def support_bed(tmp_path):
    """One clonotype spanning two samples whose panels share nothing but the comparator.

    Forty of the clone's cells sit in S1, which offered AGA and not AGB. Three sit in S2, which offered
    AGB and not AGA. Both positions bind, so the states are identical and the only thing separating a
    reading resting on forty cells from one resting on three is the support carried beside it.
    """
    (tmp_path / "panel.csv").write_text(
        "Samples,Name,Sequence,Type\nS1,AgA,AGA,Target\nS1,Ctrl,CTRL,Control\nS2,AgB,AGB,Target\nS2,Ctrl,CTRL,Control\n"
    )
    deep = [f"d{i:02d}" for i in range(40)]
    thin = ["t1", "t2", "t3"]
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in deep:
        rows.append(f"S1,{cell},CTRL,{COMPARATOR}")
        rows.append(f"S1,{cell},AGA,{BINDING}")
    for cell in thin:
        rows.append(f"S2,{cell},CTRL,{COMPARATOR}")
        rows.append(f"S2,{cell},AGB,{BINDING}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text(
        "sampleId,cellId,setId\n"
        + "".join(f"S1,{cell},K1\n" for cell in deep)
        + "".join(f"S2,{cell},K1\n" for cell in thin)
    )
    return tmp_path


def test_a_reading_on_forty_cells_and_one_on_three_are_distinguishable(support_bed):
    # Cells of one clonotype are replicates of one measurement, so how many could answer is how much
    # confidence the reading deserves. Both positions here read *bound*, so a row carrying only the state
    # makes a decision taken on three cells indistinguishable from one taken on forty.
    r = _run(support_bed, *BASE)
    assert r.returncode == 0, r.stderr

    deep = _row(support_bed, "K1", "AGA")
    thin = _row(support_bed, "K1", "AGB")
    assert deep["state"] == thin["state"] == "bound"

    assert (int(deep["cellsAsked"]), int(deep["cellsAnswered"])) == (40, 40)
    assert (int(thin["cellsAsked"]), int(thin["cellsAnswered"])) == (3, 3)
    assert int(deep["cellsAnswered"]) != int(thin["cellsAnswered"])

    # Agreement travels the same way: both readings are unanimous, and a reader has the figure rather than
    # having to infer it from the states.
    assert float(deep["agreement"]) == 1.0 and float(thin["agreement"]) == 1.0


# ---------------------------------------------------------------------------
# Every cell set aside: the question was put and the data cannot settle it.
# ---------------------------------------------------------------------------


@pytest.fixture
def gated_bed(tmp_path):
    """A clonotype whose every cell sits in high comparator background.

    The comparator reads 20 in every cell, so a gate at 10 sets all three aside. The antigen count of 500
    scores 99.85 against a comparator of 20 and binds outright with the gate off, which is what makes the
    *unreliable* reading the gate's doing rather than an absence of signal. The state is reached by
    running the same bed twice, once through the gate and once past it.
    """
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AGA,Target\nS1,Ctrl,CTRL,Control\n")
    cells = ("c1", "c2", "c3")
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in cells:
        # 20 leaves these cells admissible until the gate is what sets them aside. It is also below the
        # high-reference observation line of 100.
        rows.append(f"S1,{cell},CTRL,20")
        rows.append(f"S1,{cell},AGA,{BINDING}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,{cell},K1\n" for cell in cells))
    return tmp_path


def test_a_set_whose_every_cell_was_gated_reads_unreliable_and_never_not_bound(gated_bed):
    # The cells were dropped because their readings could not be trusted, so nothing about the receptor
    # was established. *Not bound* would assert a clean reading the run never produced. *Never asked* would
    # claim the experiment did not put the question, which it did.
    r = _run(gated_bed, *BASE, "--gate-threshold", "10")
    assert r.returncode == 0, r.stderr

    meta = json.loads((gated_bed / "result_run_meta.json").read_text())
    assert meta["cellsSetAside"] == 3, "the gate has to be what removed them"

    gated = _row(gated_bed, "K1", "AGA")
    assert gated["state"] == "unreliable"
    assert gated["unreliableReason"] == "all-cells-gated"
    # The question was put to three cells and none of them could answer it. Reporting zero on both would
    # lose the distinction the state carries.
    assert (int(gated["cellsAsked"]), int(gated["cellsAnswered"])) == (3, 0)

    # Same files, gate off: the identity binds outright. Without this the test would pass just as well
    # over a bed with no signal in it.
    assert _run(gated_bed, *BASE).returncode == 0
    assert _row(gated_bed, "K1", "AGA")["state"] == "bound"


@pytest.fixture
def cutoff_bracket_bed(tmp_path):
    """Two clonotypes differing only in count, one either side of the cutoff.

    Three cells each, so neither reading turns on the vote limits. The comparator is the same in every
    cell, so the only thing separating the two verdicts is where the cutoff sits.
    """
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AGA,Target\nS1,Ctrl,CTRL,Control\n")
    rows = ["sampleId,cellId,tag,umiCount"]
    linker = ["sampleId,cellId,setId"]
    for set_id, count in (("KBELOW", JUST_BELOW_CUTOFF), ("KABOVE", JUST_ABOVE_CUTOFF)):
        for i in range(3):
            cell = f"{set_id.lower()}{i}"
            rows.append(f"S1,{cell},CTRL,{COMPARATOR}")
            rows.append(f"S1,{cell},AGA,{count}")
            linker.append(f"S1,{cell},{set_id}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("\n".join(linker) + "\n")
    return tmp_path


def test_the_cutoff_the_entrypoint_defaults_to_is_seventy_five(cutoff_bracket_bed):
    """The default cutoff, asserted through the CLI rather than as a module constant.

    `test_cutoff_is_seventy_five` pins `BOUND_CUTOFF`; this pins the number a run with no `--cutoff`
    is actually answered under, which is a separate copy in the argparse declaration. Run with BASE,
    which deliberately carries no `--cutoff`.
    """
    r = _run(cutoff_bracket_bed, *BASE)
    assert r.returncode == 0, r.stderr

    # Below the cutoff and above it, on the same comparator. A default anywhere outside
    # (69.02, 79.36] flips one of these two.
    assert _row(cutoff_bracket_bed, "KBELOW", "AGA")["state"] == "not bound"
    assert _row(cutoff_bracket_bed, "KABOVE", "AGA")["state"] == "bound"

    # The bracket only means something if the two counts really are on opposite sides of the shipped
    # number rather than of whatever number happens to be in force. Stated once, here.
    from verdict import BOUND_CUTOFF, specificity_score

    assert specificity_score(JUST_BELOW_CUTOFF, COMPARATOR) < BOUND_CUTOFF
    assert specificity_score(JUST_ABOVE_CUTOFF, COMPARATOR) >= BOUND_CUTOFF


def test_420_an_unasked_off_target_is_reachable_only_because_the_panel_is_keyed_by_sample():
    """An unasked off-target, at the grain the keying decides.

    A clonotype whose cells came from a sample whose panel omitted an off-target must read *never asked*
    there, neither satisfied nor violated. That state is only reachable because what a sample offered is
    worked out per sample, and under a reused panel it is only CORRECT because the identity a barcode
    carries is read from that sample's own declaration.

    The identity is still in the universe, so the unasked position has a row to sit in.
    """
    from emit_verdicts import _build_grouping
    from panel import identity_universe, offered_identities

    panel = pl.DataFrame(
        {
            "tag": ["T1", "T2", "T1", "T2", "T3"],
            "sample": ["s1", "s1", "s2", "s2", "s2"],
            "Identity": ["target", "offA", "target", "offA", "offB"],
        }
    )
    grouping, rule_id, ungrouped, _declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, properties={}, reference_tags=set()
    )
    assert rule_id == "property:Identity"
    assert ungrouped == []

    # s1's panel omits offB entirely, so a set drawn only from s1 was never asked there.
    assert offered_identities(panel, grouping, ["s1"]) == {"target", "offA"}
    assert offered_identities(panel, grouping, ["s2"]) == {"target", "offA", "offB"}

    # ...and offB is still in the universe, so that position exists to hold *never asked*.
    assert identity_universe(panel, grouping) == {"target", "offA", "offB"}


def test_a_reused_barcode_is_read_as_what_its_own_sample_declared():
    """The case the per-sample keying exists for.

    One barcode drawn from a small fixed pool carries a different antigen in each sample, which is how a
    study covers more antigens than it has tags. Each sample's cells must be read against the antigen that
    sample declared, and the identity universe holds both.
    """
    from emit_verdicts import _build_grouping
    from panel import identity_universe

    panel = pl.DataFrame({"tag": ["T1", "T1"], "sample": ["s1", "s2"], "Identity": ["antigenA", "antigenB"]})
    grouping, _, ungrouped, _declared = _build_grouping(
        {"by": "property", "column": "Identity"}, panel, properties={}, reference_tags=set()
    )
    assert grouping == {("T1", "s1"): "antigenA", ("T1", "s2"): "antigenB"}
    assert identity_universe(panel, grouping) == {"antigenA", "antigenB"}
    # Nothing fell back: neither declaration is a "disagreement" under a per-sample panel.
    assert ungrouped == []
