"""Turning a cell's counts into states.

Four steps in production, in this order, and the order is load-bearing:

  1. the floor, on the raw count, per cell and per tag;
  2. tags combine into an identity by the highest of their counts;
  3. the identity's count is read against that cell's own reference reading;
  4. the comparison becomes one of the four states.

tag-stat emits only observed pairs, so a cell asked about an identity and
silent — no positive tag reading for it — produces no row at all, and that
absence is not evidence of nothing: an antigen every cell failed to bind must
still read *not bound*, not disappear as though nobody offered it. Rather than
materialize a row for every such silent position, production counts them
analytically, in `silent_tally`. `densify` — every cell against every
identity its sample offered, zeros filled in — builds that row-per-position
grid and survives only as the test oracle `silent_tally`'s counts are checked
against; it never runs in the block.

The cell key is (sampleId, cellId) throughout: cell barcodes are bare 16-mers
shared across samples.

Compare `min_umi` in per_cell_metrics.py, which is also a UMI threshold on
this data but resolves the other way: a barcode below it makes the feature
absent for that cell, omitted rather than zeroed. Both keep a reading exactly
at the threshold. The difference is the whole point of the floor — a floored
reading is still a reading, and it answers "not bound"; an omitted one leaves
nothing to answer with.

After step 2 this module holds three frame shapes: the sparse per-tag frame
the floor works on and the per-identity frame combining produces from it,
both keyed by CELL_KEY, which is the column vocabulary spanning both; and the
per-(group, identity) frame `silent_tally` returns, keyed one level coarser
than CELL_KEY, not by it. The group defaults to sampleId -- a sample offers
the same identities to every one of its cells -- but callers reducing cells
to a coarser unit than a sample (a clonotype set spanning several samples,
say) pass their own per-cell grouping instead.
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


def apply_floor(
    counts: pl.DataFrame, floor: int, reference_tags: set[str], apply_to_reference: bool = False
) -> Floored:
    """Zero every (cell, tag) count below `floor`, except the comparator's.

    A floored count contributes exactly as a count of zero does — the position
    reads *not bound*, not *unreliable*. The floor is not a statement that the
    reading could not be settled; it is that a count that small is not
    distinguishable from none.

    Reference tags are exempt by default, and `apply_to_reference` turns that
    off. The exemption's ground is that the minimum removes what is not evidence
    *of binding*, and the comparator is not evidence of binding.

    **A second ground for it used to stand here and no longer holds: that
    flooring the comparator "lowers every denominator and shifts the whole run
    toward bound".** Since each rung reads its own source raw, the comparator is
    built from the unfloored frame, so flooring here reaches no denominator at
    all. Measured on the fixture bed: with the exemption removed, the verdicts,
    the per-cell counts and the per-cell scalars are byte-identical, and only
    `readingsFloored` moves.

    So this switch does not change a single verdict. What it changes is the
    ACCOUNTING -- how many readings the run reports as removed, how many cells it
    reports as emptied, and through those, which cells a clonotype counts as
    empty. That is the whole of its effect, and it is why it ships off with a
    setting rather than being decided here.

    `reference_tags` is global by design, not by omission. `panel-file-authority`
    puts a tag's role as the reference with the RUN rather than in the panel file,
    so a tag is a comparator in every sample or in none. The panel's (tag, sample)
    keying carries what each tag *is* — the antigen it holds in that sample — not
    what role it plays, so there is no per-sample control designation to honour.

    Returns the floored counts and {"readingsFloored", "cellsEmptied"}: the two
    counters that land in this sample's row of the QC report.

    Both counters assume the sparse frame this step receives, where every row
    is an observed reading and so a count is at least 1. If densification —
    which manufactures genuine zeros — ever ran before this step, every
    manufactured row would inflate readingsFloored while every unbound cell
    would count as emptied though the floor removed nothing. In production it
    never does: `densify` exists only as the test oracle `silent_tally` is
    checked against, and never runs in the block.
    """
    # Not an optimisation: falling through would count a cell whose only
    # reading is already zero as "emptied", when the floor removed nothing.
    if floor <= 0:
        return Floored(counts, {"readingsFloored": 0, "cellsEmptied": 0})

    # is_in yields null for a null tag, so a null-tag row would escape both the
    # floor and the emptied populations here while flooring normally when no
    # reference is declared. The panel reader never emits one; this is a note
    # for anyone who feeds this an unvalidated frame.
    is_ref = pl.col("tag").is_in(list(reference_tags)) if reference_tags else pl.lit(False)
    exempt = is_ref & pl.lit(not apply_to_reference)
    below = (pl.col("umiCount") < floor) & ~exempt

    readings_floored = int(counts.select(below.sum()).item())
    out = counts.with_columns(
        pl.when(below).then(pl.lit(0, dtype=pl.Int64)).otherwise(pl.col("umiCount")).alias("umiCount")
    )

    # "Emptied" follows the same switch, and must. With the comparator exempt,
    # it is scoped to non-reference readings: a cell holding only the comparator
    # never had evidence of binding for the minimum to remove, and counting it
    # as emptied would report a loss that did not happen. With the comparator
    # subject to the minimum, the comparator IS one of the readings that can be
    # removed, so a cell holding only a floored comparator has been emptied.
    #
    # Scoping the population one way while flooring the other would report a
    # cell as keeping evidence it no longer has, or losing evidence it never had.
    # had_evidence deliberately does not filter on umiCount > 0 — that absence
    # is the sparse-frame assumption above, not an oversight to "symmetrise".
    counted = ~exempt
    had_evidence = counts.filter(counted).select(CELL_KEY).unique()
    kept_evidence = out.filter(counted & (pl.col("umiCount") > 0)).select(CELL_KEY).unique()
    cells_emptied = had_evidence.join(kept_evidence, on=CELL_KEY, how="anti").height

    return Floored(out, {"readingsFloored": readings_floored, "cellsEmptied": cells_emptied})


def cells_reading_nothing(floored: pl.DataFrame, cells: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Of `cells`, the ones left with no count on any tag once the minimum has run.

    This is not `cellsEmptied` under another name. That counter scopes itself to
    the readings the minimum was allowed to remove, so while the comparator is
    exempt it cannot observe the comparator at all. This population is every
    tag, the comparator included, and `support-travels-with-the-reading` makes
    that inclusion the whole discriminator: a cell whose antigen tags all fell
    below the minimum while its comparator survived took up reagent and none of
    it was antigen, which is a real negative and a real vote. Only a cell with
    nothing anywhere read nothing.

    So it reads the FLOORED frame and asks nothing about which tag is which. A
    comparator exempt from the minimum keeps its count and its cell is not
    empty; a comparator subject to the minimum can be zeroed like any other
    reading, and then its cell can be. Moving `--minimum-applies-to-baseline`
    moves this number, which is most of what that switch is for.

    `cells` is passed in rather than read off the frame, because the frame is
    sparse in both directions. A cell with no row at all read nothing on every
    tag and belongs here; a cell outside the universe does not belong here
    however it read. Passing the clonotypes' own membership is what keeps this
    count from ever exceeding the clonotype's cell count.

    It changes no verdict, and must not. Those cells vote *not bound* like any
    other. Dropping them from the vote would shrink the denominator and turn a
    minority into a majority, making verdicts more positive; filtering them out
    of the cell list is the same effect by another route, and would derive the
    cell list from the antigen counts.
    """
    reading = set(floored.filter(pl.col("umiCount") > 0).select(CELL_KEY).unique().rows())
    return {key for key in cells if key not in reading}


# Shipped defaults. Every one is a visible parameter rather than a constant,
# because nothing published sets any of them and a hard-coded line would pretend
# to a basis nobody has.
#
# The panel minimum is the exception to that sentence, and it GATES rather than
# tunes: it comes from one preprint, whose own panels held fifty and a hundred
# members, and nothing validates it lower. Below it, comparing a count against a
# handful of other antigens is not a background estimate, so the baseline it
# permits is not conservative but wrong. Lowering it is a departure rather than
# a preference.
#
# It was 8, which no source supports and which put this rung within reach of an
# antibody panel. It is not: the kits cap at fifteen tags, so a panel that
# declares no comparator now falls to the rung that fits each tag's own
# distribution instead of standing in as its own background.
DEFAULT_PANEL_MIN_MEMBERS = 25
DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE = 100


class ReferenceChoice(str, Enum):
    """Which comparator served. Two runs served differently do not compare.

    EMPTY_DROPLETS is deliberately absent: it needs gene expression and an
    empty-droplet population this block does not receive. Declaring a value the
    software cannot serve would put a crashing option in the dropdown.

    DISTRIBUTION is the odd one and is handled outside this module. The other
    two are keyed by cell and are built by `reference_by_cell`; DISTRIBUTION is
    keyed by (sample, identity), because it fits one distribution per tag across
    a sample's cells. Its two conditions -- enough cells, and counts that
    actually separate -- cannot be checked before the fit runs, so
    `served_source` has nothing to decide about it and `reference_by_cell`
    refuses it loudly rather than inventing a per-cell answer for it.
    """

    # Machine tokens, not prose, and deliberately identical to the model's
    # `ReferenceSource` union. These values cross three boundaries -- the run-meta
    # JSON, a p-column DOMAIN, and a UI branch -- and prose crossing a boundary
    # makes rewording a sentence a breaking change: the "every reading is
    # unreliable" banner was a `=== "no comparator available"` string match, so
    # editing this line for readability would have silently removed the warning.
    # Display wording lives in the model, which already owns the labels for these
    # three choices in its `referenceSources` output.
    #
    # `UnreliableReason` deliberately does the opposite -- its value IS the prose a
    # reader sees -- because nothing branches on it.
    # DECLARED reads against ONE declared tag. A panel declaring several is
    # refused rather than combined -- see `reference_by_cell`.
    DECLARED = "declared"
    PANEL = "panel"
    DISTRIBUTION = "distribution"
    NONE = "none"


# `resolve_default_source` stood here and is deliberately gone rather than
# merely unused. `what-plays-the-baseline` requires that the scientist selects
# among the rungs and that nothing is selected for them, because a baseline
# nobody chose is a methodology nobody knows they used -- and two runs of one
# experiment would then be answered by different rules with nobody choosing
# either.
#
# Gone rather than left in place because leaving it made a trap. The workflow
# omits --reference-source whenever the model's value is empty, so this function
# was one removal-in-the-wrong-layer away from becoming the live rule: strip the
# model's derivation first and this one silently takes over, deriving in the
# layer furthest from the reader. There is now nothing here to take over, and
# --reference-source is required.
#
# `served_source` below is a different thing and stays. It never picks a rung;
# it only reports that the one asked for cannot serve.


def served_source(
    source: ReferenceChoice,
    reference_tags: set[str],
    panel_size: int,
    min_members: int,
) -> ReferenceChoice:
    """The source that can actually be served. Only ever the one asked for, or NONE.

    A comparison that cannot be made is reported as absent rather than
    approximated: a caller who asked for a comparator this run cannot produce
    gets told plainly, never handed a different one it did not ask for.
    """
    if source is ReferenceChoice.DECLARED and not reference_tags:
        return ReferenceChoice.NONE
    if source is ReferenceChoice.PANEL and panel_size < min_members:
        return ReferenceChoice.NONE
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

    `source` is supplied, never inferred; `served_source` decides whether it
    can actually be served, and the result differs from the request in exactly
    one direction — down to NONE.

    `by_cell` is EMPTY when `served` is NONE — not a mapping of zeros. When a
    comparator did serve, it holds a key for every analysed cell, zero where
    that cell showed none of it. So a reader switches on `served`, never on key
    presence: `by_cell.get(key, 0)` would read "no comparator was available" as
    "the comparator read zero", which is the difference between a position that
    could not be settled and one that was settled as not bound.

    `cells`, when given, is authoritative in both directions: the result holds
    exactly those cells, zero-filled where the comparator read nothing. Omit it
    and the cell universe is taken from the counts frame instead, which covers
    only cells with an observed reading — pass it explicitly wherever the
    analysis has a cell list, or a cell that was asked and read nothing will be
    missing rather than zero.

    Receives the RAW, sparse per-tag frame — before the minimum count and
    before densification. Every rung computes its baseline from its own source
    and inherits no other stage's preprocessing; the minimum count acts on the
    identity's reading, which is the numerator, never on the comparator. Fed
    the floored frame instead, the PANEL median mixed raw values (reference
    tags, which the minimum exempts) with floored ones (every antigen tag)
    inside a single median.

    Sparseness matters for the same reason densification is excluded: the PANEL
    median is a median of observed readings, and on a densified frame every
    manufactured zero would drag it toward zero and change the comparator for
    every cell, not just the ones that gained one.

    `reference_tags` is not excluded from the PANEL median: the panel
    comparator is the cell's own readings, and a declared comparator, where
    also present, is one of those readings rather than something held out of
    them.
    """
    served = served_source(source, reference_tags, panel_size, min_members)
    if served is ReferenceChoice.NONE:
        return Reference({}, ReferenceChoice.NONE)

    all_cells = (
        # Deduplicated: a cell with several tag readings otherwise appears once
        # per reading, and the zero-fill loop below would revisit it that many
        # times for no effect but the extra work.
        {(s, c) for s, c in zip(counts["sampleId"].to_list(), counts["cellId"].to_list(), strict=True)}
        if cells is None
        else cells
    )
    # A semi join on the cell list, applied before either branch aggregates, so
    # a cell outside the analysis is dropped before its rows are ever combined
    # rather than combined and then discarded.
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
        if len(reference_tags) > 1:
            # DEFERRED, not unsupported-by-oversight. `baseline-scope` builds the
            # reference as a grouping over a declared property: a panel may carry
            # several comparators, and each serves the group its declaration
            # scopes it to. This block has no group-by half -- a tag is a
            # comparator for the whole panel or for none of it -- so it cannot
            # say WHICH antigens a second comparator belongs to.
            #
            # Refused rather than combined. The block used to take the highest
            # across them, which is a combination, and `baseline-scope` states
            # that references are never combined. It is also what the field does:
            # the ordinary antibody run "rejects a second control outright", and
            # the T-cell run requires one control per allele and rejects two.
            #
            # Refused rather than degraded to no comparator at all, because this
            # is a panel a scientist can fix in a minute and a silent drop to
            # *unreliable* everywhere would not tell them how.
            named = ", ".join(sorted(reference_tags))
            raise SystemExit(
                f"the panel declares {len(reference_tags)} baseline tags ({named}), and this version of "
                "the block reads counts against one baseline tag or none. Reading against several needs "
                "a panel column that says which antigens each one belongs to, which this version does "
                "not have. Mark one tag as the baseline, or clear the role values and choose a different "
                "baseline source."
            )
        # An aggregator over a single tag. `group_by` still runs so a cell with a
        # duplicated reading cannot produce two rows for one cell.
        rows = (
            scoped.filter(pl.col("tag").is_in(list(reference_tags)))
            .group_by(CELL_KEY)
            .agg(pl.col("umiCount").max().alias("ref"))
        )
    elif served is ReferenceChoice.PANEL:
        # cast(Int64) truncates rather than rounds. This used to lean on the
        # thin-reference line: a panel split between 1 and 2 medians to 1.5,
        # truncated to 1, which fell below that line and made the cell read
        # unreliable. That line is gone, so the choice now stands on its own —
        # truncation keeps the comparator an integer count of UMIs, which is
        # what every other reading in this module is.
        rows = scoped.group_by(CELL_KEY).agg(pl.col("umiCount").median().cast(pl.Int64).alias("ref"))
    else:
        # Reachable only if ReferenceChoice gains a member with no aggregation
        # branch here — most plausibly EMPTY_DROPLETS. That is a missing
        # implementation, not a fact about this run, so it must not be
        # reported as "unavailable this time".
        raise SystemExit(f"no comparator implementation for reference source {served.value!r}")

    ref = {(s, c): v for s, c, v in zip(rows["sampleId"], rows["cellId"], rows["ref"], strict=True)}
    # The tag was offered; a cell showing none of it read zero, not nothing.
    for key in all_cells:
        ref.setdefault(key, 0)
    return Reference(ref, served)


def gate_cells(
    reference: dict[tuple[str, str], int],
    threshold: int | None,
    observation_line: int = DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE,
) -> tuple[set[tuple[str, str]], int]:
    """Which cells a declared gate sets aside, and how many read high regardless.

    The gate defaults off. The exposure count is returned either way, so a run's
    exposure is visible to a scientist who has left it off — a sticky cell left
    in returns as a confident *not bound*, which is the collapse the four-state
    model exists to prevent.
    """
    # The observation line is independent of the gate: it measures how many
    # cells sat in high background, which is a fact about the run whether or
    # not anything was set aside. Folding it into the threshold would make the
    # count mean "cells the gate removed" and lose the measurement entirely.
    high = sum(1 for v in reference.values() if v >= observation_line)
    if threshold is None:
        return set(), high
    return {k for k, v in reference.items() if v >= threshold}, high


# The cutoff and the three beta constants are the dominant tool's, inherited
# rather than justified: nothing published argues any of the four over other
# values. They ship as the default so a run's numbers reconcile with what a
# scientist already has.
BETA_X, BETA_A_OFFSET, BETA_B_OFFSET = 0.925, 1, 3
BOUND_CUTOFF = 75.0


class State(str, Enum):
    """The four states a verdict takes. There is no fifth.

    NEVER_ASKED means the experiment did not put the identity to those cells.
    UNRELIABLE means it did and the data cannot settle it. Neither is a kind of
    NOT_BOUND, and collapsing either into it makes a claim the data does not
    support.
    """

    BOUND = "bound"
    NOT_BOUND = "not bound"
    NEVER_ASKED = "never asked"
    UNRELIABLE = "unreliable"


class UnreliableReason(str, Enum):
    """Why a cell's comparison could not be made. The value is the prose that
    reaches a reader; the member is what code compares against, so the wording
    can change without breaking a caller."""

    GATED = "cell set aside by the admissibility gate"
    NO_COMPARATOR = "no comparator for this cell"


def combine_tags_to_identities(counts: pl.DataFrame, grouping: Grouping) -> pl.DataFrame:
    """An identity's reading in a cell is the highest of its tags' counts.

    Resolved through the cell's OWN sample. The grouping is keyed (tag, sample)
    because the panel file is, so a barcode reused across panels contributes to
    the antigen its own sample declared rather than to whichever one a
    dataset-wide map happened to pick. A cell belongs to exactly one sample,
    which is what makes "the highest of its tags' counts, in a cell" well defined
    under reuse.

    Counts are not added together and summing is not offered. Requiring every
    tag to clear was measured and is the worst option available. Summing would
    need the reference scaled to a summed identity, which assumes each tag picks
    up background at the rate the reference does — and tags differ in how
    readily they are taken up, by an amount nobody has measured.
    """
    star = {tag: identity for (tag, sample), identity in grouping.items() if sample == ANY_SAMPLE}
    keyed = [(tag, sample, identity) for (tag, sample), identity in grouping.items() if sample != ANY_SAMPLE]
    mapped = counts.join(
        pl.DataFrame(
            keyed,
            orient="row",
            schema={"tag": pl.String, "sampleId": pl.String, "identity": pl.String},
        ),
        on=["tag", "sampleId"],
        how="left",
    )
    if star:
        # A panel with no sample dimension declares one mapping over every sample, so
        # the star rows fill wherever the keyed join found nothing. Checked second so
        # an explicit per-sample declaration always wins over the global one.
        mapped = mapped.with_columns(pl.col("identity").fill_null(pl.col("tag").replace_strict(star, default=None)))
    mapped = mapped.filter(pl.col("identity").is_not_null())
    return mapped.group_by([*CELL_KEY, "identity"]).agg(pl.col("umiCount").max().alias("umiCount"))


def densify(identities: pl.DataFrame, cells: pl.DataFrame, offered_by_sample: dict[str, set[str]]) -> pl.DataFrame:
    """Every cell against every identity its sample offered, zeros filled in.

    tag-stat emits only observed pairs, so without this an antigen every cell
    failed to bind produces no rows and its failure is indistinguishable from a
    reagent nobody offered. The epitope-mapping case turns on exactly that
    distinction: there, *not bound* is the finding.

    This is the reference implementation. Production uses `silent_tally`
    instead: on a realistic run this grid is 11-20x the sparse input and does
    not fit a large panel at all. This function exists so a test can hold it
    up as the oracle `silent_tally` is checked against, never to run in the
    block itself.
    """
    # Guard on the assembled blocks, not on offered_by_sample: a map whose every
    # value is empty — a sample stained with nothing — is non-empty itself but
    # contributes no block, and concat of an empty list raises. That shape is
    # exactly what a property test probing a stained-with-nothing sample builds.
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

    At antigen_count = 0 this is specificity_score(0, 0) ~= 0.0422 at
    reference_count = 0 and falls for every larger reference_count. That is
    the module's central claim: `silent_tally` relies on a silent admissible
    cell never scoring BOUND, which is what lets its state be known without a
    row ever being written for it. The claim holds only for `cutoff` strictly
    above 0.0422 — a cutoff at or below that bound breaks the equivalence
    between `silent_tally` and the `densify` oracle with no error raised
    here. This module does not refuse such a cutoff; refusing one is the
    CLI's job.
    """
    a = np.asarray(antigen_count, dtype=float) + BETA_A_OFFSET
    b = np.asarray(reference_count, dtype=float) + BETA_B_OFFSET
    return (1.0 - beta.cdf(BETA_X, a, b)) * 100.0


class Admissibility(NamedTuple):
    """The pair `read_states` and `silent_tally` must agree on to agree on
    what "cannot be compared" means for a cell.

    Sharing `_admissibility_reason` makes both functions agree on the
    *rule*; it does nothing to make them agree on the *arguments* the rule is
    applied to, since each call site built its own pair. Bundling the
    pair here and passing the same one to both makes disagreement — e.g.
    `read_states` given a reference restricted to observed cells while
    `silent_tally` gets the full one, which sends `silentUnreliable` wrong or
    negative — impossible by construction rather than by discipline.
    """

    reference: dict[tuple[str, str], int]
    gated: set[tuple[str, str]]
    by_identity: dict[tuple[str, str], int] | None = None


def _admissibility_reason(key: tuple[str, str], identity: str, admissibility: Admissibility) -> UnreliableReason | None:
    """Why this comparison cannot be made, or None if it can be.

    Takes an identity because one rung's comparator depends on it. Rungs
    keyed by cell — the declared reagent, the panel's own readings — answer the
    same way for every identity the cell was asked about: a cell with no
    comparator has none against anything. The rung that fits a tag's own
    distribution does not: it fits per (sample, tag), so a tag whose counts did
    not separate leaves the identities built from it with no comparator while
    every other identity in the same cell has one.

    `by_identity` is what distinguishes the two. When it is set it is the whole
    comparator and `reference` is empty; when it is None the comparator is
    keyed by cell. A caller must never merge them.

    Membership is tested, never `get(..., 0)`: a missing key means no
    comparator existed, and defaulting it to 0 would read as "the comparator
    served and found nothing" — a settled comparison rather than the absence of
    one.

    A LOW comparator reading is not a reason. `count-becomes-a-state` removed
    the thin-reference branch rather than filling it in, because no published
    line separates thin from usable: the comparison still runs, and every
    cell's reference reading is emitted so a reader can see what a verdict
    rested on.
    """
    reference, gated, by_identity = admissibility
    if key in gated:
        return UnreliableReason.GATED
    if by_identity is not None:
        return None if (key[0], identity) in by_identity else UnreliableReason.NO_COMPARATOR
    if key not in reference:
        return UnreliableReason.NO_COMPARATOR
    return None


def cell_admissibility_reason(key: tuple[str, str], admissibility: Admissibility) -> UnreliableReason | None:
    """The part of the reason that belongs to the CELL, whatever it was asked about.

    An output keyed by cell rather than by position needs this: the per-cell
    scalars, the punchcard's silent-position fallback, the set-level reason.
    Where the comparator is keyed by cell this is the whole reason. Where it is
    keyed by identity it is only the gate, because a cell whose identity has no
    fitted background is a fine cell asked an unanswerable question -- calling
    the CELL uncomparable there would report the wrong thing, and would report
    it about every identity including the ones that fitted.
    """
    _reference, gated, by_identity = admissibility
    if key in gated:
        return UnreliableReason.GATED
    if by_identity is None and key not in admissibility.reference:
        return UnreliableReason.NO_COMPARATOR
    return None


def _comparator(key: tuple[str, str], identity: str, admissibility: Admissibility) -> int | None:
    """The reading this comparison is made against, or None where none served."""
    reference, _gated, by_identity = admissibility
    if by_identity is not None:
        return by_identity.get((key[0], identity))
    return reference.get(key)


def read_states(identities: pl.DataFrame, admissibility: Admissibility, cutoff: float) -> pl.DataFrame:
    """Give every (cell, identity) row a state.

    Two routes to UNRELIABLE and they mean different things, both recorded in
    `unreliableReason`: the cell has no comparator, or an admissibility gate set
    the cell aside. Gated cells stay in the frame — dropping them made a set
    whose every cell was set aside read *never asked* instead of *unreliable*.
    The gate is checked first: a cell the gate set aside was not measured at
    all, so nothing about its comparator can be the reason reported.

    Emits umiCount and referenceCount, never the score. Re-derivation under a
    new grouping needs the counts, and no binding level may leave the block.

    `referenceCount` is nullable, and null is not 0: null means no comparator
    served this cell at all, 0 means a comparator served and read nothing. A
    downstream `fill_null(0)` on this column destroys exactly the distinction
    this module argues for everywhere else — collapsing "not measured" into
    "measured as zero".

    A cell present in `identities` but absent from the cell list
    `silent_tally` is given is emitted a row here — this function does not
    take a cell list to check against — and is silently dropped by
    `silent_tally`, which only counts cells it was told about.
    """
    keys = list(zip(identities["sampleId"].to_list(), identities["cellId"].to_list(), strict=True))
    idents = identities["identity"].to_list()
    reasons = [_admissibility_reason(k, i, admissibility) for k, i in zip(keys, idents, strict=True)]
    refs = [_comparator(k, i, admissibility) for k, i in zip(keys, idents, strict=True)]

    df = identities.with_columns(
        pl.Series("referenceCount", refs, dtype=pl.Int64),
        pl.Series("unreliableReason", [r.value if r is not None else None for r in reasons], dtype=pl.String),
    )

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

    The sparse path: silent positions are counted, never materialized.
    `densify` followed by `read_states` is the reference this must agree
    with, kept only for tests: on a realistic panel the dense grid is
    11-20x the sparse input and does not fit at all.

    A silently admissible cell's count is 0, and specificity_score(0, r) is
    specificity_score(0, 0) ~= 0.0422 at r = 0 and smaller for every larger r.
    So a silent cell resolves to NOT_BOUND unless the cell itself cannot be
    compared (gated or no comparator), which is a
    per-cell fact independent of which identity was silent — but only when
    `cutoff` is strictly above that ~0.0422 bound (see `specificity_score`).
    At or below it the dense oracle can call a silent admissible cell BOUND
    while this function still reports it NOT_BOUND, silently: refusing such a
    cutoff is the CLI's job, not this function's. Above the bound, three
    cheap terms replace a materialized row per silent cell:

        asked              = cells of the group, for every identity offered to one of its members
        observed           = the (cell, identity) rows read_states already produced
        silentUnreliable   = inadmissible cells the group counts toward that identity −
                              inadmissible cells among the observed
        silentNotBound     = asked − observed − silentUnreliable

    `observed` is `read_states`' output on the sparse frame — one row per
    (cell, identity) pair `combine_tags_to_identities` actually produced, state
    already resolved. Nothing here inspects an identity's silent reading; the
    admissibility check that decides silentUnreliable never takes an identity.

    `group_by_cell` maps a cell key to the unit the tally reports per. It
    defaults to None, which groups by the cell's own sampleId — the original
    behaviour, and the only grouping under which every member of a group is
    guaranteed to share one offered set. That guarantee is what lets `asked`
    and `total_inadmissible` be computed once per group below rather than
    once per (group, identity): a sample offers the same identities to every
    one of its cells. A group that can span samples with different offered
    sets — a clonotype set spanning several samples, say — does not have
    that guarantee: whether a member counts toward `identity` depends on
    whether THAT MEMBER'S OWN SAMPLE offered `identity`, which can differ
    member to member, so this function computes both terms inside the
    identity loop for that case instead of hoisting them above it.
    `offered_by_sample` itself is never regrouped: what a panel offered is a
    property of the staining, done per sample, so it stays keyed by sample
    regardless of what `group_by_cell` reports. Every cell key present in
    `cells` must have an entry in `group_by_cell` when one is given.

    `group_column` names the key column in the returned frame — "sampleId"
    by default, matching the default grouping; a caller passing a custom
    `group_by_cell` should also pass a `group_column` that names what the
    values in it actually are.

    Precondition, unchecked by types: `cells` must be unique on the cell key,
    and `observed` unique on (cell, identity). A duplicated `cells` row is
    harmless — it is deduplicated below — but a duplicated `observed` row is
    not: it is double-counted against totals that count the cell once, which
    can drive `silentUnreliable` negative (verified: -1 for a single
    duplicated observed row on an inadmissible cell). The assertion below
    turns that into a loud failure rather than a silently wrong number.

    A cell present in `identities` but absent from `cells` is emitted a row
    by `read_states`, which takes no cell list to check against, and is
    silently dropped here, where `cells` is the cell universe.
    """
    # A duplicated cells row must not count twice: unlike a duplicated
    # observed row (see the precondition above), this one is a legitimate
    # no-op to guard against, not a contract violation to surface.
    keys = list(dict.fromkeys(zip(cells["sampleId"].to_list(), cells["cellId"].to_list(), strict=True)))
    cell_keys = set(keys)

    def inadmissible(key: tuple[str, str], identity: str) -> bool:
        return _admissibility_reason(key, identity, admissibility) is not None

    obs_keys = list(zip(observed["sampleId"].to_list(), observed["cellId"].to_list(), strict=True))
    obs_identity = observed["identity"].to_list()

    rows: list[tuple[str, str, int, int, int, int]] = []

    if group_by_cell is None:
        # Sample-keyed path: unchanged from before generalisation. Single
        # accumulating pass, not one loop per sample: scanning all of `keys`
        # once per sample in offered_by_sample is O(groups x cells) and
        # harmless at 24 samples but quadratic once a wider key groups
        # thousands of sets. `k[0]` is the sample directly — no need for a
        # cell->sample dict when every key already carries it.
        asked_count: dict[str, int] = {}
        gated_count: dict[str, int] = {}
        no_comparator_count: dict[str, int] = {}
        for k in keys:
            sample = k[0]
            asked_count[sample] = asked_count.get(sample, 0) + 1
            if k in admissibility.gated:
                gated_count[sample] = gated_count.get(sample, 0) + 1
            elif admissibility.by_identity is None and k not in admissibility.reference:
                # Cell-keyed comparators only. Where the comparator is keyed by
                # identity this term is not a property of the cell at all, and
                # it is computed per identity in the loop below instead.
                no_comparator_count[sample] = no_comparator_count.get(sample, 0) + 1

        observed_count: dict[tuple[str, str], int] = {}
        observed_inadmissible_count: dict[tuple[str, str], int] = {}
        for k, ident in zip(obs_keys, obs_identity, strict=True):
            if k not in cell_keys:
                # In `identities` but absent from `cells`: read_states emitted
                # a row for it, and it is dropped here rather than counted
                # against a cell universe that never named it.
                continue
            if ident not in offered_by_sample.get(k[0], frozenset()):
                # Read, but this cell's OWN sample never offered the identity,
                # so the cell was never asked about it. `asked` below counts
                # only members whose own sample offered it, so counting this
                # reading would draw the numerator and the denominator from two
                # different populations. Where enough silent cells absorb the
                # imbalance it does not even raise: it displaces a silent
                # cell's real vote with one from a cell that was never asked.
                continue
            pair = (k[0], ident)
            observed_count[pair] = observed_count.get(pair, 0) + 1
            if inadmissible(k, ident):
                observed_inadmissible_count[pair] = observed_inadmissible_count.get(pair, 0) + 1

        for sample, offered in sorted(offered_by_sample.items()):
            # `asked` is hoisted out of the identity loop because a sample
            # offers the same identities to every one of its cells, so it does
            # not depend on which identity is being tallied. That precondition
            # is what makes the hoist valid, and it still holds.
            #
            # The inadmissible term no longer hoists in general. Where the
            # comparator is keyed by cell it does, and the two counters below
            # add up to the old one. Where it is keyed by identity, a tag whose
            # counts did not separate takes out every cell of the sample for the
            # identities built from it and none of the others, so the term is
            # computed inside the loop -- from the same two per-sample counters,
            # which is why this stays O(samples x identities) rather than
            # becoming O(cells x identities).
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
                    # No comparator for this identity anywhere in the sample, so
                    # every cell of it is unreliable against this identity --
                    # the gated ones included, already counted here once.
                    total_inadmissible = asked
                observed_n = observed_count.get(pair, 0)
                observed_inadmissible_n = observed_inadmissible_count.get(pair, 0)
                silent_unreliable = total_inadmissible - observed_inadmissible_n
                silent_not_bound = asked - observed_n - silent_unreliable
                # Raised rather than asserted. Stripped under -O these terms stay negative and are
                # summed into the tallies, so the run reports fewer silent positions than it has -- a
                # wrong count with nothing to show it, which is what the precondition exists to catch.
                if asked < 0 or silent_unreliable < 0 or silent_not_bound < 0:
                    raise ValueError(
                        f"negative silent term for {sample!r}/{identity!r} "
                        f"(asked={asked}, silentUnreliable={silent_unreliable}, silentNotBound={silent_not_bound}): "
                        "cells or observed violated the uniqueness precondition documented above"
                    )
                rows.append((sample, identity, asked, observed_n, silent_unreliable, silent_not_bound))
    else:
        # Group-keyed path: a group can mix members from samples with
        # different offered sets, so neither term can be hoisted above the
        # identity loop the way the sample-keyed path hoists them. Instead,
        # each group is walked once, member by member, checking that
        # member's OWN sample's offered set and accumulating a per-identity
        # count as it goes — one pass per group, producing every identity's
        # `asked`/`total_inadmissible` together, rather than one count
        # computed before the identity loop that would silently apply to an
        # identity some members' samples never offered.
        keys_by_group: dict[str, list[tuple[str, str]]] = {}
        for k in keys:
            keys_by_group.setdefault(group_by_cell[k], []).append(k)

        observed_count = {}
        observed_inadmissible_count = {}
        for k, ident in zip(obs_keys, obs_identity, strict=True):
            if k not in cell_keys:
                continue
            if ident not in offered_by_sample.get(k[0], frozenset()):
                # Read, but this cell's OWN sample never offered the identity,
                # so the cell was never asked about it. `asked` below counts
                # only members whose own sample offered it, so counting this
                # reading would draw the numerator and the denominator from two
                # different populations. Where enough silent cells absorb the
                # imbalance it does not even raise: it displaces a silent
                # cell's real vote with one from a cell that was never asked.
                continue
            pair = (group_by_cell[k], ident)
            observed_count[pair] = observed_count.get(pair, 0) + 1
            if inadmissible(k, ident):
                observed_inadmissible_count[pair] = observed_inadmissible_count.get(pair, 0) + 1

        for group in sorted(keys_by_group):
            asked_by_identity: dict[str, int] = {}
            inadmissible_by_identity: dict[str, int] = {}
            for k in keys_by_group[group]:
                # Checked per identity rather than once per member: where the
                # comparator is keyed by identity, whether this member can be
                # compared depends on which identity is being asked about.
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
                # Raised rather than asserted. Stripped under -O these terms stay negative and are
                # summed into the tallies, so the run reports fewer silent positions than it has -- a
                # wrong count with nothing to show it, which is what the precondition exists to catch.
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
