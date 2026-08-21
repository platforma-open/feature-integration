"""The spec's acceptance scenarios, each driven from files through the CLI.

Every scenario writes a counts CSV, a panel CSV and a linker CSV, runs
`emit_verdicts.py` as a subprocess, and asserts on the CSVs it wrote. Nothing
here builds a per-cell state frame, calls `read_states`, or reaches into a
module: an earlier revision of these scenarios did exactly that and passed
while the pipeline was turning an antigen every cell failed to bind into
*never asked*. A scenario that constructs the states it then reads tests its
own assertion, not the reading.

Three numbers are load-bearing in every bed below, so they are stated once
here rather than rediscovered by whoever next changes a count.

*The cutoff is 75 and the score is a beta function, not a ratio.* Against a
reference of 6, a count of 500 scores 100 and binds, while counts of 50 and 60
score 3.1 and 7.2 and read *not bound* -- large-looking counts that cannot
reach the cutoff. Against a reference of 20 a count of 500 still scores 99.85.
Check the score before asserting a state; `specificity_score(count, reference)`
in verdict.py answers directly.

*The floor is 4.* Any antigen reading of 1-3 is zeroed before anything else
runs, so background counts here sit at 5 or above. A floored reading can also
drag a panel-derived comparator to zero, against which every surviving count
scores near 100, turning a whole run *bound* for a reason that has nothing to
do with the scenario.

*A cell with no comparator reading is inadmissible and votes nowhere.* Every
bed declares a comparator tag whose role value matches `--reference-values`,
and gives every one of its cells a count for it.

Tags are the identities under the default per-tag grouping, so they are named
for the part they play (`TARGET`, `OFF1`) rather than written as barcode
sequences. The pipeline treats a tag as an opaque string.
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
    # Stated, because the CLI requires it: nothing below the model picks a rung for a scientist who
    # did not. This bed declares a comparator tag, which is the rung every scenario here is read under.
    "--reference-source",
    "declared",
    "--output-prefix",
    "result",
]

# A comparator reading every cell shares. Above the floor of 4 so it survives
# it, and well below the high-reference observation line of 100 so nothing in
# these beds is flagged for background it does not have.
COMPARATOR = 6

# Clears the cutoff of 75 against a comparator of 6: the score is 100.
BINDING = 500

# Survives the floor of 4 and scores 0.0 against a comparator of 6. A reading
# that is present and settles *not bound*, as distinct from a cell that was
# asked and produced no row at all.
BACKGROUND = 5


def _verdicts(bed):
    # Read without schema inference throughout: `unreliableReason` is null on a
    # settled row, and polars would otherwise infer the counts back into
    # integers and the reason column's nulls into something a test cannot tell
    # from an empty string.
    return pl.read_csv(bed / "result_verdicts.csv", infer_schema_length=0)


def _row(bed, set_id, identity):
    got = _verdicts(bed).filter((pl.col("setId") == set_id) & (pl.col("identity") == identity))
    assert got.height == 1, f"expected exactly one ({set_id}, {identity}) row, got {got.height}"
    return got.row(0, named=True)


# ---------------------------------------------------------------------------
# Epitope loss: the finding is a failure, and the failure comes from silence.
# ---------------------------------------------------------------------------


@pytest.fixture
def epitope_bed(tmp_path):
    """One clonotype against an unmutated antigen and four point mutants.

    The fourth mutant has **no rows at all** in the counts file. That is the
    whole point of the bed: tag-stat emits only observed (cell, tag) pairs, so
    an antigen every cell failed to bind arrives as nothing, and the reading
    has to recover *not bound* from the panel saying those cells were offered
    it. Writing zero-count rows instead would hand the pipeline the answer.
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
        # The clone binds the unmutated antigen and the first three mutants;
        # the epitope it grabs survives those substitutions.
        for tag in ("WT", "M1", "M2", "M3"):
            rows.append(f"S1,{cell},{tag},{BINDING}")
        # M4 deliberately absent -- not zero, absent.
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,{cell},K1\n" for cell in cells))
    return tmp_path


def test_the_mutant_no_cell_bound_reads_not_bound_not_never_asked(epitope_bed):
    # The scientist's statement is "binds the unmutated antigen and fails on
    # the fourth mutant", so *not bound* is the finding and the run must
    # produce it from silence. Reading M4 as *never asked* -- the failure this
    # scenario exists to catch -- turns the finding into a gap, and the
    # clonotype whose whole value is that failure goes back as unsettled.
    r = _run(epitope_bed, *BASE)
    assert r.returncode == 0, r.stderr

    wt = _row(epitope_bed, "K1", "WT")
    assert wt["state"] == "bound"

    m4 = _row(epitope_bed, "K1", "M4")
    assert m4["state"] == "not bound", "the cells were offered M4 and were silent; silence is a failure to bind"
    assert m4["unreliableReason"] is None, "a settled reading carries no reason for not settling"

    # Every one of the four cells was offered M4 and every one of them voted.
    # A reading resting on the four silences is what makes the failure a
    # finding rather than an absence of data.
    assert (int(m4["cellsCouldAnswer"]), int(m4["cellsAnswered"])) == (4, 4)


def test_the_silent_mutant_is_reported_as_a_reagent_that_produced_nothing(epitope_bed):
    # Two different statements, both true and neither substituting for the
    # other: the verdict says the clone failed to bind M4, and the quality
    # measurement says M4 returned no reads in this run. A reader deciding
    # whether the failure is biology or a dead reagent needs both, and the
    # verdict must not be suppressed to make the second point.
    assert _run(epitope_bed, *BASE).returncode == 0
    qc = pl.read_csv(epitope_bed / "result_qc.csv", infer_schema_length=0)
    never_seen = qc.filter((pl.col("measurement") == "declaredNeverSeen") & (pl.col("entity") == "M4"))
    assert never_seen.height == 1
    assert float(never_seen.row(0, named=True)["value"]) == 0.0
    assert _row(epitope_bed, "K1", "M4")["state"] == "not bound"


# ---------------------------------------------------------------------------
# Unasked off-target: one clonotype left unsettled, one positively disqualified.
# ---------------------------------------------------------------------------


@pytest.fixture
def off_target_bed(tmp_path):
    """Two clonotypes under "binds the target and nothing on the off-target list".

    The list is OFF1, OFF2, OFF3. Neither sample's panel carries all three, and
    the two samples omit different ones, which is what puts the two clonotypes
    on opposite sides of the statement.

    KA comes from S1, whose panel omits OFF3: KA has a clean reading on the
    off-targets it was asked about and an unsettled position on the one it was
    not. KB comes from S2, whose panel omits OFF2 -- but KB **binds** OFF1,
    which S2 did ask. The run disqualified KB on a position it settled, and the
    unasked one changes nothing about that.
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
        # OFF1 read low and OFF2 read nothing at all: both routes to *not
        # bound* in one clonotype, so the clean off-target list does not rest
        # on either route alone.
        rows.append(f"S1,{cell},OFF1,{BACKGROUND}")
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
    # The row has to exist. Dropping it discards a lead for a question nobody
    # asked; keeping it as anything settled asserts a clean off-target the run
    # never produced. Present, in a state that says the statement could not be
    # settled, naming the position responsible.
    r = _run(off_target_bed, *BASE)
    assert r.returncode == 0, r.stderr

    unasked = _row(off_target_bed, "KA", "OFF3")
    assert unasked["state"] == "never asked"
    assert unasked["unreliableReason"] == "never-offered"
    assert int(unasked["cellsCouldAnswer"]) == 0  # no cell of KA was ever offered OFF3

    # And the positions S1 did ask are settled, so the clonotype is unsettled
    # by exactly one position rather than by a bed that says nothing.
    assert _row(off_target_bed, "KA", "TARGET")["state"] == "bound"
    assert _row(off_target_bed, "KA", "OFF1")["state"] == "not bound"
    assert _row(off_target_bed, "KA", "OFF2")["state"] == "not bound"


def test_a_bound_off_target_survives_beside_an_unasked_one(off_target_bed):
    # The other half of the check. The obvious way to satisfy the scenario
    # above -- let any unsettled position make the whole statement unsettled --
    # sends a demonstrated off-target binder back as a maybe, silently, which
    # is the direction that costs money. KB's bound OFF1 must reach the output
    # so a downstream statement can fail KB on it.
    r = _run(off_target_bed, *BASE)
    assert r.returncode == 0, r.stderr

    assert _row(off_target_bed, "KB", "OFF2")["state"] == "never asked"

    bound_off_target = _row(off_target_bed, "KB", "OFF1")
    assert bound_off_target["state"] == "bound"
    assert int(bound_off_target["cellsAnswered"]) == 3

    # The same identity settled in opposite directions for the two clonotypes,
    # each from its own cells. A pipeline that folded the unasked position into
    # the whole statement would make these two look alike.
    assert _row(off_target_bed, "KA", "OFF1")["state"] == "not bound"

    # `set_counts` is what a ranked list is built from, so KB's disqualifying
    # bind has to be countable there too: target plus off-target, two bound.
    counts = pl.read_csv(off_target_bed / "result_set_counts.csv", infer_schema_length=0)
    assert int(counts.filter(pl.col("setId") == "KB").row(0, named=True)["boundCount"]) == 2


# ---------------------------------------------------------------------------
# Support travels with the reading.
# ---------------------------------------------------------------------------


@pytest.fixture
def support_bed(tmp_path):
    """One clonotype spanning two samples whose panels share nothing but the comparator.

    Forty of the clone's cells sit in S1, which offered AGA and not AGB; three
    sit in S2, which offered AGB and not AGA. Both positions bind, so the
    states are identical and the only thing separating a reading resting on
    forty cells from one resting on three is the support carried beside it.
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
    # Cells of one clonotype are replicates of one measurement, so how many
    # could answer is how much confidence the reading deserves. Both positions
    # here read *bound*, so a row carrying only the state makes a decision
    # taken on three cells indistinguishable from one taken on forty -- inside
    # a single clonotype's row set, which is where the two really do differ.
    r = _run(support_bed, *BASE)
    assert r.returncode == 0, r.stderr

    deep = _row(support_bed, "K1", "AGA")
    thin = _row(support_bed, "K1", "AGB")
    assert deep["state"] == thin["state"] == "bound"

    assert (int(deep["cellsCouldAnswer"]), int(deep["cellsAnswered"])) == (40, 40)
    assert (int(thin["cellsCouldAnswer"]), int(thin["cellsAnswered"])) == (3, 3)
    assert int(deep["cellsAnswered"]) != int(thin["cellsAnswered"])

    # Agreement travels the same way: both readings are unanimous, and a
    # reader has the figure rather than having to infer it from the states.
    assert float(deep["agreement"]) == 1.0 and float(thin["agreement"]) == 1.0


# ---------------------------------------------------------------------------
# Every cell set aside: the question was put and the data cannot settle it.
# ---------------------------------------------------------------------------


@pytest.fixture
def gated_bed(tmp_path):
    """A clonotype whose every cell sits in high comparator background.

    The comparator reads 20 in every cell, so a gate at 10 sets all three
    aside. The antigen count of 500 scores 99.85 against a comparator of 20 and
    binds outright with the gate off -- which is what makes the *unreliable*
    reading the gate's doing rather than an absence of signal. Nothing injects
    an `unreliable` row; the state is reached by running the same bed twice,
    once through the gate and once past it.
    """
    (tmp_path / "panel.csv").write_text("Samples,Name,Sequence,Type\nS1,AgA,AGA,Target\nS1,Ctrl,CTRL,Control\n")
    cells = ("c1", "c2", "c3")
    rows = ["sampleId,cellId,tag,umiCount"]
    for cell in cells:
        # 20 leaves these cells admissible until the gate is what sets them
        # aside -- and it is below the
        # high-reference observation line of 100, so the bed is not also
        # exercising that measurement.
        rows.append(f"S1,{cell},CTRL,20")
        rows.append(f"S1,{cell},AGA,{BINDING}")
    (tmp_path / "counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "linker.csv").write_text("sampleId,cellId,setId\n" + "".join(f"S1,{cell},K1\n" for cell in cells))
    return tmp_path


def test_a_set_whose_every_cell_was_gated_reads_unreliable_and_never_not_bound(gated_bed):
    # The cells were dropped because their readings could not be trusted, so
    # nothing about the receptor was established. *Not bound* would assert a
    # clean reading the run never produced, in the direction that costs money;
    # *never asked* would claim the experiment did not put the question, which
    # it did.
    r = _run(gated_bed, *BASE, "--gate-threshold", "10")
    assert r.returncode == 0, r.stderr

    meta = json.loads((gated_bed / "result_run_meta.json").read_text())
    assert meta["cellsSetAside"] == 3, "the gate has to be what removed them"

    gated = _row(gated_bed, "K1", "AGA")
    assert gated["state"] == "unreliable"
    assert gated["unreliableReason"] == "all-cells-gated"
    # The question was put to three cells and none of them could answer it.
    # Reporting zero on both would lose the distinction the state carries.
    assert (int(gated["cellsCouldAnswer"]), int(gated["cellsAnswered"])) == (3, 0)

    # Same files, gate off: the identity binds outright. Without this the test
    # would pass just as well over a bed with no signal in it, where the
    # *unreliable* reading says nothing about the gate.
    assert _run(gated_bed, *BASE).returncode == 0
    assert _row(gated_bed, "K1", "AGA")["state"] == "bound"


def test_420_an_unasked_off_target_is_reachable_only_because_the_panel_is_keyed_by_sample():
    """`420-unasked-off-target`, at the grain the keying decides.

    A clonotype whose cells came from a sample whose panel omitted an off-target must read
    *never asked* there — neither satisfied nor violated. That state is only reachable because
    what a sample offered is worked out per sample (`242`), and under a reused panel it is only
    CORRECT because the identity a barcode carries is read from that sample's own declaration.

    The second half is the other side of `420`: the identity is still in the universe, so the
    unasked position has a row to sit in rather than vanishing from the answer (`205`).
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
    """`260-panel-file-authority@3.0` — the case the keying exists for.

    One barcode drawn from a small fixed pool carries a different antigen in each sample, which is
    how a study covers more antigens than it has tags. Each sample's cells must be read against the
    antigen that sample declared, and the identity universe holds both.
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
