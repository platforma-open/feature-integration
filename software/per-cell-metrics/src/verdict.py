"""Turning a cell's counts into states.

Four steps in production, and the order is load-bearing:

  1. the floor, on the raw count, per cell and per tag
  2. tags combine into an identity by the highest of their counts
  3. the identity's count is read against that cell's own reference reading
  4. the comparison becomes one of the four states

tag-stat emits only observed pairs, so a cell asked about an identity and silent
produces no row. An antigen every cell failed to bind must read *not bound*, not
vanish as though nobody offered it. Production counts those positions analytically in
`silent_tally`. `densify` builds the full grid and is only the test oracle.

The cell key is (sampleId, cellId) throughout: barcodes are bare 16-mers shared
across samples.

Compare `min_umi` in per_cell_metrics.py, which resolves the other way -- below it a
feature is omitted rather than zeroed.

Three frame shapes after step 2. The sparse per-tag frame and the per-identity frame
are keyed by CELL_KEY. `silent_tally` returns one keyed coarser, by (group,
identity), where group defaults to sampleId.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

import numpy as np
import polars as pl
from panel import ANY_SAMPLE, Grouping
from scipy.stats import beta

CELL_KEY = ("sampleId", "cellId")

# Uncalibrated: a declared default the scientist can move, not a fitted line.
DEFAULT_FLOOR = 4


class Floored(NamedTuple):
    counts: pl.DataFrame
    stats: dict[str, int]


def apply_floor(counts: pl.DataFrame, floor: int, reference_tags: set[str]) -> Floored:
    """Zero every (cell, tag) count below `floor`, except the comparator's.

    A floored count reads *not bound*, never *unreliable*: a count that small is not
    distinguishable from none.

    Reference tags are ALWAYS exempt, and there is no switch. The minimum asks whether a
    count is evidence of binding, and a tag declared to be bound by nothing is never
    evidence of binding, so the question does not arise for it.

    `reference_tags` is global by design: a tag is a comparator in every sample or in
    none. The panel's (tag, sample) keying carries what a tag IS, not its role.

    Returns the floored counts and {"readingsFloored", "cellsEmptied"} for this sample's
    QC row. Both assume the SPARSE frame. Never densify first: manufactured rows would
    inflate readingsFloored and count every unbound cell as emptied.
    """
    # Not an optimisation: falling through would count a cell whose only reading is
    # already zero as "emptied", when the floor removed nothing.
    if floor <= 0:
        return Floored(counts, {"readingsFloored": 0, "cellsEmptied": 0})

    # is_in yields null for a null tag, so a null-tag row would escape both the floor and
    # the emptied population. The panel reader never emits one.
    is_ref = pl.col("tag").is_in(list(reference_tags)) if reference_tags else pl.lit(False)
    exempt = is_ref
    below = (pl.col("umiCount") < floor) & ~exempt

    readings_floored = int(counts.select(below.sum()).item())
    out = counts.with_columns(
        pl.when(below).then(pl.lit(0, dtype=pl.Int64)).otherwise(pl.col("umiCount")).alias("umiCount")
    )

    # "Emptied" must follow the same switch. With the comparator exempt it is scoped to
    # non-reference readings: a cell holding only the comparator never had evidence of
    # binding to remove. Scoping one way while flooring the other reports a cell as keeping
    # evidence it lost, or losing evidence it never had. had_evidence deliberately does not
    # filter on umiCount > 0 -- that is the sparse-frame assumption.
    counted = ~exempt
    had_evidence = counts.filter(counted).select(CELL_KEY).unique()
    kept_evidence = out.filter(counted & (pl.col("umiCount") > 0)).select(CELL_KEY).unique()
    cells_emptied = had_evidence.join(kept_evidence, on=CELL_KEY, how="anti").height

    return Floored(out, {"readingsFloored": readings_floored, "cellsEmptied": cells_emptied})


def cells_reading_nothing(floored: pl.DataFrame, cells: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Of `cells`, the ones left with no count on any tag once the minimum has run.

    Not `cellsEmptied` renamed. That counter is scoped to readings the minimum could
    remove, so an exempt comparator is invisible to it. This population is every tag,
    comparator included, and that inclusion is the whole discriminator: a cell whose
    antigen tags all fell below the minimum while its comparator survived took up reagent,
    none of it antigen -- a real negative and a real vote. So it reads the FLOORED frame
    and ignores which tag is which.

    `cells` is passed in because the frame is sparse both ways: a cell with no row read
    nothing on every tag and belongs here, and a cell outside the universe does not.
    Passing the clonotypes' own membership keeps this count from exceeding the clonotype's
    cell count. It changes no verdict, and must not -- these cells vote *not bound*, and
    dropping them would shrink the denominator and make verdicts more positive.
    """
    reading = set(floored.filter(pl.col("umiCount") > 0).select(CELL_KEY).unique().rows())
    return {key for key in cells if key not in reading}


# Shipped defaults. Each is a visible parameter rather than a constant, because nothing
# published sets any of them. The panel minimum GATES rather than tunes: it comes from one
# preprint whose own panels held fifty and a hundred members, and nothing validates it
# lower. Below it, comparing a count against a handful of antigens is not a background
# estimate, so the baseline is wrong rather than conservative. Keep it above the fifteen-tag
# cap of an antibody kit, so a panel declaring no comparator falls to the per-tag
# distribution rung.
DEFAULT_PANEL_MIN_MEMBERS = 25


class ReferenceChoice(str, Enum):
    """Which comparator served. Two runs served differently do not compare.

    EMPTY_DROPLETS is deliberately absent: it needs gene expression and an empty-droplet
    population this block does not receive.

    DISTRIBUTION is handled outside this module. The other two are keyed by cell and built
    by `reference_by_cell`. DISTRIBUTION is keyed by (sample, identity), and its conditions
    cannot be checked before the fit runs, so `reference_by_cell` refuses it loudly.
    """

    # Machine tokens, never prose, identical to the model's `ReferenceSource` union. These
    # cross three boundaries -- run-meta JSON, a p-column DOMAIN, a UI branch -- so prose here
    # would make rewording a sentence a breaking change. Display wording lives in the model's
    # `referenceSources` output. `UnreliableReason` does the opposite, since its value IS the
    # prose and nothing branches on it. DECLARED reads against every declared tag, by the highest.
    DECLARED = "declared"
    PANEL = "panel"
    DISTRIBUTION = "distribution"


# Nothing here derives a default rung, and nothing may. A baseline nobody chose is a
# methodology nobody knows they used. --reference-source is required. A default would be
# the trap: the workflow omits --reference-source whenever the model's value is empty, so
# such a function would silently become the live rule. `served_source` below never picks a
# rung. It only reports that the one asked for cannot serve.


def served_source(
    source: ReferenceChoice,
    reference_tags: set[str],
    panel_size: int,
    min_members: int,
) -> ReferenceChoice:
    """The source asked for, or a refusal naming the condition that failed.

    A baseline is required and a run without one does not happen. There is no bottom rung
    answering everything *unreliable*: a full punchcard of non-answers costs what a real run
    costs and looks like a result.

    Both conditions are properties of the SETTINGS, so they are caught before anything is
    read. The model refuses the same two in `args()`. This is the backstop for a hand-driven
    run.

    The third rung cannot be checked here: whether a sample holds enough cells whose counts
    separate is a property of the DATA.
    """
    if source is ReferenceChoice.DECLARED and not reference_tags:
        raise SystemExit(
            "the declared-baseline rung was selected and this panel declares no baseline tag. "
            "Mark a tag as the baseline in the panel's role column, or select a different baseline "
            "source. A run with no baseline produces no verdicts, which is what this block is for."
        )
    if source is ReferenceChoice.PANEL and panel_size < min_members:
        raise SystemExit(
            f"the other-tags-in-the-cell rung was selected and this panel carries {panel_size} tags, "
            f"below the {min_members} that rung needs. Below it the baseline is not conservative but "
            "wrong, so the condition is a gate rather than a preference. Select a different baseline "
            "source; an antibody panel cannot reach this one, its kits capping at fifteen tags."
        )
    return source


class Reference(NamedTuple):
    by_cell: dict[tuple[str, str], int]
    served: ReferenceChoice


def reference_by_cell(
    counts: pl.DataFrame,
    reference_tags: set[str],
    source: ReferenceChoice,
    cells: list[tuple[str, str]] | None = None,
    panel_size: int = 0,
    min_members: int = DEFAULT_PANEL_MIN_MEMBERS,
) -> Reference:
    """The reference reading per cell, and which source actually served.

    `source` is supplied, never inferred, and `served` always equals it -- there is no rung
    below to fall to. `by_cell` holds a key for every analysed cell, zero where that cell
    showed none of the comparator. A reader still switches on `served`, not on key presence:
    `by_cell.get(key, 0)` would read "not in the analysis" as "the comparator read zero".

    `cells`, where given, is authoritative both ways: the result holds exactly those cells,
    zero-filled. Omit it and the universe comes from the counts frame, so a cell that was
    asked and read nothing goes missing rather than zero.

    Receives the RAW, sparse per-tag frame -- before the minimum and before densification.
    Fed the floored frame, the PANEL median would mix raw reference values with floored
    antigen ones; on a densified frame, manufactured zeros would drag that median toward
    zero for every cell. `reference_tags` is NOT excluded from the PANEL median: that
    comparator is the cell's own readings, and a declared comparator is one of them.
    """
    # Raises where the rung asked for cannot serve from the settings alone. No fall-through,
    # because there is no bottom rung.
    served = served_source(source, reference_tags, panel_size, min_members)

    all_cells = (
        # Deduplicated: a cell with several tag readings would otherwise be revisited
        # once per reading by the zero-fill loop below.
        {(s, c) for s, c in zip(counts["sampleId"].to_list(), counts["cellId"].to_list(), strict=True)}
        if cells is None
        else cells
    )
    # Semi join on the cell list, before either branch aggregates, so a cell outside the
    # analysis is dropped before its rows are combined.
    scoped = (
        counts
        if cells is None
        else counts.join(
            pl.DataFrame(cells, orient="row", schema={"sampleId": pl.String, "cellId": pl.String}),
            on=CELL_KEY,
            how="semi",
        )
    )

    if served is ReferenceChoice.DECLARED:
        # Several declared references combine by taking the HIGHEST, which is `baseline-scope`'s rule
        # for replicates within one group. This block has no scope construct, so no declared property
        # separates any two references, so the whole panel is one group and every declared reference in
        # it is a replicate -- the case that atom settles rather than the cross-group case it forbids.
        # Taking the highest is also what stops a dead reference from making the background look
        # cleaner than it was, and it is what the gate already does with several references, so both
        # roles read them alike.
        #
        # The alternative was refusing a panel that declares two, which sent the scientist back to edit
        # the panel file for a case the corpus had already decided.
        rows = (
            scoped.filter(pl.col("tag").is_in(list(reference_tags)))
            .group_by(CELL_KEY)
            .agg(pl.col("umiCount").max().alias("ref"))
        )
    elif served is ReferenceChoice.PANEL:
        # cast(Int64) truncates rather than rounds, keeping the comparator an integer UMI
        # count like every other reading here.
        rows = scoped.group_by(CELL_KEY).agg(pl.col("umiCount").median().cast(pl.Int64).alias("ref"))
    else:
        # Reachable only if ReferenceChoice gains a member with no branch here. That is a
        # missing implementation, not a fact about this run, so it must not read as
        # "unavailable this time".
        raise SystemExit(f"no comparator implementation for reference source {served.value!r}")

    ref = {(s, c): v for s, c, v in zip(rows["sampleId"], rows["cellId"], rows["ref"], strict=True)}
    # The tag was offered. A cell showing none of it read zero, not nothing.
    for key in all_cells:
        ref.setdefault(key, 0)
    return Reference(ref, served)


def gate_cells(
    reference: dict[tuple[str, str], int],
    threshold: int | None,
) -> tuple[set[tuple[str, str]], int | None]:
    """Which cells a declared gate sets aside, and how many read high.

    ONE threshold does both jobs: *how many are high* needs a high, and only a declared gate
    supplies one. So the cells set aside and the cells counted high are the same cells.

    The gate defaults off, and then the count is None rather than zero. There is no boundary
    to count against, so the run's exposure is reported as the SPREAD of the readings
    instead. A count against a line nobody drew would assert a boundary.

    A sticky cell left in returns as a confident *not bound*, the collapse the four-state
    model prevents, so the exposure is surfaced either way.

    Strictly above the threshold, never at it. `reference-two-roles` says a cell *above* the
    threshold is set aside and that a cell is set aside where a reading *exceeds* it, which is
    the same direction the minimum takes from the other side -- a count of four survives a
    minimum of four.
    """
    if threshold is None:
        return set(), None
    gated = {k for k, v in reference.items() if v > threshold}
    return gated, len(gated)


# The cutoff and the three beta constants are the dominant tool's, inherited rather than
# justified: nothing published argues any of the four. They ship as the default so a run's
# numbers reconcile with what a scientist already has.
BETA_X, BETA_A_OFFSET, BETA_B_OFFSET = 0.925, 1, 3
BOUND_CUTOFF = 75.0

# The population rung's own call, and not this block's to move: under a fitted distribution
# a cell reads *bound* at 0.9 or above, where 0.9 is the probability its count belongs to
# the signal component. The score below is the declared reagent's rule and does not apply
# here -- each baseline brings its own rule, and a run selects one baseline.
#
# NOT A SETTING, and it must not become one. It comes from the literature, so a dial would
# only produce runs that cannot be compared against the work the method came from.
DISTRIBUTION_BOUND_PROBABILITY = 0.9


class State(str, Enum):
    """The four states a verdict takes. There is no fifth.

    NEVER_ASKED means the experiment did not put the identity to those cells. UNRELIABLE
    means it did and the data cannot settle it. Neither is a kind of NOT_BOUND.
    """

    BOUND = "bound"
    NOT_BOUND = "not bound"
    NEVER_ASKED = "never asked"
    UNRELIABLE = "unreliable"


# The two states a reading can settle on. A cell in neither made no comparison, so it is not evidence
# about anything -- not about a clonotype's verdict, and not about how a tag agrees with its siblings.
SETTLED = (State.BOUND.value, State.NOT_BOUND.value)


class UnreliableReason(str, Enum):
    """Why a cell's comparison could not be made. The value is the prose a reader sees,
    the member is what code compares against, so wording can change safely."""

    GATED = "cell set aside by the admissibility gate"
    NO_COMPARATOR = "no comparator for this cell"


def combine_tags_to_identities(counts: pl.DataFrame, grouping: Grouping) -> pl.DataFrame:
    """An identity's reading in a cell is the highest of its tags' counts.

    Resolved through the cell's OWN sample. The grouping is keyed (tag, sample) because the
    panel file is, so a barcode reused across panels contributes to the antigen its own
    sample declared. A cell belongs to exactly one sample, which makes "the highest of its
    tags' counts" well defined under reuse.

    Counts are never added, and summing is not offered. Requiring every tag to clear was
    measured and is the worst option available. Summing would need the reference scaled to a
    summed identity, assuming each tag picks up background at the reference's rate -- tags
    differ by an amount nobody has measured.
    """
    star = {tag: identity for (tag, sample), identity in grouping.items() if sample == ANY_SAMPLE}
    keyed = [(tag, sample, identity) for (tag, sample), identity in grouping.items() if sample != ANY_SAMPLE]
    # Lazy and projected to the four columns the group_by reads. Eagerly, the joined frame carries
    # every column of `counts` plus `identity` at one row per (cell, tag), which measured 7.4 GB
    # across this function's two calls on a 28.5M-row run.
    mapped = (
        counts.lazy()
        .select(*CELL_KEY, "tag", "umiCount")
        .join(
            pl.LazyFrame(
                keyed,
                orient="row",
                schema={"tag": pl.String, "sampleId": pl.String, "identity": pl.String},
            ),
            on=["tag", "sampleId"],
            how="left",
        )
    )
    if star:
        # A panel with no sample dimension declares one mapping over every sample, so star
        # rows fill where the keyed join found nothing. Checked second, so an explicit
        # per-sample declaration always wins.
        mapped = mapped.with_columns(pl.col("identity").fill_null(pl.col("tag").replace_strict(star, default=None)))
    return (
        mapped.filter(pl.col("identity").is_not_null())
        .group_by([*CELL_KEY, "identity"])
        .agg(pl.col("umiCount").max().alias("umiCount"))
        .collect(engine="streaming")
    )


def densify(identities: pl.DataFrame, cells: pl.DataFrame, offered_by_sample: dict[str, set[str]]) -> pl.DataFrame:
    """Every cell against every identity its sample offered, zeros filled in.

    Without this, an antigen every cell failed to bind produces no rows and its failure is
    indistinguishable from a reagent nobody offered.

    The reference implementation, and it must never run in the block: on a realistic run this
    grid is 11-20x the sparse input and does not fit a large panel at all. Production uses
    `silent_tally`, and this is the oracle it is tested against.
    """
    # Guard on the assembled blocks, never on offered_by_sample. A map whose every value is
    # empty -- a sample stained with nothing -- is non-empty itself but contributes no block,
    # and concat of an empty list raises.
    blocks = [
        cells.filter(pl.col("sampleId") == sample).join(pl.DataFrame({"identity": sorted(offered)}), how="cross")
        for sample, offered in sorted(offered_by_sample.items())
        if offered
    ]
    grid = (
        pl.concat(blocks, how="vertical")
        if blocks
        else cells.head(0).with_columns(pl.lit(None, pl.String).alias("identity"))
    )

    return grid.join(identities, on=[*CELL_KEY, "identity"], how="left").with_columns(
        pl.col("umiCount").fill_null(0).cast(pl.Int64)
    )


def specificity_score(antigen_count, reference_count):
    """How specifically the antigen count exceeds the reference: 0-100.

    At antigen_count = 0 this is ~0.0422 at reference_count = 0, and falls for every larger
    reference_count. `silent_tally` relies on a silent admissible cell never scoring BOUND,
    which lets its state be known with no row written. That holds only for a `cutoff`
    strictly above 0.0422 -- at or below, `silent_tally` and the `densify` oracle part
    company with no error raised here. Refusing such a cutoff is the CLI's job.
    """
    a = np.asarray(antigen_count, dtype=float) + BETA_A_OFFSET
    b = np.asarray(reference_count, dtype=float) + BETA_B_OFFSET
    return (1.0 - beta.cdf(BETA_X, a, b)) * 100.0


class Admissibility(NamedTuple):
    """The pair `read_states` and `silent_tally` must share to agree on what "cannot be
    compared" means for a cell.

    Sharing `_admissibility_reason` makes them agree on the *rule*, not on the *arguments*.
    Passing one bundle to both makes disagreement impossible. The disagreement to fear:
    `read_states` given a reference restricted to observed cells while `silent_tally` gets
    the full one, which sends `silentUnreliable` wrong or negative.
    """

    reference: dict[tuple[str, str], int]
    gated: set[tuple[str, str]]
    by_identity: dict[tuple[str, str], int] | None = None
    # The population rung's comparator is not a count. It is a probability per (sample, cell,
    # identity) that the cell's reading belongs to the signal component, and the state is read
    # from it directly. Set means it is the whole comparator, and `reference` and `by_identity`
    # are both empty. A missing key means the fit established nothing for that position.
    probabilities: dict[tuple[str, str, str], float] | None = None


def _admissibility_reason(key: tuple[str, str], identity: str, admissibility: Admissibility) -> UnreliableReason | None:
    """Why this comparison cannot be made, or None if it can be.

    Takes an identity because one rung's comparator depends on it. Cell-keyed rungs answer
    the same for every identity. The per-tag distribution rung fits per (sample, tag), so a
    tag whose counts did not separate leaves only the identities built from it uncomparable.

    `by_identity` distinguishes the two: set means it is the whole comparator and `reference`
    is empty; None means the comparator is keyed by cell. Never merge them. Membership is
    tested, never `get(..., 0)` -- a missing key means no comparator existed, and defaulting
    to 0 reads as "served and found nothing".

    A LOW comparator reading is not a reason. No published line separates thin from usable,
    so the comparison runs and every cell's reference reading is emitted.
    """
    reference, gated, by_identity, probabilities = admissibility
    if key in gated:
        return UnreliableReason.GATED
    if probabilities is not None:
        return None if (key[0], key[1], identity) in probabilities else UnreliableReason.NO_COMPARATOR
    if by_identity is not None:
        return None if (key[0], identity) in by_identity else UnreliableReason.NO_COMPARATOR
    if key not in reference:
        return UnreliableReason.NO_COMPARATOR
    return None


def cell_admissibility_reason(key: tuple[str, str], admissibility: Admissibility) -> UnreliableReason | None:
    """The part of the reason belonging to the CELL, whatever it was asked about.

    Needed by every output keyed by cell rather than by position. Where the comparator is
    keyed by cell this is the whole reason. Where it is keyed by identity it is only the
    gate, because a cell whose identity has no fitted background is a fine cell asked an
    unanswerable question -- calling the CELL uncomparable would misreport every identity,
    including the ones that fitted.
    """
    _reference, gated, by_identity, probabilities = admissibility
    if key in gated:
        return UnreliableReason.GATED
    if by_identity is None and probabilities is None and key not in admissibility.reference:
        return UnreliableReason.NO_COMPARATOR
    return None


def _comparator(key: tuple[str, str], identity: str, admissibility: Admissibility) -> int | None:
    """The reading this comparison is made against, or None where none served.

    None under the population rung, always. That rung's comparator is a fitted distribution
    rather than a reading, and a number here would read as a comparator that served.
    """
    reference, _gated, by_identity, probabilities = admissibility
    if probabilities is not None:
        return None
    if by_identity is not None:
        return by_identity.get((key[0], identity))
    return reference.get(key)


def read_states(identities: pl.DataFrame, admissibility: Admissibility, cutoff: float) -> pl.DataFrame:
    """Give every (cell, identity) row a state.

    Two routes to UNRELIABLE, both recorded in `unreliableReason`: the cell has no comparator,
    or a gate set it aside. Gated cells stay in the frame -- dropping them makes a set whose
    every cell was set aside read *never asked*. The gate is checked first, because a cell it
    set aside was not measured at all.

    Emits umiCount and referenceCount, never the score. `referenceCount` is nullable, and null
    is not 0 -- null means no comparator served, 0 means one served and read nothing. A
    downstream `fill_null(0)` collapses "not measured" into "measured as zero".

    A cell in `identities` but absent from `silent_tally`'s cell list still gets a row here,
    since this function takes no cell list, and `silent_tally` drops it.
    """
    keys = list(zip(identities["sampleId"].to_list(), identities["cellId"].to_list(), strict=True))
    idents = identities["identity"].to_list()
    reasons = [_admissibility_reason(k, i, admissibility) for k, i in zip(keys, idents, strict=True)]
    refs = [_comparator(k, i, admissibility) for k, i in zip(keys, idents, strict=True)]

    df = identities.with_columns(
        pl.Series("referenceCount", refs, dtype=pl.Int64),
        pl.Series("unreliableReason", [r.value if r is not None else None for r in reasons], dtype=pl.String),
    )

    # Each baseline brings its own rule, and the selected baseline decides which one runs. A
    # fitted population hands back a probability per position, and the state is read from it at
    # the rung's own line. The score below is the declared reagent's rule and does not apply
    # here: substituting a fitted background into it would produce a number the method it came
    # from never defines.
    if admissibility.probabilities is not None:
        called = [admissibility.probabilities.get((k[0], k[1], i)) for k, i in zip(keys, idents, strict=True)]
        df = df.with_columns(pl.Series("_pBound", called, dtype=pl.Float64)).with_columns(
            pl.when(pl.col("unreliableReason").is_not_null())
            .then(pl.lit(State.UNRELIABLE.value))
            .when(pl.col("_pBound") >= DISTRIBUTION_BOUND_PROBABILITY)
            .then(pl.lit(State.BOUND.value))
            .otherwise(pl.lit(State.NOT_BOUND.value))
            .alias("state")
        )
        return df.select([*CELL_KEY, "identity", "umiCount", "referenceCount", "state", "unreliableReason"])

    scored = specificity_score(
        df["umiCount"].to_numpy(),
        np.nan_to_num(df["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0),
    )

    df = df.with_columns(pl.Series("_score", scored, dtype=pl.Float64)).with_columns(
        pl.when(pl.col("unreliableReason").is_not_null())
        .then(pl.lit(State.UNRELIABLE.value))
        .when(pl.col("_score") >= cutoff)
        .then(pl.lit(State.BOUND.value))
        .otherwise(pl.lit(State.NOT_BOUND.value))
        .alias("state")
    )

    return df.select([*CELL_KEY, "identity", "umiCount", "referenceCount", "state", "unreliableReason"])


def silent_tally(
    observed: pl.DataFrame,
    cells: pl.DataFrame,
    offered_by_sample: dict[str, set[str]],
    admissibility: Admissibility,
    group_by_cell: dict[tuple[str, str], str] | None = None,
    group_column: str = "sampleId",
) -> pl.DataFrame:
    """Per (group, identity): how many asked cells were never observed, and how they resolve.

    The sparse path -- silent positions are counted, never materialized. `densify` then
    `read_states` is the reference this must agree with, kept only for tests.

    A silently admissible cell's count is 0, and specificity_score(0, r) is ~0.0422 at r = 0
    and smaller beyond. So a silent cell resolves to NOT_BOUND unless the cell itself cannot
    be compared -- a per-cell fact independent of which identity was silent. That holds only
    for a `cutoff` strictly above ~0.0422; at or below, the dense oracle can call such a cell
    BOUND while this reports NOT_BOUND, silently. Refusing such a cutoff is the CLI's job.
    Above the bound, three cheap terms replace a materialized row per silent cell:

        asked              = cells of the group, for every identity offered to one of its members
        observed           = the (cell, identity) rows read_states already produced
        silentUnreliable   = inadmissible cells the group counts toward that identity -
                              inadmissible cells among the observed
        silentNotBound     = asked - observed - silentUnreliable

    `group_by_cell` maps a cell key to the unit the tally reports per. It defaults to None,
    grouping by the cell's own sampleId -- the only grouping under which every member shares
    one offered set, which is what lets `asked` and `total_inadmissible` be computed once per
    group. A group spanning samples with different offered sets has no such guarantee, so
    both terms move inside the identity loop.

    `offered_by_sample` is never regrouped: staining is done per sample. Every cell key in
    `cells` needs an entry in `group_by_cell` where one is given. `group_column` names the key
    column in the returned frame, "sampleId" by default.

    Precondition, unchecked by types: `cells` unique on the cell key, `observed` unique on
    (cell, identity). A duplicated `observed` row is double-counted against totals that count
    the cell once, which can drive `silentUnreliable` negative. The check below makes that
    loud.
    """
    # A duplicated cells row must not count twice -- a legitimate no-op to guard against,
    # unlike a duplicated observed row, which is a contract violation.
    keys = list(dict.fromkeys(zip(cells["sampleId"].to_list(), cells["cellId"].to_list(), strict=True)))
    cell_keys = set(keys)

    def inadmissible(key: tuple[str, str], identity: str) -> bool:
        return _admissibility_reason(key, identity, admissibility) is not None

    obs_keys = list(zip(observed["sampleId"].to_list(), observed["cellId"].to_list(), strict=True))
    obs_identity = observed["identity"].to_list()

    rows: list[tuple[str, str, int, int, int, int]] = []

    if group_by_cell is None:
        # Sample-keyed path. One accumulating pass, never one loop per sample: scanning all of
        # `keys` per sample is O(groups x cells), harmless at 24 samples but quadratic once a
        # wider key groups thousands of sets.
        #
        # It hoists the inadmissible term out of the identity loop, which needs that term to be a fact
        # about the CELL. The fitted rung's comparator is keyed by position, so it is not, and the
        # hoisted count would disagree with `inadmissible()` below. Refused rather than computed: both
        # production callers pass `group_by_cell`, so this is a guard on the next one.
        if admissibility.probabilities is not None:
            raise ValueError(
                "silent_tally was given a position-keyed comparator with no group_by_cell: whether a "
                "cell can be compared then depends on the identity asked about, which the sample-keyed "
                "path cannot hoist. Pass group_by_cell, as both production callers do."
            )
        asked_count: dict[str, int] = {}
        gated_count: dict[str, int] = {}
        no_comparator_count: dict[str, int] = {}
        for k in keys:
            sample = k[0]
            asked_count[sample] = asked_count.get(sample, 0) + 1
            if k in admissibility.gated:
                gated_count[sample] = gated_count.get(sample, 0) + 1
            elif (
                admissibility.by_identity is None
                and admissibility.probabilities is None
                and k not in admissibility.reference
            ):
                # Cell-keyed comparators only. Where the comparator is keyed by identity, or by
                # position as the fitted rung's probabilities are, this is not a property of the cell
                # and the loop below computes it per identity. Testing `reference` under those shapes
                # would disagree with `inadmissible()` below and drive `silentNotBound` negative.
                no_comparator_count[sample] = no_comparator_count.get(sample, 0) + 1

        observed_count: dict[tuple[str, str], int] = {}
        observed_inadmissible_count: dict[tuple[str, str], int] = {}
        for k, ident in zip(obs_keys, obs_identity, strict=True):
            if k not in cell_keys:
                # In `identities` but absent from `cells`: dropped rather than counted against a
                # cell universe that never named it.
                continue
            if ident not in offered_by_sample.get(k[0], frozenset()):
                # Read, but this cell's OWN sample never offered the identity. `asked` counts only
                # members whose own sample offered it, so counting this would draw numerator and
                # denominator from two populations.
                continue
            pair = (k[0], ident)
            observed_count[pair] = observed_count.get(pair, 0) + 1
            if inadmissible(k, ident):
                observed_inadmissible_count[pair] = observed_inadmissible_count.get(pair, 0) + 1

        for sample, offered in sorted(offered_by_sample.items()):
            # `asked` hoists out of the identity loop because a sample offers the same
            # identities to all its cells. The inadmissible term hoists only for cell-keyed
            # comparators. Keyed by identity, a tag whose counts did not separate takes out every
            # cell of the sample for the identities built from it and none of the others, so it
            # is computed inside the loop. That keeps this O(samples x identities).
            asked = asked_count.get(sample, 0)
            gated_here = gated_count.get(sample, 0)
            uncomparable_here = no_comparator_count.get(sample, 0)
            for identity in sorted(offered):
                pair = (sample, identity)
                if admissibility.by_identity is None:
                    total_inadmissible = gated_here + uncomparable_here
                elif pair in admissibility.by_identity:
                    total_inadmissible = gated_here
                else:
                    # No comparator for this identity anywhere in the sample, so every cell is
                    # unreliable against it -- gated ones included, and already counted here once.
                    total_inadmissible = asked
                observed_n = observed_count.get(pair, 0)
                observed_inadmissible_n = observed_inadmissible_count.get(pair, 0)
                silent_unreliable = total_inadmissible - observed_inadmissible_n
                silent_not_bound = asked - observed_n - silent_unreliable
                # Raised, not asserted: stripped under -O these terms stay negative and are summed
                # into the tallies, so the run reports fewer silent positions than it has.
                if asked < 0 or silent_unreliable < 0 or silent_not_bound < 0:
                    raise ValueError(
                        f"negative silent term for {sample!r}/{identity!r} "
                        f"(asked={asked}, silentUnreliable={silent_unreliable}, silentNotBound={silent_not_bound}): "
                        "cells or observed violated the uniqueness precondition documented above"
                    )
                rows.append((sample, identity, asked, observed_n, silent_unreliable, silent_not_bound))
    else:
        # Group-keyed path. A group can mix samples with different offered sets, so neither
        # term hoists above the identity loop. Each group is walked once, member by member,
        # checking that member's OWN sample's offered set. A single count computed before the
        # loop would silently apply to an identity some members never saw.
        keys_by_group: dict[str, list[tuple[str, str]]] = {}
        for k in keys:
            keys_by_group.setdefault(group_by_cell[k], []).append(k)

        observed_count = {}
        observed_inadmissible_count = {}
        for k, ident in zip(obs_keys, obs_identity, strict=True):
            if k not in cell_keys:
                continue
            if ident not in offered_by_sample.get(k[0], frozenset()):
                # Read, but this cell's OWN sample never offered the identity. `asked` counts only
                # members whose own sample offered it, so counting this would draw numerator and
                # denominator from two populations.
                continue
            pair = (group_by_cell[k], ident)
            observed_count[pair] = observed_count.get(pair, 0) + 1
            if inadmissible(k, ident):
                observed_inadmissible_count[pair] = observed_inadmissible_count.get(pair, 0) + 1

        for group in sorted(keys_by_group):
            asked_by_identity: dict[str, int] = {}
            inadmissible_by_identity: dict[str, int] = {}
            for k in keys_by_group[group]:
                # Per identity rather than once per member: with an identity-keyed comparator,
                # whether this member can be compared depends on which identity is asked about.
                for identity in offered_by_sample.get(k[0], set()):
                    asked_by_identity[identity] = asked_by_identity.get(identity, 0) + 1
                    if inadmissible(k, identity):
                        inadmissible_by_identity[identity] = inadmissible_by_identity.get(identity, 0) + 1

            for identity in sorted(asked_by_identity):
                asked = asked_by_identity[identity]
                total_inadmissible = inadmissible_by_identity.get(identity, 0)
                pair = (group, identity)
                observed_n = observed_count.get(pair, 0)
                observed_inadmissible_n = observed_inadmissible_count.get(pair, 0)
                silent_unreliable = total_inadmissible - observed_inadmissible_n
                silent_not_bound = asked - observed_n - silent_unreliable
                # Raised, not asserted: stripped under -O these terms stay negative and are summed
                # into the tallies, so the run reports fewer silent positions than it has.
                if asked < 0 or silent_unreliable < 0 or silent_not_bound < 0:
                    raise ValueError(
                        f"negative silent term for {group!r}/{identity!r} "
                        f"(asked={asked}, silentUnreliable={silent_unreliable}, silentNotBound={silent_not_bound}): "
                        "cells or observed violated the uniqueness precondition documented above"
                    )
                rows.append((group, identity, asked, observed_n, silent_unreliable, silent_not_bound))

    return pl.DataFrame(
        rows,
        orient="row",
        schema={
            group_column: pl.String,
            "identity": pl.String,
            "asked": pl.Int64,
            "observed": pl.Int64,
            "silentUnreliable": pl.Int64,
            "silentNotBound": pl.Int64,
        },
    )
