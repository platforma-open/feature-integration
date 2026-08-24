"""The entrypoint: counts, a panel and a cell list become a four-state verdict.

Composes the reading in one order, and the order is load-bearing at every step. The
floor works on the raw per-(cell, tag) counts. A cell's reference reading is taken from
the floored frame. Tags combine into an identity by the highest of their counts. The
identity's count is read against that cell's own reference. A set's cells combine by
majority. Reversing any pair changes the answer: flooring after combining would floor
one reading where two were taken, and taking the reference before the floor would
compare against a number the floor has already been applied to elsewhere.

**The grid of every cell against every identity is never built.** A silent cell -- asked
about an identity and showing no reading for it -- scores `specificity_score(0, r)`, at
most ~0.0422 and falling as the reference rises, so it settles *not bound* unless the
cell itself cannot be compared. `silent_tally` counts those positions analytically,
because on a realistic panel the grid is 11-20x the sparse input and a pMHC panel does
not fit at all. Two consequences are enforced here rather than downstream: a `--cutoff`
at or below that ~0.0422 bound is refused, and verdict.py's row-per-position reference
implementation is never called from production, which the test suite asserts by checking
this file does not name it.

`offered` is keyed by SAMPLE throughout and is never regrouped by set. Staining is done
per sample, so a set spanning two samples was offered whatever either panel offered, and
`combine_cells` takes that union itself. Keying the map by set instead makes every lookup
miss, reads every offered set as empty, and raises nothing.

One `Admissibility` bundle is built and handed to `read_states`, `combine_cells` and
`self_disagreement` alike, so they cannot be given different reference dicts and then
disagree about which cells "cannot be compared" -- which surfaces as a silent-position
count that is wrong or negative rather than as an error.

Every frame is sorted before it is written. `combine_tags_to_identities` groups without
maintaining order, so an unsorted frame varies run to run. A p-column's identity is its
content, and an unstable byte order costs every downstream node its dedup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Collection
from typing import NamedTuple

import numpy as np
import polars as pl
from combine import (
    DEFAULT_MIN_AGREEMENT,
    DEFAULT_MIN_VOTERS,
    attach_competitor_notes,
    combine_cells,
    self_disagreement,
    set_counts,
)
from panel import (
    ANY_SAMPLE,
    Grouping,
    consistent_properties,
    default_grouping,
    identity_universe,
    offered_identities,
    panel_read_mismatch,
    property_columns,
    read_panel,
)
from qc_measures import (
    DEFAULT_LINES,
    MEASUREMENTS,
    Coverage,
    Reading,
    Status,
    antigen_count_deciles,
    deciles_of,
    per_antigen_measures,
    reads_per_cell,
    roll_up,
    sibling_disagreement,
    status_for,
)
from tag_distribution import (
    DEFAULT_DISTRIBUTION_MIN_CELLS,
    TagFits,
    fit_tag_probabilities_by_pair,
)
from verdict import (
    BOUND_CUTOFF,
    DEFAULT_FLOOR,
    DEFAULT_PANEL_MIN_MEMBERS,
    Admissibility,
    Reference,
    ReferenceChoice,
    State,
    UnreliableReason,
    apply_floor,
    cell_admissibility_reason,
    cells_reading_nothing,
    combine_tags_to_identities,
    gate_cells,
    read_states,
    reference_by_cell,
    specificity_score,
)

CellKey = tuple[str, str]


def _identity_probabilities(fits, grouping) -> dict[tuple[str, str, str], float]:
    """Per (sample, cell, identity), the highest probability among the identity's own tags.

    `tags-combine-by-the-highest` fixes the combination: an identity's reading in a cell is
    the highest of its tags and never their sum, because tags differ in uptake and a sum
    would need the baseline scaled to match. The same rule applies to a probability -- the
    identity is bound in that cell where any one of its tags says so, which is also how
    `what-plays-the-baseline` reads a population rung's identity.

    A (tag, sample) pair that established nothing contributes nothing, so an identity all of
    whose tags missed carries no key and reads *unreliable* rather than a low probability.
    """
    out: dict[tuple[str, str, str], float] = {}
    for row in fits.probabilities.iter_rows(named=True):
        identity = grouping.get((row["tag"], row["sampleId"])) or grouping.get((row["tag"], ANY_SAMPLE))
        if identity is None:
            continue
        key = (row["sampleId"], row["cellId"], identity)
        p = float(row["pBound"])
        if p > out.get(key, -1.0):
            out[key] = p
    return out


def _cell_keyed_reference(counts, reference_tags, source, analysed_cells, panel_size, args) -> Reference:
    """The comparator for the rungs keyed by cell: a declared reagent, or the panel's own readings.

    Raw counts, never floored. Each rung computes its baseline from its own source, and
    the minimum acts on the identity's reading -- the numerator -- never on the comparator.
    The floored frame would make the panel rung's median a mixture of raw values (reference
    tags, which the minimum exempts) and floored ones. The declared rung is unaffected
    either way, since a reference tag's reading is already exempt.
    """
    return reference_by_cell(
        counts,
        reference_tags,
        source,
        cells=analysed_cells,
        panel_size=panel_size,
        min_members=args.panel_min_members,
    )


# A silent cell's count is zero, and a zero count's best possible score is
# specificity_score(0, 0). At or below it, the analytic silent count and the
# row-per-position reference part company over a silent admissible cell, quietly: one
# calls it bound, the other not bound, and nothing raises. `silent_tally` states that
# refusing such a cutoff belongs to the CLI.
ANALYTIC_CUTOFF_BOUND = float(specificity_score(0, 0))

# The pivoted per-identity summary costs one column per identity, so it is emitted only
# for a panel small enough that a wide frame is still a table a reader can open. Declared
# rather than derived, since nothing published says where a table stops being readable,
# and deliberately well under the thousand-plus identities a pMHC panel carries.
IDENTITY_SUMMARY_MAX_IDENTITIES = 100

# A rollup is reported in the same frame as the measurements it aggregates, as a row whose
# measurement is the rollup itself. A measurement is an axis value here, so a level's
# summary costs a row rather than a column.
ROLLUP = "rollup"
ROLLUP_COUNTS = "The worst status among this level's measurements, and how much of it was checked."
# The rollup has no declaration to borrow a readable name from, and a row reading `rollup`
# beside rows reading `readsPerCell` leaves the reader guessing which is which.
ROLLUP_LABEL = "Worst status at this level"

MEASUREMENT_BY_ID = {m.id: m for m in MEASUREMENTS}


def _rolls_up(measurement: str) -> bool:
    """Whether a measurement's status reaches its level's rollup. Unknown ids do, as before."""
    declared = MEASUREMENT_BY_ID.get(measurement)
    return True if declared is None else declared.rolls_up


class QcRow(NamedTuple):
    """One measurement at one level entity, before its declaration is attached.

    `status` and `coverage` are both carried because a measurement's own status is not
    recoverable from a coverage triple: `roll_up` reports *not evaluated* for a level with
    nothing judgeable in it, so a row computed and left unjudged would come back saying
    nobody looked. The triple says how much was checked. The status says whether what was
    checked is wrong.

    `panel_id` is set on tag-level and identity-level rows and left empty on the rest. A
    panel carries the worst status among those measurements, so those rows have to say
    which panel they belong to.
    """

    level: str
    entity: str
    measurement: str
    value: float | None
    detail: str
    panel_id: str
    status: Status | None
    coverage: Coverage


def _write_sorted(frame: pl.DataFrame, path: str, by: list[str]) -> None:
    """Write a frame in a fixed row order, header-only when it has no rows.

    Every frame reaching here is built with an explicit schema, so an empty one still
    carries its columns and writes a header. A consumer meeting a header-only frame knows
    the step ran and found nothing. One meeting an empty file cannot tell that from a step
    that never ran.
    """
    frame.sort(by).write_csv(path)


def _read_columns(path: str, columns: tuple[str, ...], what: str) -> pl.DataFrame:
    """Read a CSV as strings, keeping the named columns and stripping them.

    Stripped because these columns are join keys against the panel, whose reader strips
    `tag` and `sample` for the same reason. A tag written " AAAA " on one side and "AAAA"
    on the other joins to nothing, and reports the barcode as both undeclared and never
    seen.
    """
    frame = pl.read_csv(path, infer_schema_length=0)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise SystemExit(f"{what} {path!r} has no column(s) {missing}; columns are {frame.columns}")
    return frame.select([pl.col(c).str.strip_chars().fill_null("") for c in columns])


def _read_counts(path: str) -> pl.DataFrame:
    """The counts frame, with umiCount as an integer, or a curated exit naming the bad value.

    `_read_columns` reads every column as a string and fills nulls with "", so a blank cell
    and a decimal both survive to the cast. A bare `.cast` dies there as a raw polars
    traceback naming neither the file nor the column, the one thing a reader needs.
    """
    counts = _read_columns(path, ("sampleId", "cellId", "tag", "umiCount"), "counts file")
    umi = counts["umiCount"].cast(pl.Int64, strict=False)
    offenders = [raw for raw, cast in zip(counts["umiCount"], umi, strict=True) if cast is None]
    if offenders:
        shown = ", ".join(repr(v) for v in offenders[:5])
        raise SystemExit(
            f"counts file {path!r} has {len(offenders)} umiCount value(s) that are not whole numbers: "
            f"{shown}. A UMI count is a count of observations; a blank or a decimal is not one."
        )
    return counts.with_columns(umi.alias("umiCount"))


def _json_arg(raw: str | None, flag: str):
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag} is not valid JSON: {exc}") from exc


# What joins several grouping columns into one identity key. A scientist may group on more
# than one column, and the identity is the distinct combination of their values, so one
# string has to carry them all. A panel value containing this separator would let two
# different combinations produce one key. That is reported rather than silently merged, and
# the run continues, because refusing the panel would reject a file that reads correctly
# under every other grouping.
GROUPING_KEY_SEPARATOR = " | "


def _grouping_columns(rule: dict, declared: list[str]) -> list[str]:
    """The columns a property rule names, as a list.

    Accepts `columns: [...]`, and the older `column: "..."` because a project stored before
    the rule took a list carries that shape. Reading both costs one function. The
    alternative is a data migration over every stored project.
    """
    raw = rule.get("columns")
    if raw is None:
        single = rule.get("column") or ""
        raw = [single] if single else []
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise SystemExit(f"--grouping columns must be a list of strings; got {raw!r}")
    named = [c for c in raw if c]
    if not named:
        raise SystemExit("--grouping names no column; give one or more, or use {'by':'tag'}")
    missing = [c for c in named if c not in declared]
    if missing:
        raise SystemExit(f"--grouping names {missing}, which the panel does not declare: {declared}")
    return named


def _build_grouping(
    rule: dict | None,
    panel: pl.DataFrame,
    properties: dict[str, dict[str, str]],
    reference_tags: set[str],
) -> tuple[Grouping, str, list[str]]:
    """The tag -> identity map the run reads at, and the id of the rule behind it.

    A property grouping is built from `consistent_properties`, never from the panel column.
    The panel reader strips `tag` and `sample` and carries property values through exactly
    as written, so reading the column directly makes " Spike " and "Spike" two identities
    that no clean fixture would reveal.

    Reference tags are excluded here rather than by `identity_universe`, which takes no
    reference tags and never will -- one place decides, so the two cannot drift. Leaving
    them in would give the comparator an identity of its own, read by comparing it against
    itself.

    A tag the grouping column says nothing about keeps its own identity rather than
    vanishing. Dropping it would remove a declared reagent from the answer with nothing
    downstream able to tell the panel was short.
    """
    by_tag = default_grouping(panel, reference_tags)
    # Type-checked before it is read as one. `--grouping '"tag"'` is valid JSON and not a
    # mapping, and reaching `.get` on it raises an AttributeError instead of the usage
    # message written two lines below for exactly this mistake.
    if rule is not None and not isinstance(rule, dict):
        raise SystemExit(
            f"--grouping must be a JSON object, {{'by':'tag'}} or {{'by':'property','columns':[...]}}; got {rule!r}"
        )
    if rule is None or rule.get("by") == "tag":
        # The per-tag grouping groups on no column, so it declares nothing of its identities.
        return by_tag, "per-tag", [], {}
    if rule.get("by") != "property":
        raise SystemExit(f"--grouping must be {{'by':'tag'}} or {{'by':'property','columns':[...]}}; got {rule!r}")

    columns = _grouping_columns(rule, property_columns(panel))

    # Read PER PANEL ROW, never through `consistent_properties`. The panel declares per tag
    # and sample, so a value differing between a tag's rows is a declaration -- this barcode
    # carries that antigen in that sample -- not a disagreement to collapse. The tag-grain
    # accessor discards exactly the information the keying exists to carry, and every reused
    # barcode then falls back to standing alone under its raw sequence. Values are stripped
    # here for the same reason `consistent_properties` strips them.
    grouping: Grouping = {}
    ungrouped_pairs: list[tuple[str, str]] = []
    # What each identity was grouped ON, recorded here because this is the one place that
    # knows both the column and the row's value. `panel-file-authority` makes a grouped-on
    # column a declaration of the identity, unique by construction: every member carries the
    # same value, because that value is what put it there. It cannot be recovered later from
    # tag-grain agreement, since a reused barcode has none.
    declared: dict[str, dict[str, str]] = {}
    flagged: set[str] = set()
    rows = zip(
        panel["tag"].to_list(),
        panel["sample"].to_list(),
        *(panel[c].to_list() for c in columns),
    )
    for tag, sample, *raw_values in sorted(rows):
        if tag in reference_tags:
            continue
        values = [(v or "").strip() for v in raw_values]
        # ALL named columns must carry a value. A combination missing one component is not
        # that combination, and supplying the absent one would invent a declaration.
        if all(values):
            identity = GROUPING_KEY_SEPARATOR.join(values)
            grouping[(tag, sample)] = identity
            declared.setdefault(identity, {}).update(dict(zip(columns, values)))
            flagged.update(v for v in values if GROUPING_KEY_SEPARATOR.strip() in v)
        else:
            # A pair the grouping column says nothing about keeps its own identity rather
            # than vanishing. Dropping it would remove a declared reagent from the answer
            # with nothing downstream able to tell the panel was short.
            grouping[(tag, sample)] = tag
            ungrouped_pairs.append((tag, sample))
    # Reported as distinct TAGS, never pairs. The returned list is a contract: it travels in
    # the run meta as `tagsWithoutGroupingValue`, and the punchcard counts it in a banner
    # naming barcodes rather than (barcode, sample) pairs.
    ungrouped = sorted({tag for tag, _sample in ungrouped_pairs})
    if ungrouped:
        # Also returned, not only logged. A property the file does not carry narrows what can
        # be answered, and that narrowing has to be visible in the output rather than in a log
        # line nobody reads afterwards. These tags are answered under a grouping that could
        # not place them, so a bare barcode sits among the family identities.
        print(
            f"[emit-verdicts] {len(ungrouped)} tag(s) carry no value for every one of {columns} and "
            f"stand as their own identity: {ungrouped[:8]}",
            file=sys.stderr,
        )
    if flagged:
        print(
            f"[emit-verdicts] {len(flagged)} panel value(s) contain "
            f"{GROUPING_KEY_SEPARATOR.strip()!r}, which joins grouping columns: {sorted(flagged)[:8]}. "
            "Two combinations may share one identity key.",
            file=sys.stderr,
        )
    return grouping, "property:" + "|".join(columns), ungrouped, declared


def _linker_frame(grouping: Grouping) -> pl.DataFrame:
    """Which identities each tag feeds -- one row per distinct (tag, identity).

    Not keyed by sample. The linker lets a tag-keyed figure sit beside an identity-keyed
    verdict, and neither side carries a sample: verdicts are (set, identity) over clonotypes
    that span samples, and the per-tag figures are run-level. An axis no joined table has
    disambiguates nothing -- it makes the join malformed.

    Many-to-many by construction: under (tag, sample) grouping one tag can feed a different
    identity in each sample. Distinct rows matter, because two tags of one identity would
    otherwise emit the same key twice, and duplicate axis keys break a grid silently,
    rendering one row and an ellipsis with no error. The sample component is therefore read
    and discarded, ANY_SAMPLE included.
    """
    rows = {(tag, identity) for (tag, _sample), identity in grouping.items()}
    return pl.DataFrame(
        sorted(rows),
        orient="row",
        schema={"tag": pl.String, "identity": pl.String},
    ).with_columns(pl.lit(1, dtype=pl.Int64).alias("1"))


def _identity_labels(
    grouping: Grouping,
    properties: dict[str, dict[str, str]],
    feature_col: str,
    rule_id: str,
    disagreed: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """A readable name per identity, never two identities under one name.

    Under a property grouping the identity is the property value, already the name a reader
    recognises. Under the per-tag grouping the identity is a barcode, so the panel's feature
    name stands in, and where two barcodes carry the same name the tag is appended.

    `disagreed` is the exception, and it applies to BOTH branches, because both lose a label
    the same way. `consistent_properties` drops a property a tag's rows disagree about, so
    the tag has no value to group on and no feature name to borrow, and stands under its raw
    barcode -- the least readable thing this can produce, at the moment a reader most needs
    to understand what happened. Such an identity is labelled with the names it DID declare,
    joined: `SARS-TRI-S_WT / SARS-TRI-S_WT__alt1`. The reagent stays recognisable and the
    conflict stays visible.

    Which column's disagreements arrive here depends on the rule, and the caller resolves
    that before calling.

    The uniqueness rule applies to joined names too. Two tags can disagree about the grouping
    column while declaring the SAME pair of names, joining to one string -- so the fallback
    would put two identities under one label. Where any label repeats, joined or plain, the
    identity is appended.
    """
    if rule_id != "per-tag":
        joined = {tag: " / ".join(values) for tag, values in (disagreed or {}).items() if values}
        by_identity = {identity: joined.get(identity, identity) for identity in set(grouping.values())}
        repeated = Counter(by_identity.values())
        return {
            identity: (f"{label} ({identity})" if repeated[label] > 1 else label)
            for identity, label in by_identity.items()
        }
    # Three rungs, in this order: the name the samples agreed on, else the names they
    # disagreed about joined, else the bare barcode for a tag the panel named nowhere. The
    # collision rule below already covers the joined strings.
    joined = {tag: " / ".join(values) for tag, values in (disagreed or {}).items() if values}
    # Over the IDENTITIES, never the grouping's keys. Under the per-tag grouping an identity
    # is a tag, but the grouping is keyed by (tag, sample), so its keys are pairs. Iterating
    # them looks up a tuple in `properties`, finds nothing, and drops every label back to the
    # bare barcode this function exists to avoid.
    names = {
        tag: (properties.get(tag, {}).get(feature_col) or joined.get(tag) or tag) for tag in set(grouping.values())
    }
    collisions = Counter(names.values())
    return {tag: (f"{name} ({tag})" if collisions[name] > 1 else name) for tag, name in names.items()}


# The key column of result_identity_properties.csv. A panel column of the same name would
# collapse into the key as the frame is built: the property would not be dropped, it would
# silently BECOME the identity. Such a column is excluded from the export and reported.
IDENTITY_KEY_COLUMN = "identity"


def _identity_properties(
    grouping: Grouping,
    properties: dict[str, dict[str, str]],
    columns: list[str],
    declared: dict[str, dict[str, str]],
    disagreed: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """Per identity, the panel declarations that hold of it.

    A declaration reaches an identity two ways, and `panel-file-authority` fixes both.

    The columns the scientist GROUPED ON arrive in `declared`, from the builder that formed
    the identities. They are declarations by construction: every member carries the same
    value, because that value is what put it there. They are taken rather than tested, and
    they must be -- a reused barcode has no tag-grain agreement to test.

    Every OTHER column holds only where all of the identity's member tags agree.

    Whatever the panel says consistently about an identity's tags must travel with that
    identity's verdicts, so a reader sees the declaration wherever the reading appears.
    Without this a downstream reader sees that an identity was bound and not what the
    scientist declared it to be. The rule is `consistent_properties`' own rule lifted one
    grain: there it holds across a tag's ROWS, here across an identity's TAGS. A property
    differing between member tags is omitted, neither blanked nor resolved to a winner.

    A tag that declares nothing does not block its neighbours. `disagreed` separates that
    silence from a tag whose own rows contradict each other, which without it reaches the
    test below as the empty string and is filtered out like a blank cell. On a panel with
    barcode reuse that inverts the outcome: on a real sixteen-row panel, an identity whose
    five member tags declared six different antigen names came back carrying ONE member's
    name, because four had contradicted themselves into silence and the survivor then agreed
    with nobody but itself. A member that contradicted itself is a disagreement, not a
    silence, and it blocks the property. Strictly more omission and never more assertion.

    Reference tags need no exclusion: `_build_grouping` keeps them out of the grouping.
    """
    # Distinct member tags per identity. The grouping is keyed (tag, sample), so one tag
    # reaches an identity once per sample that declares it there. The membership test keeps a
    # tag from counting twice -- a repeat would misreport how many tags an identity holds.
    tags_of: dict[str, list[str]] = {}
    for (tag, _sample), identity in sorted(grouping.items()):
        members = tags_of.setdefault(identity, [])
        if tag not in members:
            members.append(tag)

    conflicted = disagreed or {}
    held: dict[str, dict[str, str]] = {}
    for identity, tags in tags_of.items():
        # Seeded with what the identity was grouped on. Those columns are settled, so the
        # agreement test below skips them rather than re-deciding them from a grain that
        # cannot answer.
        agreed: dict[str, str] = dict(declared.get(identity, {}))
        for column in columns:
            if column in agreed:
                continue
            # A member that contradicted itself blocks the property. Checked BEFORE the
            # values are gathered, because such a member contributes nothing to them and
            # would otherwise look like one that declared nothing.
            if any(tag in conflicted.get(column, {}) for tag in tags):
                continue
            values = {v for v in (properties.get(tag, {}).get(column, "") for tag in tags) if v}
            if len(values) == 1:
                agreed[column] = next(iter(values))
        held[identity] = agreed
    return held


def _panel_id(tags: frozenset[str]) -> str:
    """A stable id for a declared tag set.

    No panel file names its panel, so the id is derived from the sorted tag list and is the
    same in every re-run of the same declaration. Where one panel covers every sample, the
    axis takes a single value and drops out.
    """
    return hashlib.sha256("\t".join(sorted(tags)).encode()).hexdigest()[:12]


def _declared_by_sample(panel: pl.DataFrame, samples: list[str]) -> dict[str, frozenset[str]]:
    """Each sample's declared tag set, with the unkeyed panel applying to all."""
    everywhere = set(panel.filter(pl.col("sample") == ANY_SAMPLE)["tag"].to_list())
    return {
        sample: frozenset(everywhere | set(panel.filter(pl.col("sample") == sample)["tag"].to_list()))
        for sample in samples
    }


def _cells_by_set(linker: pl.DataFrame) -> dict[str, list[CellKey]]:
    """Set membership from the linker, each cell listed once under its set.

    `combine_cells` asserts the map is disjoint, so a cell listed under two sets fails loudly
    there rather than being counted twice into a tally that counts every cell once.
    """
    members: dict[str, list[CellKey]] = {}
    seen: set[tuple[str, CellKey]] = set()
    for sample_id, cell_id, set_id in linker.iter_rows():
        key = (sample_id, cell_id)
        if (set_id, key) in seen:
            continue
        seen.add((set_id, key))
        members.setdefault(set_id, []).append(key)
    return {set_id: sorted(keys) for set_id, keys in sorted(members.items())}


def count_by_set(cells_by_set: dict[str, list], population: set) -> dict[str, int]:
    """How many of each clonotype's cells fall in `population`.

    Two populations use it: cells a gate set aside, and cells that read nothing at all. Both
    are properties of the cell rather than of a position, since a cell that answered nothing
    answered nothing at every identity. Every clonotype appears, zeros included, because a
    reader must not have to tell "none of them" apart from "column missing". A caller writing
    into the run record drops the zeros itself, since that file is parsed on every render.
    """
    return {set_id: sum(1 for key in cells if key in population) for set_id, cells in sorted(cells_by_set.items())}


def _pivot_identity_summary(verdicts: pl.DataFrame, universe: set[str]) -> tuple[pl.DataFrame, pl.DataFrame, bool]:
    """The per-set verdict row and its support, one column per identity in each.

    Pivoted onto the set axis alone, because the block that consumes this drops a column
    carrying an axis the clonotype anchor does not have, with no error. Gated on identity
    count: the pivot costs a column per identity, and a large panel would turn one artifact
    into a thousand.

    The second frame is the readout's, and its cell carries everything a reader needs to ask
    "why is this mark this colour": `state|answered|couldAnswer|agreement|reason|bound`.
    `agreement` and `reason` are empty where they do not apply. `bound` is last because it
    was appended, so a reader that destructures the first five fields positionally still
    decodes a value written before it existed.

    No score, and no binding level. `binary-narrowing` forbids a reading of the antigen
    counts as a level or an order from leaving this block, so the cell explains a verdict by
    what it RESTS on -- how many cells could answer, how many did, how far they agreed.

    One column rather than five, for two reasons. `support-travels-with-the-reading` obliges
    both counts to travel with a verdict *wherever it appears*, and a punchcard drawn from
    the state pivot alone would not. And the support cannot arrive as sibling columns: a
    column name here IS an antigen name from a customer's panel file, so any suffix marking a
    support column is a name some panel is entitled to use, and a grid pairs cells only by
    position, which no import guarantees.

    The state pivot is left as it is, because lead selection reads it and a compound value
    would not filter.
    """
    if len(universe) > IDENTITY_SUMMARY_MAX_IDENTITIES or verdicts.height == 0:
        sets = verdicts.select("setId").unique() if verdicts.height else pl.DataFrame(schema={"setId": pl.String})
        return sets, sets, False
    ordered = ["setId", *sorted(universe)]
    states = verdicts.pivot(on="identity", index="setId", values="state").select(ordered)
    # Every part is cast and null-filled before joining. concat_str propagates a null through
    # the whole value, so one absent agreement would blank the state beside it.
    punch = verdicts.with_columns(
        pl.concat_str(
            [
                pl.col("state"),
                pl.col("cellsAnswered").cast(pl.String).fill_null(""),
                pl.col("cellsCouldAnswer").cast(pl.String).fill_null(""),
                pl.col("agreement").cast(pl.String).fill_null(""),
                pl.col("unreliableReason").cast(pl.String).fill_null(""),
                # Sixth, and APPENDED rather than inserted, so a reader that destructures
                # the first five fields positionally still reads them correctly and a project
                # whose last run predates this field renders unchanged. The expansion needs
                # it: at each identity, how many of its cells read bound.
                pl.col("cellsBound").cast(pl.String).fill_null(""),
            ],
            separator="|",
        ).alias("punch")
    ).pivot(on="identity", index="setId", values="punch")
    return states, punch.select(ordered), True


# A run whose cell count passes this gets no per-cell punchcard. The frame below is the
# DENSE per-cell-per-identity grid the rest of this module goes out of its way never to
# build -- 11-20x the sparse input on a realistic panel. The readout needs the grid, so it is
# built here, and bounded here, because "needs it" is not "at any size". Above the line the
# export is skipped and the page says so, which is a readout a reader can act on. A run that
# dies importing a Parquet file is not.
CELL_PUNCH_MAX_CELLS = 200_000


def _pivot_cell_punch(
    states: pl.DataFrame,
    cells_by_set: dict[str, list[CellKey]],
    offered_by_sample: dict[str, set[str]],
    admissibility: Admissibility,
    universe: set[str],
) -> tuple[pl.DataFrame, bool]:
    """One row per cell, one column per identity: that cell's own reading, not its set's verdict.

    The same four states the set-level card uses, and for the same reason: a cell asked about
    an identity always resolves to one of them. Three come straight from `read_states`. The
    fourth is structural -- an identity no sample holding this cell offered is NEVER_ASKED --
    and it is the only way a position here is blank.

    **A cell with no row in `states` is not an absence.** It was asked and read nothing, its
    count is zero, and a zero count resolves the same way every time: NOT_BOUND, unless the
    cell cannot be compared, in which case UNRELIABLE. That is `silent_tally`'s rule, not
    re-derived here because the deciding function, `_admissibility_reason`, is the one both
    `read_states` and `silent_tally` already call. Drawing a silent cell as an empty position
    would contradict the arithmetic that produced its set's verdict, where the same cell
    voted.

    `setId` travels as a COLUMN rather than an axis. The readout shows one clonotype at a
    time and filters on it, and a cell belongs to exactly one set.
    """
    members = [(sample, cell, set_id) for set_id, keys in sorted(cells_by_set.items()) for sample, cell in keys]
    ordered_identities = sorted(universe)
    empty = pl.DataFrame(schema={"sampleId": pl.String, "cellId": pl.String, "setId": pl.String})
    if not members or not ordered_identities:
        return empty, False
    # Both gates, and the identity one is the same limit the set-level pivot uses: a column
    # per identity is a p-column per identity, whichever axis the rows are on.
    if len(ordered_identities) > IDENTITY_SUMMARY_MAX_IDENTITIES or len(members) > CELL_PUNCH_MAX_CELLS:
        return empty, False

    member_frame = pl.DataFrame(
        members, orient="row", schema={"sampleId": pl.String, "cellId": pl.String, "setId": pl.String}
    )
    offered_frame = pl.DataFrame(
        [(sample, identity) for sample, ids in sorted(offered_by_sample.items()) for identity in sorted(ids)],
        orient="row",
        schema={"sampleId": pl.String, "identity": pl.String},
    )
    # The cell's own half of the reason, the admissibility gate, is one row per member rather
    # than one per member and identity, because no identity changes whether a cell was set
    # aside. The other half, where a comparator is keyed by identity, is joined below as
    # (sample, identity): a frame of samples by identities, thousands of rows against the
    # grid's tens of millions.
    reasons = pl.DataFrame(
        [
            (
                sample,
                cell,
                (lambda r: r.value if r is not None else None)(
                    cell_admissibility_reason((sample, cell), admissibility)
                ),
            )
            for sample, cell, _ in members
        ],
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "cellReason": pl.String},
    )

    # Joined to `offered` rather than crossed with the universe: a position no sample holding
    # the cell offered must not appear at all, or the silent rule below would resolve a
    # question nobody asked. Where the comparator is keyed by identity, a (sample, identity)
    # with no fitted background is uncomparable for every cell of that sample, and only for
    # that identity. Carried as the pairs that DID fit, so the missing ones fall out of a left
    # join as nulls.
    fitted = (
        pl.DataFrame(
            sorted(admissibility.by_identity),
            orient="row",
            schema={"sampleId": pl.String, "identity": pl.String},
        ).with_columns(pl.lit(True).alias("_fitted"))
        if admissibility.by_identity is not None
        else None
    )

    grid = (
        member_frame.join(offered_frame, on="sampleId", how="inner")
        .join(reasons, on=["sampleId", "cellId"], how="left")
        .join(
            states.select("sampleId", "cellId", "identity", "state", "unreliableReason"),
            on=["sampleId", "cellId", "identity"],
            how="left",
        )
    )
    if fitted is not None:
        grid = grid.join(fitted, on=["sampleId", "identity"], how="left").with_columns(
            pl.when(pl.col("cellReason").is_not_null())
            .then(pl.col("cellReason"))
            .when(pl.col("_fitted").is_null())
            .then(pl.lit(UnreliableReason.NO_COMPARATOR.value))
            .otherwise(None)
            .alias("cellReason")
        )

    grid = grid.with_columns(
        pl.when(pl.col("state").is_not_null())
        .then(pl.col("state"))
        .when(pl.col("cellReason").is_not_null())
        .then(pl.lit(State.UNRELIABLE.value))
        .otherwise(pl.lit(State.NOT_BOUND.value))
        .alias("cellState"),
        # The reason a POSITION is unreliable where one was recorded, and the cell's own
        # reason where the position is silent. Never both: a recorded row already carries
        # whichever applied.
        pl.when(pl.col("unreliableReason").is_not_null())
        .then(pl.col("unreliableReason"))
        .otherwise(pl.col("cellReason"))
        .alias("reason"),
    )

    # How many identities this cell read BOUND, over the identities it was asked. Counted
    # before the pivot, where it is one group_by, and from the resolved state, so a silent
    # position counts as the not-bound it is.
    bound_counts = (
        grid.group_by("sampleId", "cellId")
        .agg((pl.col("cellState") == State.BOUND.value).sum().alias("boundIdentities"))
        .with_columns(pl.col("boundIdentities").cast(pl.Int64))
    )

    # `state|reason`, two fields and nothing else. The set-level punch carries six because a
    # verdict rests on counts a reader needs beside it. A cell IS the evidence, so there is
    # nothing to report about how much of it there was.
    punch = grid.with_columns(
        pl.concat_str([pl.col("cellState"), pl.col("reason").fill_null("")], separator="|").alias("punch")
    ).pivot(on="identity", index=["sampleId", "cellId"], values="punch")

    # Every identity gets a column even where no cell was offered it, so the readout's columns
    # are the panel rather than whatever this run happened to ask.
    for identity in ordered_identities:
        if identity not in punch.columns:
            punch = punch.with_columns(pl.lit(None, dtype=pl.String).alias(identity))

    return (
        punch.join(member_frame, on=["sampleId", "cellId"], how="left")
        .join(bound_counts, on=["sampleId", "cellId"], how="left")
        .select("sampleId", "cellId", "setId", "boundIdentities", *ordered_identities),
        True,
    )


def _leaf(level, entity, measurement, value, detail, panel_id, status: Status | None) -> QcRow:
    """One measurement's row: its own status, and the coverage of that one status.

    The triple comes from `roll_up`, so a leaf and a rollup are counted by one rule, and the
    row keeps the status `roll_up` would have flattened.
    """
    reading = Reading(status, value)
    return QcRow(level, entity, measurement, value, detail, panel_id, status, roll_up([reading]))


def _qc_frame(rows: list[QcRow]) -> pl.DataFrame:
    """The measurement set as a frame keyed (level, entity, measurement).

    Every declared measurement keeps its place whether or not this run could compute it, and
    a measurement nothing computed reads *not evaluated* with its reason rather than being
    absent. A reader must never mistake "nothing computed this yet" for "checked and found
    fine". A field with nothing in it is written null rather than as an empty string: polars
    quotes an empty string to keep it apart from a null, and a quoted empty cell is a value a
    downstream import would carry as one.
    """
    built = []
    for row in rows:
        declared = MEASUREMENT_BY_ID.get(row.measurement)
        built.append(
            {
                "level": row.level,
                "entity": row.entity,
                "panelId": row.panel_id,  # "" not None: this is an AXIS key, and a null is not a usable one
                "measurement": row.measurement,
                # The readable name, carried beside the id rather than instead of it. The id
                # is a p-column axis value and must stay stable. The label is what a reader
                # who never opened this module sees.
                "label": ROLLUP_LABEL if declared is None else declared.label,
                "value": row.value,
                "detail": row.detail or None,
                # Null where no line stands behind the measurement. The reason is read from the
                # value, which is where a reader looks next anyway.
                "status": None if row.status is None else row.status.value,
                "judged": row.coverage.judged,
                "unjudged": row.coverage.unjudged,
                "notEvaluated": row.coverage.not_evaluated,
                "counts": ROLLUP_COUNTS if declared is None else declared.counts,
                "implies": None if declared is None else declared.implies,
                "reason": None if declared is None else declared.deferred_reason,
            }
        )
    return pl.DataFrame(
        built,
        schema={
            "level": pl.String,
            "entity": pl.String,
            "panelId": pl.String,
            "measurement": pl.String,
            "label": pl.String,
            "value": pl.Float64,
            "detail": pl.String,
            "status": pl.String,
            "judged": pl.Int64,
            "unjudged": pl.Int64,
            "notEvaluated": pl.Int64,
            "counts": pl.String,
            "implies": pl.String,
            "reason": pl.String,
        },
    )


def _add(rows: list[QcRow], level: str, entity: str, measurement: str, value, detail: str = "", panel_id: str = ""):
    """Append one measurement row, taking its status from the lines in force.

    Every declared measurement goes through here. One with no line in force carries no
    status, which is honest rather than a refusal: it was computed, no line stands behind it,
    so its number is shown and nothing is claimed.
    """
    rows.append(
        _leaf(level, entity, measurement, value, detail, panel_id, status_for(measurement, value, DEFAULT_LINES))
    )


_DECILE_SCHEMA = {"distribution": pl.String, "decile": pl.Int64, "value": pl.Float64}
_BACKGROUND_SCHEMA = {
    "sampleId": pl.String,
    "tag": pl.String,
    "backgroundMean": pl.Float64,
    "signalMean": pl.Float64,
    "backgroundWeight": pl.Float64,
}


def _decile_rows(distribution: str, deciles: pl.DataFrame) -> list[dict]:
    """One distribution's decile points as rows. A point with no value contributes none."""
    return [
        {"distribution": distribution, "decile": int(d), "value": float(v)}
        for d, v in zip(deciles["decile"], deciles["value"], strict=True)
        if v is not None
    ]


def _sticky_measure(readings: dict[tuple[str, str], int], gate: int | None) -> tuple[float | None, str]:
    """One sample's sticky exposure, in whichever form the gate allows.

    A declared gate supplies a *high*, so the measurement is a count of the cells at or above
    it. With no gate there is no high, and a count against a line nobody drew would assert a
    boundary; the spread of the readings goes out instead, which is what a scientist reads in
    order to place one. The value is then the median, matching the other spreads here.
    """
    comparator_detail = f"cellsWithAComparator={len(readings)}"
    if gate is not None:
        return float(sum(1 for v in readings.values() if v >= gate)), f"{comparator_detail}|gate={gate}"
    deciles = deciles_of(np.asarray(list(readings.values()), dtype=float))
    points = "|".join(
        f"{d}:{'' if v is None else round(v, 3)}" for d, v in zip(deciles["decile"], deciles["value"], strict=True)
    )
    middle = deciles.filter(pl.col("decile") == 50)["value"].to_list()
    return (middle[0] if middle else None), f"{comparator_detail}|noGateDeclared|{points}"


def _score_spread(states: pl.DataFrame, served: ReferenceChoice) -> tuple[float | None, str]:
    """The run's scores as deciles, or why there are none.

    Only the declared rung scores. A population baseline yields a probability, which is not on
    the same scale and cannot be pooled with a score, so under it this does not exist.

    Cells carrying an `unreliableReason` are left out. A cell with no comparator or one a gate
    set aside still has a number here -- the score is computed before the state is called -- but
    it answers a comparison that never happened.
    """
    if served is not ReferenceChoice.DECLARED:
        return None, f"the {served.value} baseline yields no score, so a run resting on it has no spread"
    scored = states.filter(pl.col("unreliableReason").is_null())
    if scored.height == 0:
        return None, "no cell was scored"
    values = specificity_score(
        scored["umiCount"].to_numpy(),
        np.nan_to_num(scored["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0),
    )
    deciles = deciles_of(np.asarray(values, dtype=float))
    detail = "|".join(
        f"{d}:{'' if v is None else round(v, 3)}" for d, v in zip(deciles["decile"], deciles["value"], strict=True)
    )
    middle = deciles.filter(pl.col("decile") == 50)["value"].to_list()
    return (middle[0] if middle else None), detail


def _fitted_background(tag_fits: TagFits | None, samples: Collection[str], tag: str) -> tuple[float | None, str]:
    """One tag's fitted background across a panel's samples, as a value and its detail."""
    if tag_fits is None:
        return None, "no population baseline served this run, so nothing was fitted"
    fitted = [tag_fits.backgrounds[(s, tag)] for s in sorted(samples) if (s, tag) in tag_fits.backgrounds]
    missed = [s for s in sorted(samples) if (s, tag) in tag_fits.reasons]
    if not fitted:
        why = tag_fits.reasons.get((missed[0], tag), "") if missed else "this tag was not fitted in any sample"
        return None, f"fitted in no sample of this panel: {why}"
    means = [b.mean for b in fitted]
    detail = "|".join(
        [
            f"samplesFitted={len(fitted)}",
            f"samplesUnfitted={len(missed)}",
            f"backgroundRange={min(means):.4g}..{max(means):.4g}",
            f"medianSignalMean={_median([b.signal_mean for b in fitted]):.4g}",
            f"medianBackgroundWeight={_median([b.weight for b in fitted]):.4g}",
        ]
    )
    return _median(means), detail


def _median(values: list[float]) -> float | None:
    return float(pl.Series(values).median()) if values else None


# Long on purpose and not decomposed. This is one composition taken in the one order the
# reading has, and splitting it into stages would put that order in the call sites rather
# than in the code a reader follows top to bottom.
def main() -> None:
    p = argparse.ArgumentParser(description="Read antigen counts into a four-state binding verdict per set.")
    p.add_argument("counts_csv", help="sparse per-(sampleId, cellId, tag) UMI counts")
    p.add_argument("panel_csv", help="the panel file: which tags each sample was stained with")
    p.add_argument("--linker", default=None, help="cell -> clonotype set CSV (sampleId, cellId, setId)")
    p.add_argument("--cells", default=None, help="the cell list (sampleId, cellId); overrides the linker's cells")
    p.add_argument("--barcode-col", default="tag", help="panel column holding the barcode sequence")
    p.add_argument("--feature-col", default="feature", help="panel column holding the antigen name")
    p.add_argument("--sample-col", default="", help="panel column holding the sample; empty declares one panel for all")
    p.add_argument("--role-column", default="", help="panel column declaring each tag's role")
    p.add_argument("--reference-values", default="", help="comma-separated role values marking a comparator tag")
    p.add_argument(
        "--reference-source",
        required=True,
        # Derived from the enum, never restated. A hard-coded list lets the CLI reject a new
        # rung that every layer above it accepts.
        choices=[choice.value for choice in ReferenceChoice],
        help=(
            "which comparator to ask for; the run may serve 'none' instead, never a different one. "
            "Required: nothing here picks a rung for a scientist who did not"
        ),
    )
    p.add_argument("--panel-min-members", type=int, default=DEFAULT_PANEL_MIN_MEMBERS)
    p.add_argument(
        "--distribution-min-cells",
        type=int,
        default=DEFAULT_DISTRIBUTION_MIN_CELLS,
        help="cells a sample needs before a tag's own distribution may serve as its baseline",
    )
    p.add_argument("--floor", type=int, default=DEFAULT_FLOOR, help="zero every non-comparator reading below this")
    p.add_argument(
        "--cutoff", type=float, default=BOUND_CUTOFF, help="specificity score at or above which a cell binds"
    )
    p.add_argument("--min-voters", type=int, default=DEFAULT_MIN_VOTERS)
    p.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT)
    p.add_argument("--gate-threshold", type=int, default=None, help="set aside cells whose comparator reads this high")
    p.add_argument("--grouping", default=None, help="JSON: {'by':'tag'} or {'by':'property','column':...}")
    p.add_argument("--contending", default=None, help="JSON: groups of identities that contend, as a list of lists")
    # Accepted and not yet read. The capture rollup was its only reader, and only the sample
    # carries an aggregated status now. It stays declared because the capture axis ships on the
    # QC columns for the same reason: adding an axis to a released column changes that column's
    # identity, where adding a value does not.
    p.add_argument("--capture-map", default=None, help="JSON: sampleId -> captureId (accepted, not yet read)")
    p.add_argument(
        "--sample-labels",
        default=None,
        help="JSON: sampleId -> the label the panel file writes for it, when the two differ",
    )
    p.add_argument(
        "--qc-summary", default=None, help="per-sample read QC CSV (sampleId, readsTotal, readsMatched, ...)"
    )
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    if args.cutoff <= ANALYTIC_CUTOFF_BOUND:
        raise SystemExit(
            f"--cutoff must be strictly above {ANALYTIC_CUTOFF_BOUND:.4f}, the best score a zero count can reach. "
            f"At or below it a cell that was asked and read nothing settles one way when counted and the other "
            f"when written out, with no error raised. Got {args.cutoff}."
        )

    prefix = args.output_prefix
    roles = {"barcode": args.barcode_col, "feature": args.feature_col}
    if args.sample_col:
        roles["sample"] = args.sample_col
    panel, dropped_lines = read_panel(args.panel_csv, roles)

    # The panel file names samples the way a scientist does, "donor01", while the counts, the
    # linker and every axis this run emits are keyed by the platform's sampleId. Nothing else
    # bridges the two namespaces. Unbridged, `offered` ends up keyed by labels, no sample that
    # exists is offered anything, and every verdict comes back *never asked* -- the correct
    # answer to a question nobody asked, so it raises nothing. Translation happens HERE, once,
    # before the panel is used for anything. A value the map does not mention is left alone
    # rather than dropped, because a panel row naming a sample this run does not have is a real
    # mismatch and should reach the mismatch report.
    label_of_sample: dict[str, str] = _json_arg(args.sample_labels, "--sample-labels") or {}
    if label_of_sample and "sample" in panel.columns:
        by_label: dict[str, str] = {}
        for sample_id, label in sorted(label_of_sample.items()):
            if label in by_label:
                raise SystemExit(
                    f"--sample-labels gives label {label!r} to both {by_label[label]!r} and "
                    f"{sample_id!r}; a panel row naming it cannot be resolved to one sample"
                )
            by_label[label] = sample_id
        panel = panel.with_columns(pl.col("sample").replace(by_label))

    prop_cols = property_columns(panel)
    properties, inconsistent = consistent_properties(panel, prop_cols)
    # Kept, not merely reported: the values a tag disagreed about are what a fallback identity
    # is labelled with, and `properties` holds only what a tag agreed on.
    disagreed_by_column: dict[str, dict[str, list[str]]] = {}
    for tag, column, values in inconsistent:
        disagreed_by_column.setdefault(column, {})[tag] = sorted(values)
    for tag, column, values in inconsistent:
        print(
            f"[emit-verdicts] tag {tag!r} declares {column!r} as {values}; it carries no agreed value", file=sys.stderr
        )

    # The reference designation is read through `consistent_properties`, which strips the value
    # and drops any property a tag's rows disagree about. A per-sample comparator designation is
    # therefore discarded rather than honoured, which is what `apply_floor` documents.
    reference_values = {v.strip() for v in args.reference_values.split(",") if v.strip()}
    reference_tags: set[str] = set()
    # The column is checked whenever one is named, never only when values are named with it.
    # Gating the check on `reference_values` leaves the worse half silent: a role column the
    # panel does not declare designates no tag, and the baseline falls back to the panel's own
    # readings without a word -- a different number reported as the requested one.
    if args.role_column and args.role_column not in prop_cols:
        raise SystemExit(f"--role-column {args.role_column!r} is not a panel column; columns are {prop_cols}")
    if args.role_column and reference_values:
        reference_tags = {t for t, props in properties.items() if props.get(args.role_column) in reference_values}

    grouping_rule = _json_arg(args.grouping, "--grouping")
    grouping, grouping_id, ungrouped_tags, grouped_on = _build_grouping(
        grouping_rule, panel, properties, reference_tags
    )
    universe = identity_universe(panel, grouping)
    by_tag_grouping = default_grouping(panel, reference_tags)
    tag_universe = identity_universe(panel, by_tag_grouping)

    # Validated as a list of lists before it is read as one. A flat `["AgA","AgB"]` is valid
    # JSON, and `set("AgA")` is a set of CHARACTERS, so the run completes, no competitor note
    # fires, every `wasCompeted` reads false, and the run record states a contention that was
    # never tested -- a silent wrong answer, and the shape a hand-driven run reaches for.
    contending_raw = _json_arg(args.contending, "--contending") or []
    if not isinstance(contending_raw, list):
        raise SystemExit(f"--contending must be a JSON list of lists of identities; got {contending_raw!r}")
    for group in contending_raw:
        if not isinstance(group, list) or not all(isinstance(member, str) for member in group):
            raise SystemExit(
                f"--contending must be a JSON list of LISTS of identities; {group!r} is not a list of "
                'strings. A flat list such as ["AgA","AgB"] declares no group -- it reads each name as '
                "its own set of characters."
            )
        if len(group) < 2:
            raise SystemExit(
                f"--contending group {group!r} has fewer than two members; an identity cannot contend "
                "with itself, and a group of one tests nothing."
            )
    contending = [set(group) for group in contending_raw]

    counts = _read_counts(args.counts_csv)

    # The cell list is an input, never derived from the antigen readings: nothing in the counts
    # separates a cell from a droplet that held none. `--cells` wins over the linker where both
    # arrive, because a list from gene expression covers cells whose receptor never assembled.
    linker = (
        _read_columns(args.linker, ("sampleId", "cellId", "setId"), "linker file")
        if args.linker
        else pl.DataFrame(schema={"sampleId": pl.String, "cellId": pl.String, "setId": pl.String})
    )
    cells_by_set = _cells_by_set(linker)
    linker_cells = {key for keys in cells_by_set.values() for key in keys}
    if args.cells:
        listed = _read_columns(args.cells, ("sampleId", "cellId"), "cell list")
        cell_list = set(listed.iter_rows())
        cell_list_source = "cell list"
    elif args.linker:
        cell_list = linker_cells
        cell_list_source = "clonotype linker"
    else:
        # No list arrived, and one is NOT derived from the counts. Nothing in the antigen
        # readings separates a cell from a droplet that held none, so the observed barcodes are
        # not a cell list: in droplet data they outnumber the cells by one to two orders of
        # magnitude, because ambient material lands on most barcodes. Standing them in would be
        # worse than approximate, since `readsPerCell` divides by this and a healthy library
        # would read undersequenced and alert. Every barcode is still analysed and every count
        # still emitted. What is withheld is the claim that these barcodes are cells:
        # `inCellList` is unknown rather than true, and the measurements needing a cell list
        # read *not evaluated*.
        cell_list = None
        cell_list_source = "none"

    # `cell_list is None` means no list arrived, which differs from a list that arrived empty:
    # the first cannot answer "is this barcode a cell", the second answers "no". `listed`
    # collapses both for the set arithmetic below, where either way there are no barcodes to
    # add.
    listed = cell_list if cell_list is not None else set()

    observed_cells = set(counts.select("sampleId", "cellId").unique().iter_rows())
    # Barcodes outside the cell list stay in the frame, labelled. One dropped here is
    # indistinguishable afterwards from one that never existed, and its antigen counts are real
    # whatever the list says about it.
    analysed_cells = sorted(listed | observed_cells | linker_cells)

    panel_samples = {s for s in panel["sample"].to_list() if s != ANY_SAMPLE}
    samples = sorted(
        {s for s, _ in observed_cells} | {s for s, _ in listed} | {s for s, _ in linker_cells} | panel_samples
    )

    # The floor is applied per sample, so the counters it returns land in each sample's own QC
    # row. A cell key carries its sample, so partitioning is exact on both counters and the run
    # totals are their sums. There is no second implementation of the rule to drift from this
    # one.
    floor_stats: dict[str, dict[str, int]] = {}
    parts = []
    for sample in samples:
        floored_part = apply_floor(
            counts.filter(pl.col("sampleId") == sample),
            args.floor,
            reference_tags,
        )
        parts.append(floored_part.counts)
        floor_stats[sample] = floored_part.stats
    floored = pl.concat(parts) if parts else counts
    readings_floored = sum(s["readingsFloored"] for s in floor_stats.values())
    cells_emptied = sum(s["cellsEmptied"] for s in floor_stats.values())

    # One panel size, read once and passed to both. Deriving it separately for the default
    # choice and for the resolution would let the two disagree about whether the panel is large
    # enough to serve as its own comparator.
    panel_size = int(panel["tag"].n_unique())

    # No default and no derivation. The rung is the scientist's choice, and a run that carried
    # none is a configuration error rather than a run to guess at. argparse refuses it above,
    # so this never sees an empty value.
    source = ReferenceChoice[args.reference_source.upper()]
    tag_fits: TagFits | None = None
    # Set only by the one rung whose conditions the settings cannot answer. The other two
    # refuse in `served_source` before any of this runs.
    no_baseline_reason: str | None = None
    if source is ReferenceChoice.DISTRIBUTION:
        # Keyed by (sample, identity) and never by cell: this rung fits one distribution per
        # tag across a sample's cells, so its answer is the same number for every cell of a
        # sample and a different one for every identity. `reference_by_cell` has nothing to
        # return for it. Fitted over the RAW counts and the FULL cell universe -- the cells that
        # read nothing, and the cells a gate will later set aside. That second part is
        # `baseline-over-all-returned-cells`, which is also why the fit runs before
        # `gate_cells` below.
        tag_fits = fit_tag_probabilities_by_pair(counts, analysed_cells, panel, args.distribution_min_cells)
        probabilities = _identity_probabilities(tag_fits, grouping)
        # A run where no tag fitted anywhere established no baseline. This is the one refusal
        # that cannot be caught from the settings: whether a sample holds three hundred cells
        # whose counts admit a two-component fit is a property of the data. So the run FINISHES,
        # says so, and draws no punchcard -- rather than answering every position *unreliable*,
        # which is honest and useless, or crashing after doing the work.
        reference = Reference({}, ReferenceChoice.DISTRIBUTION)
        by_identity = None
        if not probabilities:
            no_baseline_reason = (
                "no baseline could be established: the tag-distribution rung was selected and no tag's "
                f"counts admitted a two-component fit in any sample, against the "
                f"{args.distribution_min_cells} cells this rung requires. Whether a sample can support "
                "this rung is a property of the data rather than of the settings, so it could not be "
                "caught before the run. The run's quality measurements are below; no verdicts were read."
            )
            probabilities = None
        tag_probabilities = _identity_probabilities(tag_fits, by_tag_grouping) or None
    else:
        by_identity = None
        probabilities = None
        tag_probabilities = None
        reference = _cell_keyed_reference(counts, reference_tags, source, analysed_cells, panel_size, args)

    gated, cells_high_reference = gate_cells(reference.by_cell, args.gate_threshold)
    if reference.served is ReferenceChoice.DISTRIBUTION:
        # No per-cell comparator exists to read a gate against, so the gate sets nothing aside
        # and the exposure count is not a measurement this run made. None, never 0: a zero would
        # report a run with no high background rather than one where the question does not
        # arise.
        cells_high_reference = None

    # Built once and handed to every consumer. Two bundles built from two reference dicts do
    # not raise. They disagree about which cells cannot be compared, and the silent-position
    # count comes out wrong or negative.
    admissibility = Admissibility(reference.by_cell, gated, by_identity, probabilities)

    non_reference = floored.filter(~pl.col("tag").is_in(list(reference_tags))) if reference_tags else floored
    identities = combine_tags_to_identities(non_reference, grouping)
    states = read_states(identities, admissibility, args.cutoff)

    # The per-tag reading is diagnostic only: it compares each tag against the reference
    # separately, and no verdict is built from it. The measurement set carries it at both levels
    # always, so where the chosen grouping is not the per-tag one it is read a second time.
    if grouping == by_tag_grouping:
        tag_states = states
    else:
        # A second bundle, because the per-tag read asks about different identities. Where the
        # comparator is keyed by identity, the bundle built for the chosen grouping answers
        # about identities this read never mentions.
        tag_admissibility = (
            Admissibility(reference.by_cell, gated, None, tag_probabilities)
            if (by_identity is not None or tag_probabilities is not None)
            else admissibility
        )
        tag_states = read_states(
            combine_tags_to_identities(non_reference, by_tag_grouping), tag_admissibility, args.cutoff
        )

    # Which (sample, tag) pairs the reads actually carry, from the RAW counts. Never from
    # `floored`: a count the minimum zeroed is a reading that happened and failed, and settles
    # *not bound*, while a tag with no reads at all is a question nobody put. Reading the
    # floored frame here would turn a dead reagent into a confident clean negative on every
    # clonotype in the run.
    seen_pairs = {
        (row["sampleId"], row["tag"]) for row in counts.select("sampleId", "tag").unique().iter_rows(named=True)
    }
    offered_by_sample = {s: offered_identities(panel, grouping, [s], seen_pairs) for s in samples}
    tag_offered_by_sample = {s: offered_identities(panel, by_tag_grouping, [s], seen_pairs) for s in samples}

    def _answers(frame: pl.DataFrame) -> pl.DataFrame:
        """The frame, or its headers alone where the run established no baseline.

        A run with no baseline read no verdicts, so the frames carrying answers carry no rows.
        They keep their schemas, because every reader still needs to find its columns, and a
        missing file reads as a stage that crashed rather than one that finished and said why.

        Emitting the answers instead would fill every position with *unreliable*: honest and
        useless, costing what a real run costs and looking like a result at a glance.

        The STRUCTURAL frames are written in full either way -- which tags feed which identity,
        what each sample was offered, the panel and identity labels. Those describe the run
        rather than answering it, and a reader working out why no baseline could be established
        needs them.
        """
        return frame.clear() if no_baseline_reason else frame

    verdicts = attach_competitor_notes(
        combine_cells(
            states,
            universe,
            offered_by_sample,
            cells_by_set,
            admissibility,
            args.min_voters,
            args.min_agreement,
        ),
        contending,
    )
    _write_sorted(_answers(verdicts), f"{prefix}_verdicts.csv", ["setId", "identity"])
    # The set's own cell count, joined on rather than computed inside `set_counts`, which is a
    # pure reading of the verdicts frame at its (setId, identity) grain where a cell count does
    # not live. It is the set's cells, NOT its answering cells: that number varies by identity
    # and travels with the verdict as support. This one is a property of the clonotype, which is
    # why `the-explore-readout` puts it beside the name instead of in every position.
    per_set_cells = pl.DataFrame(
        [(set_id, len(cells)) for set_id, cells in sorted(cells_by_set.items())],
        orient="row",
        schema={"setId": pl.String, "cellCount": pl.Int64},
    )
    # Set-aside cells PER CLONOTYPE, never per run. `the-explore-readout` states them once for
    # the clonotype, and the run-level total in the run meta answers a different question that
    # cannot be split back apart. `gated` holds (sampleId, cellId) keys and `cells_by_set` maps a
    # set to its members, so this is a membership count over cells already read.
    per_set_gated = pl.DataFrame(
        list(count_by_set(cells_by_set, gated).items()),
        orient="row",
        schema={"setId": pl.String, "cellsSetAside": pl.Int64},
    )
    # Cells that read nothing at all, PER CLONOTYPE. Carried beside the clonotype's cell count
    # rather than at every identity, because a cell with nothing left is empty at every identity
    # and repeating the subtraction per position would report a per-identity failure that did not
    # happen. It separates a negative resting on cells that read something from one resting on
    # cells that read nothing, and changes no verdict.
    per_set_empty = pl.DataFrame(
        list(count_by_set(cells_by_set, cells_reading_nothing(floored, linker_cells)).items()),
        orient="row",
        schema={"setId": pl.String, "cellsReadingNothing": pl.Int64},
    )
    counts_frame = (
        set_counts(verdicts)
        .join(per_set_cells, on="setId", how="left")
        # Filled rather than asserted, unlike cellCount below: with no gate declared `gated` is
        # empty, so every set legitimately has nothing set aside and 0 is the true answer.
        .join(per_set_gated, on="setId", how="left")
        # Filled for the same reason, and it bites harder here: this column ships off by
        # default, so the reader who turns it on is the one asking the question, and a null
        # would answer it with a blank where zero is the truth.
        .join(per_set_empty, on="setId", how="left")
        .with_columns(pl.col("cellsSetAside").fill_null(0), pl.col("cellsReadingNothing").fill_null(0))
    )
    # Every set comes FROM the linker, so every set has cells. Asserted rather than filled with
    # zero: a set with no cells is a contradiction, and writing 0 would report it as a real,
    # empty clonotype.
    missing = counts_frame.filter(pl.col("cellCount").is_null())["setId"].to_list()
    if missing:
        raise SystemExit(f"sets carry verdicts but no cells, which cannot happen: {missing[:8]}")
    _write_sorted(_answers(counts_frame), f"{prefix}_set_counts.csv", ["setId"])

    summary, punch, summary_emitted = _pivot_identity_summary(verdicts, universe)
    _write_sorted(_answers(summary), f"{prefix}_identity_summary.csv", ["setId"])
    _write_sorted(_answers(punch), f"{prefix}_identity_punch.csv", ["setId"])

    cell_punch, cell_punch_emitted = _pivot_cell_punch(states, cells_by_set, offered_by_sample, admissibility, universe)
    _write_sorted(_answers(cell_punch), f"{prefix}_cell_punch.csv", ["setId", "sampleId", "cellId"])

    # The sparse per-tag counts and the per-cell scalars together carry every per-cell state, at
    # a small fraction of the dense grid's size. They stay inside the block: reading the same
    # experiment under another grouping is another execution rather than a re-derivation a reader
    # performs, and the grouping enters after the counting.
    #
    # That argument used to end "so the dense grid is never exported", and it no longer holds:
    # `_pivot_cell_punch` above exports it, because a readout showing one clonotype's cells
    # against the panel cannot be assembled from a sparse frame by a grid. What survives is the
    # SIZE, which is why that function carries a cell gate and reports whether it emitted.
    #
    # With no list, membership is unknown rather than false: a barcode nobody classified is not a
    # barcode classified as "not a cell".
    unlisted_reads = "false" if cell_list is not None else "unknown"
    in_list = pl.DataFrame(
        [(s, c, "true") for s, c in sorted(listed)],
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "inCellList": pl.String},
    )

    def _admissibility(key: CellKey) -> str:
        reason = cell_admissibility_reason(key, admissibility)
        return "admissible" if reason is None else reason.value

    # Admissibility is built HERE, in the same row as its own cell, and not attached to a later
    # frame as a positional column. Polars does not promise a left frame's row order survives a
    # join (`maintain_order` defaults to "none"), so a positional attach after the joins below can
    # give cells each other's labels -- and `_write_sorted` then sorts the file, which hides it
    # rather than repairing it.
    reference_frame = pl.DataFrame(
        [(s, c, reference.by_cell.get((s, c)), _admissibility((s, c))) for s, c in analysed_cells],
        orient="row",
        schema={
            "sampleId": pl.String,
            "cellId": pl.String,
            "referenceCount": pl.Int64,
            "admissibility": pl.String,
        },
    )
    cell_counts = (
        non_reference.join(reference_frame, on=["sampleId", "cellId"], how="left")
        .join(in_list, on=["sampleId", "cellId"], how="left")
        .with_columns(pl.col("inCellList").fill_null(unlisted_reads))
        .select(["sampleId", "cellId", "tag", "umiCount", "referenceCount", "inCellList"])
    )
    _write_sorted(cell_counts, f"{prefix}_cell_counts.csv", ["sampleId", "cellId", "tag"])

    cell_scalars = (
        reference_frame.join(in_list, on=["sampleId", "cellId"], how="left")
        .with_columns(pl.col("inCellList").fill_null(unlisted_reads))
        .select(["sampleId", "cellId", "referenceCount", "admissibility", "inCellList"])
    )
    _write_sorted(_answers(cell_scalars), f"{prefix}_cell_scalars.csv", ["sampleId", "cellId"])

    # Both frames are pure key sets -- what a sample was offered, and which identity a tag feeds
    # -- and each carries a constant value column so it can become a p-column at all. A frame of
    # key columns alone imports as nothing: columns are built from value columns, so a key-only
    # file yields no column and the fact it records never leaves the block.
    offered_frame = pl.DataFrame(
        [(sample, identity, "true") for sample in samples for identity in sorted(offered_by_sample[sample])],
        orient="row",
        schema={"sampleId": pl.String, "identity": pl.String, "offered": pl.String},
    )
    _write_sorted(offered_frame, f"{prefix}_offered.csv", ["sampleId", "identity"])

    # The value column is named "1" and holds 1, matching the cell-linker convention already used
    # for linker columns elsewhere in the platform.
    #
    # Deliberately NOT keyed by sample. The reason is the join, not the declaration. This linker
    # puts a tag-keyed figure beside an identity-keyed verdict, and neither side carries a sample:
    # verdicts are (set, identity) over clonotypes that span samples, and the per-tag figures are
    # run-level. A sample axis here is an axis no participating table has. It does not sharpen the
    # join -- it makes the join malformed, and `createPlDataTableV3` label discovery then rejects
    # the spec frame.
    #
    # Under (tag, sample) grouping one tag can feed several identities, so this frame is
    # many-to-many with one row per pair. Distinct rows matter: two tags of one identity would
    # otherwise emit the same key twice, and duplicate axis keys break a grid silently.
    linker_frame = _linker_frame(grouping)
    _write_sorted(linker_frame, f"{prefix}_tag_identity.csv", ["tag", "identity"])

    # Only disagreements in the column that SUPPLIES the label matter: a tag that disagrees about
    # some other property still carries an ordinary name. Which column that is depends on the rule
    # -- a property grouping labels by the value it grouped on, while the per-tag grouping borrows
    # the feature name. Passing the grouping column either way made every per-tag run look up "",
    # so a barcode two samples named differently fell through to its raw 15-mer with the conflict
    # shown nowhere a reader would look.
    #
    # Under a property grouping on ONE column, that column supplies the rescue. Under several there
    # is no single such column, so the feature column supplies it: a pair that fell back has no
    # combination at all, and what a reader needs then is what the reagent is called.
    grouping_columns = (
        _grouping_columns(grouping_rule, property_columns(panel))
        if (isinstance(grouping_rule, dict) and grouping_rule.get("by") == "property")
        else []
    )
    label_column = grouping_columns[0] if len(grouping_columns) == 1 else args.feature_col
    # Bound once and passed to both readers below. `_identity_labels` joins these names into the
    # label a reader sees. The run record carries the same names apart so the readout can say WHY a
    # label is joined. Deriving the second from the first -- splitting the label back on " / " --
    # would guess wrong for a reagent whose own name contains a slash.
    label_disagreements = disagreed_by_column.get(label_column or "", {})
    labels = _identity_labels(
        grouping,
        properties,
        args.feature_col,
        grouping_id,
        label_disagreements,
    )
    identity_labels = pl.DataFrame(
        [(identity, labels.get(identity, identity)) for identity in sorted(universe)],
        orient="row",
        schema={"identity": pl.String, "label": pl.String},
    )
    _write_sorted(identity_labels, f"{prefix}_identity_labels.csv", ["identity"])

    # The declarations, keyed the same way the verdicts are. Wide -- one column per property --
    # because the workflow turns each into its own p-column with the property name in the DOMAIN,
    # which is what makes two properties two distinct columns rather than one a reader unstacks.
    #
    # A property no identity agreed on is left out rather than exported empty: an all-blank
    # filterable column offers a reader a filter with nothing to filter by. The surviving names are
    # recorded in the run meta, because the workflow builds one spec per column and the headers are
    # panel data, unknown until this runs.
    exportable = [c for c in prop_cols if c != IDENTITY_KEY_COLUMN]
    if len(exportable) < len(prop_cols):
        print(
            f"[emit-verdicts] panel column {IDENTITY_KEY_COLUMN!r} is the key of the identity-property "
            "table and cannot also be one of its properties; it is left out of that export and reaches "
            "no consumer. Rename it in the panel file to have it travel with the verdicts.",
            file=sys.stderr,
        )
    identity_properties = _identity_properties(grouping, properties, exportable, grouped_on, disagreed_by_column)
    property_values = {
        column: sorted({held[column] for held in identity_properties.values() if column in held})
        for column in exportable
    }
    emitted_properties = [c for c in exportable if property_values[c]]
    identity_property_frame = pl.DataFrame(
        [
            tuple([identity] + [identity_properties.get(identity, {}).get(c, "") for c in emitted_properties])
            for identity in sorted(universe)
        ],
        orient="row",
        schema={IDENTITY_KEY_COLUMN: pl.String} | {c: pl.String for c in emitted_properties},
    )
    _write_sorted(identity_property_frame, f"{prefix}_identity_properties.csv", ["identity"])

    declared = _declared_by_sample(panel, samples)
    panel_of_sample = {sample: _panel_id(tags) for sample, tags in declared.items()}
    tags_of_panel: dict[str, frozenset[str]] = {panel_of_sample[s]: declared[s] for s in samples}
    samples_of_panel: dict[str, list[str]] = {}
    for sample in samples:
        samples_of_panel.setdefault(panel_of_sample[sample], []).append(sample)

    # Named for a reader, so the sample is shown under the label the panel file used rather than
    # the sampleId it was translated to. The KEY is the sampleId, because a key has to join.
    panel_labels = pl.DataFrame(
        [
            (
                panel_id,
                f"{len(tags_of_panel[panel_id])} tags: "
                + ", ".join(label_of_sample.get(s, s) for s in samples_of_panel[panel_id]),
            )
            for panel_id in sorted(tags_of_panel)
        ],
        orient="row",
        schema={"panelId": pl.String, "label": pl.String},
    )
    _write_sorted(panel_labels, f"{prefix}_panel_labels.csv", ["panelId"])

    sample_panel = pl.DataFrame(
        [(sample, panel_of_sample[sample]) for sample in samples],
        orient="row",
        schema={"sampleId": pl.String, "panelId": pl.String},
    )
    _write_sorted(sample_panel, f"{prefix}_sample_panel.csv", ["sampleId"])

    # Both directions of the panel-versus-reads check, re-keyed onto the panel: a per-tag failure
    # is a property of the declared tag set rather than of any one sample carrying it. The samples
    # reporting it travel in the row.
    #
    # `seen` is drawn from the counts, whose feature barcodes were already snapped onto the panel by
    # refine-tags. So `seen` is a subset of the declared set here, and only the declared-never-seen
    # direction can produce a row. Reporting an undeclared barcode needs a pre-correction source.
    seen = counts.select("sampleId", "tag").unique()
    unknown_panel = _panel_id(frozenset())
    mismatch_rows: dict[tuple[str, str, str], set[str]] = {}
    for row in panel_read_mismatch(panel, seen).iter_rows(named=True):
        # In the unkeyed case every row comes back under "*", which is not a sample id: the
        # declaration really is global, so it reports against every sample in the run.
        affected = samples if row["sample"] == ANY_SAMPLE else [row["sample"]]
        for sample in affected:
            key = (panel_of_sample.get(sample, unknown_panel), row["tag"], row["direction"])
            mismatch_rows.setdefault(key, set()).add(sample)
    mismatch = pl.DataFrame(
        [(panel_id, tag, direction, ", ".join(sorted(s))) for (panel_id, tag, direction), s in mismatch_rows.items()],
        orient="row",
        schema={"panelId": pl.String, "tag": pl.String, "direction": pl.String, "samples": pl.String},
    )
    _write_sorted(mismatch, f"{prefix}_panel_mismatch.csv", ["panelId", "direction", "tag"])

    # ---- the quality measurements -------------------------------------------------

    def _disagreement_rates(samples_here: list[str]) -> dict[str, float | None]:
        """The per-tag self-disagreement rate over one panel's samples only.

        Scoped per panel rather than over the run, because the row carrying this figure is keyed
        `(tag, panelId)`. A run-global rate on a panel's row says something the panel did not do: a
        reagent declared in panels P and Q but misbehaving only in Q's samples shows the same
        inflated rate on P's row, pointing a reader at the wrong panel and the wrong remedy.

        Measured at the tag and nowhere else. The identity-level figure has nothing to compare
        against, so it cannot separate a faulty reagent from a panel full of weak binders.

        The cell sets are restricted too, not only the states. A set spanning two panels' samples
        would otherwise bring its other panel's cells into this panel's evaluable count, and
        self-disagreement is precisely a count of a set's cells contradicting each other.
        """
        here = set(samples_here)
        sets_here = {set_id: [key for key in members if key[0] in here] for set_id, members in cells_by_set.items()}
        sets_here = {set_id: members for set_id, members in sets_here.items() if members}
        by_tag = self_disagreement(
            tag_states.filter(pl.col("sampleId").is_in(samples_here)).select(
                "sampleId", "cellId", pl.col("identity").alias("key"), "state"
            ),
            tag_universe,
            {s: tag_offered_by_sample[s] for s in samples_here},
            sets_here,
            admissibility,
        )
        return dict(zip(by_tag["key"].to_list(), by_tag["disagreementRate"].to_list(), strict=True))

    read_qc: dict[str, dict] = {}
    if args.qc_summary:
        for row in pl.read_csv(args.qc_summary, infer_schema_length=0).iter_rows(named=True):
            read_qc[str(row.get("sampleId", "")).strip()] = row

    def _number(row: dict, column: str) -> float | None:
        raw = row.get(column)
        if raw is None or str(raw).strip() == "":
            return None
        return float(raw)

    rows: list[QcRow] = []
    sample_coverage: dict[str, Coverage] = {}
    for sample in samples:
        first = len(rows)
        sample_counts = counts.filter(pl.col("sampleId") == sample)
        listed_here = [key for key in sorted(listed) if key[0] == sample] if cell_list is not None else None
        qc = read_qc.get(sample, {})

        reads_matched = _number(qc, "readsMatched")
        matched_detail = "" if reads_matched is None else f"readsMatched={int(reads_matched)}"
        _add(rows, "sample", sample, "readsTotal", _number(qc, "readsTotal"), matched_detail)
        _add(rows, "sample", sample, "panelAssignedFraction", _number(qc, "panelAssignedFraction"))
        _add(rows, "sample", sample, "cellBarcodeValidFraction", _number(qc, "cellBarcodeValidFraction"))
        # The denominator is the cell list, never the barcodes the reads happened to touch: the
        # five-thousand recommendation is per called cell, and in droplet data observed barcodes
        # run one to two orders of magnitude higher, so dividing by them would alert on a healthy
        # run. No cell list means no denominator, so depth is *not evaluated*.
        depth = (
            reads_per_cell(int(reads_matched), len(listed_here))
            if reads_matched is not None and listed_here is not None
            else None
        )
        detail = f"cellsInList={len(listed_here)}" if listed_here is not None else "no cell list supplied"
        _add(rows, "sample", sample, "readsPerCell", depth, detail)

        deciles = antigen_count_deciles(sample_counts)
        decile_detail = "|".join(
            f"{d}:{'' if v is None else round(v, 3)}" for d, v in zip(deciles["decile"], deciles["value"], strict=True)
        )
        middle = deciles.filter(pl.col("decile") == 50)["value"].to_list()
        _add(rows, "sample", sample, "antigenCountDistribution", middle[0] if middle else None, decile_detail)
        _add(rows, "sample", sample, "aggregateBarcodeFraction", None)

        stats = floor_stats.get(sample, {"readingsFloored": 0, "cellsEmptied": 0})
        _add(
            rows,
            "sample",
            sample,
            "floorRemoved",
            float(stats["readingsFloored"]),
            f"cellsEmptied={stats['cellsEmptied']}",
        )

        listed_totals = (
            sample_counts.join(in_list, on=["sampleId", "cellId"], how="semi")
            .group_by("cellId")
            .agg(pl.col("umiCount").sum().alias("total"))["total"]
            .to_list()
        )
        _add(
            rows,
            "sample",
            sample,
            "uniqueCountsPerCell",
            _median([float(v) for v in listed_totals]),
            f"cellsWithAReading={len(listed_totals)}",
        )

        # Two forms, and the gate decides which. With a gate declared this counts the cells it
        # set aside. With none there is no *high* to count, so the measurement is the spread of
        # the readings themselves -- which `290-reference-two-roles` names as what a scientist
        # reads in order to declare a gate.
        here = {key: value for key, value in reference.by_cell.items() if key[0] == sample}
        high_value, high_detail = _sticky_measure(here, args.gate_threshold)
        _add(rows, "sample", sample, "highReferenceCells", high_value, high_detail)

        # A measurement declaring `rolls_up=False` states a reagent's condition on a sample's
        # row, and `310` keeps a reagent's failure off every sample: one bad reagent marking
        # twenty samples is how a sample status becomes noise. Its own row keeps its status.
        sample_coverage[sample] = roll_up(
            [Reading(r.status, r.value) for r in rows[first:] if _rolls_up(r.measurement)]
        )

    per_sample_tag_total = {
        (row["sampleId"], row["tag"]): row["total"]
        for row in counts.group_by(["sampleId", "tag"])
        .agg(pl.col("umiCount").sum().alias("total"))
        .iter_rows(named=True)
    }

    for panel_id in sorted(tags_of_panel):
        panel_samples_here = samples_of_panel[panel_id]
        panel_tags = tags_of_panel[panel_id]
        tag_rate = _disagreement_rates(panel_samples_here)
        here_total = {
            tag: float(sum(per_sample_tag_total.get((s, tag), 0) for s in panel_samples_here))
            for tag in {t for (s, t) in per_sample_tag_total if s in panel_samples_here} | set(panel_tags)
        }
        observed_here = {tag for tag, total in here_total.items() if total > 0}

        # A declared tag is alerting at zero reads, so every declared tag gets a row rather than
        # only the ones that produced nothing: reporting only the failures leaves a reader unable
        # to tell a clean panel from an unchecked one.
        for tag in sorted(panel_tags):
            _add(rows, "tag", tag, "declaredNeverSeen", here_total[tag], "", panel_id)
        for tag in sorted(observed_here - panel_tags):
            _add(rows, "tag", tag, "undeclaredBarcodes", here_total[tag], "", panel_id)

        # The fitted background, one row per declared tag. Fits are per (sample, tag) and this
        # table is keyed (tag, panel), so the value is the MEDIAN background mean over the
        # panel's samples that fitted, and the detail carries how many did and the spread. A
        # mean of means would let one sample's outlier move a tag's whole row.
        #
        # Under a declared baseline nothing is fitted, so every row carries no value and says
        # why -- the same device `readsPerCell` uses when no cell list arrived. The row is there
        # either way, or a reader cannot tell "not fitted" from "never measured".
        for tag in sorted(panel_tags):
            _add(
                rows,
                "tag",
                tag,
                "fittedBackground",
                *_fitted_background(tag_fits, panel_samples_here, tag),
                panel_id,
            )

        panel_states = tag_states.filter(pl.col("sampleId").is_in(panel_samples_here)).rename({"identity": "tag"})
        # RAW counts, not `floored`. Cells-with-count and the median are what the reagent
        # delivered, and the minimum is what survived it. Passing the floored frame here is the
        # defect 330-the-quality-readout names: a reagent putting two counts into every cell
        # would read the same as one that delivered nothing.
        panel_counts = counts.filter(pl.col("sampleId").is_in(panel_samples_here))
        for row in per_antigen_measures(
            panel_counts, panel_states, panel_tags, panel_samples_here, reference_tags
        ).iter_rows(named=True):
            above = row["cellsAboveTheLine"]
            # None only for a reference tag, which is held out of the verdict read. Say so rather
            # than printing a zero: no cell was called bound because none was asked.
            detail = (
                f"cellsWithCount={row['cellsWithCount']}"
                f"|medianCountPerCell={row['medianCountPerCell']}"
                f"|samplesSeenIn={row['samplesSeenIn']}/{row['samplesInPanel']}"
            )
            if above is None:
                detail += "|cellsAboveTheLine=none asked, this tag supplies the baseline"
            _add(
                rows,
                "tag",
                row["tag"],
                "perAntigen",
                float(above) if above is not None else None,
                detail,
                panel_id,
            )

        # No line stands behind this, so it reads unjudged and its value travels beside its
        # siblings for a reader to compare. A tag standing clear of the other tags in its panel is
        # misbehaving whatever the absolute rate -- a real finding, but one a reader makes by
        # looking. Applying a threshold would need a multiplier nobody published. Keeping the rows
        # per panel is what makes the comparison the right one.
        for tag in sorted(panel_tags & set(tag_rate)):
            _add(rows, "tag", tag, "tagDisagreement", tag_rate[tag], "", panel_id)

        # The identity -> tags map comes from `grouping`, the one place that settles which tags
        # an identity carries. Restricted to this panel's samples and declarations, matching how
        # the row is keyed: a tag misbehaving against its siblings in one panel must not report
        # that on another panel's row.
        siblings_of_identity: dict[str, list[str]] = {}
        identity_of_tag: dict[str, str] = {}
        for (tag, sample), identity in grouping.items():
            if tag not in panel_tags or (sample not in set(panel_samples_here) and sample != ANY_SAMPLE):
                continue
            members = siblings_of_identity.setdefault(identity, [])
            if tag not in members:
                members.append(tag)
            identity_of_tag[tag] = identity
        sibling_rate = sibling_disagreement(panel_states, siblings_of_identity)

        # No line stands behind this either, so it reads unjudged beside its siblings. A blank
        # and a zero are opposite findings here, so a row with no rate says which case it is.
        for tag in sorted(panel_tags & set(sibling_rate)):
            rate = sibling_rate[tag]
            detail = ""
            if rate is None:
                detail = (
                    "this identity carries one tag, so it has no sibling"
                    if len(siblings_of_identity[identity_of_tag[tag]]) < 2
                    else "no cell holds this tag beside a sibling"
                )
            _add(rows, "tag", tag, "siblingDisagreement", rate, detail, panel_id)

    # One row for the whole run, and the entity is the run: 320 puts the score spread at that
    # grain because the cutoff is one number for the run, so a per-sample figure would answer a
    # question nobody asked. Emitted outside the sample loop, which is also what keeps it out of
    # every sample's rollup.
    #
    # The score is re-derived from the counts `read_states` returns rather than carried out of
    # it. Same function and same inputs, so the two cannot drift, and `read_states` keeps its
    # refusal to emit a binding level per cell.
    score_value, score_detail = _score_spread(states, reference.served)
    _add(rows, "run", "run", "scoreDistribution", score_value, score_detail)

    # Only the sample carries an aggregated status, over its OWN per-sample measurements. A
    # per-tag failure is usually a property of the reagent across the whole run, so feeding a dead
    # reagent in a panel of twenty tags into a sample status would mark every sample alerting and
    # make that status noise. It does not hide: the per-tag row states the reagent finding on its
    # own, keyed by the panel that has it.
    for sample in samples:
        coverage = sample_coverage[sample]
        rows.append(QcRow("sample", sample, ROLLUP, None, "", "", coverage.status, coverage))

    _write_sorted(_qc_frame(rows), f"{prefix}_qc.csv", ["level", "entity", "panelId", "measurement"])

    # The three distributions `330-the-quality-readout` puts last, as plottable frames rather than
    # as detail strings on a measurement row. A reader settles the cutoff and the gate by looking at
    # these, so they have to be drawable: a decile encoded inside a detail string is a number nobody
    # can plot.
    #
    # Deciles of the score and of the reference reading share one frame, keyed by which distribution
    # a row belongs to. Both are taken over the whole run: the cutoff is one number for the run, and
    # so is the gate, so a plot must show every cell the number will act on. `330` says exactly that
    # of the reference reading, and pooling over samples is deliberate rather than a simplification.
    decile_rows: list[dict] = []
    if reference.served is ReferenceChoice.DECLARED:
        scored = states.filter(pl.col("unreliableReason").is_null())
        if scored.height > 0:
            values = specificity_score(
                scored["umiCount"].to_numpy(),
                np.nan_to_num(scored["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0),
            )
            decile_rows += _decile_rows("score", deciles_of(np.asarray(values, dtype=float)))
    if reference.by_cell:
        readings = np.asarray(list(reference.by_cell.values()), dtype=float)
        decile_rows += _decile_rows("referenceReading", deciles_of(readings))
    _write_sorted(
        pl.DataFrame(decile_rows, schema=_DECILE_SCHEMA),
        f"{prefix}_qc_deciles.csv",
        ["distribution", "decile"],
    )

    # One row per (sample, tag) the fit scored, at the fit's own grain. Aggregating to the tag would
    # hide a reagent that separated in one sample and not in another, which is the comparison a
    # reader makes here.
    background_rows = [
        {
            "sampleId": sample,
            "tag": tag,
            "backgroundMean": b.mean,
            "signalMean": b.signal_mean,
            "backgroundWeight": b.weight,
        }
        for (sample, tag), b in sorted((tag_fits.backgrounds if tag_fits is not None else {}).items())
    ]
    _write_sorted(
        pl.DataFrame(background_rows, schema=_BACKGROUND_SCHEMA),
        f"{prefix}_qc_backgrounds.csv",
        ["sampleId", "tag"],
    )

    meta = {
        "referenceChoice": reference.served.value,
        "referenceSourceRequested": source.value,
        # Whether a baseline was established, and where not, why. Only the tag-distribution rung
        # can reach false: its conditions are properties of the data, so a run resting on it
        # proceeds and reports afterwards. The other rungs refuse from the settings.
        #
        # The model reads this and draws no punchcard where it is false, showing the reason in its
        # place. The answer frames are header-only in that case, so a consumer that reads them
        # anyway finds no rows rather than a full grid of non-answers.
        "baselineEstablished": no_baseline_reason is None,
        "noBaselineReason": no_baseline_reason,
        "cellListSource": cell_list_source,
        "cellsInList": len(cell_list) if cell_list is not None else None,
        "cellsAnalysed": len(analysed_cells),
        "floor": args.floor,
        "cutoff": args.cutoff,
        "minVoters": args.min_voters,
        "minAgreement": args.min_agreement,
        "gateThreshold": args.gate_threshold,
        "panelMinMembers": args.panel_min_members,
        "distributionMinCells": args.distribution_min_cells,
        # Per (sample, tag), and only where that rung was asked for: which tags could not be
        # fitted, and why. A tag missing here fitted. The reader needs both halves to tell a panel
        # that mostly worked from one that mostly did not.
        "distributionUnfitted": (
            {f"{sample}/{tag}": reason for (sample, tag), reason in sorted(tag_fits.reasons.items())}
            if tag_fits is not None
            else {}
        ),
        "roleColumn": args.role_column,
        "referenceValues": sorted(reference_values),
        "referenceTags": sorted(reference_tags),
        "grouping": grouping_rule or {"by": "tag"},
        "groupingId": grouping_id,
        # The narrowing a short panel file costs, carried in the output rather than only in a log
        # line: these tags were answered under a grouping that could not place them.
        "tagsWithoutGroupingValue": sorted(ungrouped_tags),
        "contending": [sorted(group) for group in contending],
        "identityCount": len(universe),
        # The identities themselves, in the order the pivot lays them out. The workflow builds one
        # p-column per column of result_identity_summary.csv, and the column names are the
        # identities -- panel data, unknown until this runs. A count cannot name them, so without
        # this the pivoted summary imports as nothing and the only per-antigen state a
        # clonotype-anchored reader can see disappears with no error.
        "identities": sorted(universe),
        # Read by the workflow to label the punchcard's columns. An identity whose grouping value
        # was dropped is labelled with the names it did declare, so the card shows a reagent rather
        # than a 15-mer. Every other identity labels itself.
        "identityLabels": {identity: labels.get(identity, identity) for identity in sorted(universe)},
        # Why a label above is two names joined. Keyed exactly as `_identity_labels` keys its own
        # lookup, so an entry appears for precisely the identities whose label was joined. Only
        # genuine conflicts: one declared name is the ordinary case. The workflow turns each entry
        # into the column's description annotation, shown as a header tooltip -- otherwise a reader
        # meets two antigen names in one header with nothing saying whether the barcode was shared,
        # the panel was inconsistent, or the block merged something.
        "identityNameConflicts": {
            identity: sorted(names)
            for identity in sorted(universe)
            if len(names := label_disagreements.get(identity, [])) > 1
        },
        # The declaration columns that reached result_identity_properties.csv, and the distinct
        # values each carries. Both are panel data: the workflow builds one p-column per name and
        # annotates it with its own value set, so without these the declarations import as nothing.
        "identityProperties": emitted_properties,
        "identityPropertyValues": {c: property_values[c] for c in emitted_properties},
        "identitySummaryEmitted": summary_emitted,
        # False where the run was too wide or too deep for the dense per-cell grid, so the readout
        # can say which of the two it was rather than showing an empty tab.
        "cellPunchEmitted": cell_punch_emitted,
        "cellPunchCells": len(cell_punch),
        "identitySummaryLimit": IDENTITY_SUMMARY_MAX_IDENTITIES,
        "readingsFloored": readings_floored,
        "cellsEmptied": cells_emptied,
        "cellsHighReference": cells_high_reference,
        "cellsSetAside": len(gated),
        # The same tally per clonotype, for the expansion, and present only when a gate was
        # declared: the UI's whole condition is an absent key. Sparse -- a clonotype that lost
        # nothing carries no entry, and an absent key reads as zero -- because this file is parsed
        # on every render.
        **(
            {"cellsSetAsideBySet": {k: v for k, v in count_by_set(cells_by_set, gated).items() if v > 0}}
            if args.gate_threshold
            else {}
        ),
        "panelLinesDropped": dropped_lines,
        "samples": samples,
        "setCount": len(cells_by_set),
        # How many DISTINCT panels the run carried. One means every sample was stained with the
        # same tags, and then how many of a clonotype's cells could answer is the same at every
        # identity -- its own cell count, which the grid already shows beside its name. The readout
        # uses this to decide whether the per-identity figure says anything.
        "samplePanelCount": len(set(panel_of_sample.values())),
    }
    with open(f"{prefix}_run_meta.json", "w") as out:
        json.dump(meta, out, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
