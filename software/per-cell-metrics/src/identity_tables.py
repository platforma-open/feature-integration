"""The identity, panel and per-cell tables, and the grouping rule they are all keyed by.

The grouping decides what an identity IS, so everything here is downstream of it: the
tag-to-identity map, the identity labels and properties, the panel ids, and the two wide
pivots. A caller that builds one of these against a different grouping than another gets
two tables that cannot be joined.
"""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from collections.abc import Iterable

import polars as pl
from panel import (
    ANY_SAMPLE,
    Grouping,
    default_grouping,
    property_columns,
)
from verdict import (
    Admissibility,
    State,
    UnreliableReason,
    cell_admissibility_reason,
)

CellKey = tuple[str, str]
# One column per identity, so this bounds the pivot's WIDTH and not its length. A pMHC panel
# carries more than a thousand identities and is out of this version's scope.
IDENTITY_SUMMARY_MAX_IDENTITIES = 10_000
# What joins several grouping columns into one identity key. A scientist may group on more than
# one column, and the identity is the distinct combination of their values. A panel value
# containing this separator would let two different combinations produce one key. That is reported
# rather than silently merged, and the run continues, because refusing the panel would reject a
# file that reads correctly under every other grouping.
GROUPING_KEY_SEPARATOR = " | "


def _grouping_columns(rule: dict, declared: list[str]) -> list[str]:
    """The columns a property rule names, as a list.

    Accepts `columns: [...]`, and the older `column: "..."` because a project stored before the rule
    took a list carries that shape. The alternative is a data migration over every stored project.
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

    A property grouping is built from `consistent_properties`, never from the panel column. The panel
    reader strips `tag` and `sample` and carries property values through exactly as written, so
    reading the column directly makes " Spike " and "Spike" two identities.

    Reference tags are excluded here rather than by `identity_universe`, which takes no reference
    tags and never will -- one place decides, so the two cannot drift. Leaving them in would give the
    comparator an identity of its own, read by comparing it against itself.

    A tag the grouping column says nothing about keeps its own identity rather than vanishing.
    Dropping it would remove a declared reagent from the answer with nothing downstream able to tell
    the panel was short.
    """
    by_tag = default_grouping(panel, reference_tags)
    # Type-checked before it is read as one. `--grouping '"tag"'` is valid JSON and not a mapping,
    # and reaching `.get` on it raises an AttributeError instead of the usage message two lines below.
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

    # Read PER PANEL ROW, never through `consistent_properties`. The panel declares per tag and
    # sample, so a value differing between a tag's rows is a declaration -- this barcode carries that
    # antigen in that sample -- not a disagreement to collapse. The tag-grain accessor discards exactly
    # the information the keying exists to carry, and every reused barcode then falls back to standing
    # alone under its raw sequence. Values are stripped here for the same reason
    # `consistent_properties` strips them.
    grouping: Grouping = {}
    ungrouped_pairs: list[tuple[str, str]] = []
    # What each identity was grouped ON, recorded here because this is the one place that knows both
    # the column and the row's value. A grouped-on column is a declaration of the identity, unique by
    # construction: every member carries the same value, because that value is what put it there. It
    # cannot be recovered later from tag-grain agreement, since a reused barcode has none.
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
        # ALL named columns must carry a value. A combination missing one component is not that
        # combination, and supplying the absent one would invent a declaration.
        if all(values):
            identity = GROUPING_KEY_SEPARATOR.join(values)
            grouping[(tag, sample)] = identity
            declared.setdefault(identity, {}).update(dict(zip(columns, values)))
            flagged.update(v for v in values if GROUPING_KEY_SEPARATOR.strip() in v)
        else:
            # A pair the grouping column says nothing about keeps its own identity rather than vanishing.
            # Dropping it would remove a declared reagent from the answer with nothing downstream able
            # to tell the panel was short.
            grouping[(tag, sample)] = tag
            ungrouped_pairs.append((tag, sample))
    # Reported as distinct TAGS, never pairs. The returned list travels in the run meta as
    # `tagsWithoutGroupingValue`, and the punchcard counts it in a banner naming barcodes rather than
    # (barcode, sample) pairs.
    ungrouped = sorted({tag for tag, _sample in ungrouped_pairs})
    if ungrouped:
        # Also returned, not only logged. A property the file does not carry narrows what can be
        # answered, and that narrowing has to be visible in the output. These tags are answered under a
        # grouping that could not place them, so a bare barcode sits among the family identities.
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

    Not keyed by sample. The linker lets a tag-keyed figure sit beside an identity-keyed verdict, and
    neither side carries a sample: verdicts are (set, identity) over clonotypes that span samples,
    and the per-tag figures are run-level. An axis no joined table has makes the join malformed.

    Many-to-many by construction: under (tag, sample) grouping one tag can feed a different identity
    in each sample. Distinct rows matter, because two tags of one identity would otherwise emit the
    same key twice, and duplicate axis keys break a grid silently.
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
    recognises. Under the per-tag grouping the identity is a barcode, so the panel's feature name
    stands in, and where two barcodes carry the same name the tag is appended.

    `disagreed` is the exception, and it applies to BOTH branches. `consistent_properties` drops a
    property a tag's rows disagree about, so the tag has no value to group on and no feature name to
    borrow, and stands under its raw barcode. Such an identity is labelled with the names it DID
    declare, joined: `SARS-TRI-S_WT / SARS-TRI-S_WT__alt1`.

    Which column's disagreements arrive here depends on the rule, and the caller resolves that.

    The uniqueness rule applies to joined names too. Two tags can disagree about the grouping column
    while declaring the SAME pair of names, joining to one string, so where any label repeats the
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
    # Over the IDENTITIES, never the grouping's keys. Under the per-tag grouping an identity is a tag,
    # but the grouping is keyed by (tag, sample), so its keys are pairs. Iterating them looks up a
    # tuple in `properties`, finds nothing, and drops every label back to the bare barcode.
    return tag_labels(set(grouping.values()), properties, feature_col, disagreed)


def tag_labels(
    tags: Iterable[str],
    properties: dict[str, dict[str, str]],
    feature_col: str,
    disagreed: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """A readable name per tag, never two tags under one name.

    Three rungs, in this order: the name the samples agreed on, else the names they disagreed about
    joined, else the bare barcode for a tag the panel named nowhere. The collision rule covers the
    joined strings too.

    Also the per-tag branch of `_identity_labels`, where an identity IS a tag. One rule, so a tag
    cannot read under one name beside its verdict and another beside its reagent figures.
    """
    joined = {tag: " / ".join(values) for tag, values in (disagreed or {}).items() if values}
    names = {tag: (properties.get(tag, {}).get(feature_col) or joined.get(tag) or tag) for tag in tags}
    collisions = Counter(names.values())
    return {tag: (f"{name} ({tag})" if collisions[name] > 1 else name) for tag, name in names.items()}


# The key column of result_identity_properties.csv. A panel column of the same name would collapse
# into the key as the frame is built: the property would not be dropped, it would silently BECOME
# the identity. Such a column is excluded from the export and reported.
IDENTITY_KEY_COLUMN = "identity"


def _identity_properties(
    grouping: Grouping,
    properties: dict[str, dict[str, str]],
    columns: list[str],
    declared: dict[str, dict[str, str]],
    disagreed: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """Per identity, the panel declarations that hold of it.

    A declaration reaches an identity two ways. The columns the scientist GROUPED ON arrive in
    `declared`, from the builder that formed the identities. They are declarations by construction:
    every member carries the same value, because that value is what put it there. They are taken
    rather than tested, and they must be -- a reused barcode has no tag-grain agreement to test.

    Every OTHER column holds only where all of the identity's member tags agree. A property differing
    between member tags is omitted, neither blanked nor resolved to a winner. This is
    `consistent_properties`' own rule lifted one grain: there it holds across a tag's ROWS, here
    across an identity's TAGS.

    A tag that declares nothing does not block its neighbours. `disagreed` separates that silence
    from a tag whose own rows contradict each other, which without it reaches the test below as the
    empty string and is filtered out like a blank cell. On a real sixteen-row panel an identity whose
    five member tags declared six different antigen names came back carrying ONE member's name,
    because four had contradicted themselves into silence. A member that contradicted itself is a
    disagreement, not a silence, and it blocks the property.

    Reference tags need no exclusion: `_build_grouping` keeps them out of the grouping.
    """
    # Distinct member tags per identity. The grouping is keyed (tag, sample), so one tag reaches an
    # identity once per sample that declares it there. The membership test keeps a tag from counting
    # twice -- a repeat would misreport how many tags an identity holds.
    tags_of: dict[str, list[str]] = {}
    for (tag, _sample), identity in sorted(grouping.items()):
        members = tags_of.setdefault(identity, [])
        if tag not in members:
            members.append(tag)

    conflicted = disagreed or {}
    held: dict[str, dict[str, str]] = {}
    for identity, tags in tags_of.items():
        # Seeded with what the identity was grouped on. Those columns are settled, so the agreement
        # test below skips them rather than re-deciding them from a grain that cannot answer.
        agreed: dict[str, str] = dict(declared.get(identity, {}))
        for column in columns:
            if column in agreed:
                continue
            # A member that contradicted itself blocks the property. Checked BEFORE the values are
            # gathered, because such a member contributes nothing to them and would otherwise look like
            # one that declared nothing.
            if any(tag in conflicted.get(column, {}) for tag in tags):
                continue
            values = {v for v in (properties.get(tag, {}).get(column, "") for tag in tags) if v}
            if len(values) == 1:
                agreed[column] = next(iter(values))
        held[identity] = agreed
    return held


def _panel_id(tags: frozenset[str]) -> str:
    """A stable id for a declared tag set.

    No panel file names its panel, so the id is derived from the sorted tag list and is the same in
    every re-run of the same declaration. Where one panel covers every sample, the axis takes a
    single value and drops out.
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

    `combine_cells` asserts the map is disjoint, so a cell listed under two sets fails loudly there
    rather than being counted twice into a tally that counts every cell once.
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

    Two populations use it: cells a gate set aside, and cells that read nothing at all. Both are
    properties of the cell rather than of a position. Every clonotype appears, zeros included,
    because a reader must not have to tell "none of them" apart from "column missing". A caller
    writing into the run record drops the zeros itself, since that file is parsed on every render.
    """
    return {set_id: sum(1 for key in cells if key in population) for set_id, cells in sorted(cells_by_set.items())}


def _pivot_identity_summary(verdicts: pl.DataFrame, universe: set[str]) -> tuple[pl.DataFrame, pl.DataFrame, bool]:
    """The per-set verdict row and its support, one column per identity in each.

    Pivoted onto the set axis alone, because the block that consumes this drops a column carrying an
    axis the clonotype anchor does not have, with no error. Gated on identity count: the pivot costs
    a column per identity.

    The second frame is the readout's, and its cell carries everything a reader needs to ask "why is
    this mark this colour": `state|answered|asked|agreement|reason|bound`. `agreement` and
    `reason` are empty where they do not apply. `bound` is last because it was appended, so a reader
    that destructures the first five fields positionally still decodes an older value.

    No score, and no binding level: the cell explains a verdict by what it RESTS on -- how many cells
    could answer, how many did, how far they agreed.

    One column rather than five. Both counts must travel with a verdict wherever it appears, and a
    punchcard drawn from the state pivot alone would not. The support cannot arrive as sibling
    columns either: a column name here IS an antigen name from a customer's panel file, so any suffix
    marking a support column is a name some panel is entitled to use, and a grid pairs cells only by
    position.

    The state pivot is left as it is, because lead selection reads it and a compound value would not
    filter.
    """
    if len(universe) > IDENTITY_SUMMARY_MAX_IDENTITIES or verdicts.height == 0:
        sets = verdicts.select("setId").unique() if verdicts.height else pl.DataFrame(schema={"setId": pl.String})
        return sets, sets, False
    ordered = ["setId", *sorted(universe)]
    states = verdicts.pivot(on="identity", index="setId", values="state").select(ordered)
    # Every part is cast and null-filled before joining. concat_str propagates a null through the whole
    # value, so one absent agreement would blank the state beside it.
    punch = verdicts.with_columns(
        pl.concat_str(
            [
                pl.col("state"),
                pl.col("cellsAnswered").cast(pl.String).fill_null(""),
                pl.col("cellsAsked").cast(pl.String).fill_null(""),
                pl.col("agreement").cast(pl.String).fill_null(""),
                pl.col("unreliableReason").cast(pl.String).fill_null(""),
                # Sixth, and APPENDED rather than inserted, so a reader that destructures the first five
                # fields positionally still reads them correctly and a project whose last run predates this
                # field renders unchanged.
                pl.col("cellsBound").cast(pl.String).fill_null(""),
            ],
            separator="|",
        ).alias("punch")
    ).pivot(on="identity", index="setId", values="punch")
    return states, punch.select(ordered), True


# A run whose cell count passes this gets no per-cell punchcard. The frame below is the DENSE
# per-cell-per-identity grid the rest of this module never builds -- 11-20x the sparse input on a
# realistic panel. This bounds the row count; IDENTITY_SUMMARY_MAX_IDENTITIES bounds the width.
#
# Above the line the export is skipped and `cellPunchEmitted` in the run record carries that. No page
# reads that flag yet, so the skip is currently silent to a reader.
CELL_PUNCH_MAX_CELLS = 2_000_000


def _pivot_cell_punch(
    states: pl.DataFrame,
    cells_by_set: dict[str, list[CellKey]],
    offered_by_sample: dict[str, set[str]],
    admissibility: Admissibility,
    universe: set[str],
) -> tuple[pl.DataFrame, bool]:
    """One row per cell, one column per identity: that cell's own reading, not its set's verdict.

    The same four states the set-level card uses. Three come straight from `read_states`. The fourth
    is structural -- an identity no sample holding this cell offered is NEVER_ASKED -- and it is the
    only way a position here is blank.

    **A cell with no row in `states` is not an absence.** It was asked and read nothing, its count is
    zero, and a zero count resolves the same way every time: NOT_BOUND, unless the cell cannot be
    compared, in which case UNRELIABLE. That is `silent_tally`'s rule, not re-derived here because the
    deciding function, `_admissibility_reason`, is the one both `read_states` and `silent_tally`
    already call. Drawing a silent cell as an empty position would contradict the arithmetic that
    produced its set's verdict, where the same cell voted.

    `setId` travels as a COLUMN rather than an axis. The readout shows one clonotype at a time and
    filters on it, and a cell belongs to exactly one set.
    """
    members = [(sample, cell, set_id) for set_id, keys in sorted(cells_by_set.items()) for sample, cell in keys]
    ordered_identities = sorted(universe)
    empty = pl.DataFrame(schema={"sampleId": pl.String, "cellId": pl.String, "setId": pl.String})
    if not members or not ordered_identities:
        return empty, False
    # Both gates, and the identity one is the same limit the set-level pivot uses: a column per
    # identity is a p-column per identity, whichever axis the rows are on.
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
    # The cell's own half of the reason, the admissibility gate, is one row per member rather than one
    # per member and identity, because no identity changes whether a cell was set aside. The other half,
    # where a comparator is keyed by identity, is joined below as (sample, identity): a frame of samples
    # by identities, thousands of rows against the grid's tens of millions.
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

    # Joined to `offered` rather than crossed with the universe: a position no sample holding the cell
    # offered must not appear at all, or the silent rule below would resolve a question nobody asked.
    #
    # A comparator that is not keyed by cell leaves some POSITIONS uncomparable while the cell itself is
    # fine, and `cell_admissibility_reason` cannot see that -- it answers about the cell. Two bundle
    # shapes do it and at most one is ever set: a per-(sample, identity) background, and the fitted
    # rung's per-(sample, cell, identity) probabilities. Carried as the positions that DID get a
    # comparator, so the rest fall out of a left join as nulls and reach the silent rule below as
    # *unreliable* rather than as *not bound*.
    comparable, comparable_on = None, None
    if admissibility.by_identity is not None:
        comparable_on = ["sampleId", "identity"]
        comparable = pl.DataFrame(
            sorted(admissibility.by_identity),
            orient="row",
            schema={"sampleId": pl.String, "identity": pl.String},
        ).with_columns(pl.lit(True).alias("_comparable"))
    elif admissibility.probabilities is not None:
        comparable_on = ["sampleId", "cellId", "identity"]
        comparable = pl.DataFrame(
            sorted(admissibility.probabilities),
            orient="row",
            schema={"sampleId": pl.String, "cellId": pl.String, "identity": pl.String},
        ).with_columns(pl.lit(True).alias("_comparable"))

    grid = (
        member_frame.join(offered_frame, on="sampleId", how="inner")
        .join(reasons, on=["sampleId", "cellId"], how="left")
        .join(
            states.select("sampleId", "cellId", "identity", "state", "unreliableReason"),
            on=["sampleId", "cellId", "identity"],
            how="left",
        )
    )
    if comparable is not None:
        grid = grid.join(comparable, on=comparable_on, how="left").with_columns(
            pl.when(pl.col("cellReason").is_not_null())
            .then(pl.col("cellReason"))
            .when(pl.col("_comparable").is_null())
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
        # The reason a POSITION is unreliable where one was recorded, and the cell's own reason where
        # the position is silent. Never both: a recorded row already carries whichever applied.
        pl.when(pl.col("unreliableReason").is_not_null())
        .then(pl.col("unreliableReason"))
        .otherwise(pl.col("cellReason"))
        .alias("reason"),
    )

    # How many identities this cell read BOUND, over the identities it was asked. Counted before the
    # pivot, where it is one group_by, and from the resolved state, so a silent position counts as the
    # not-bound it is.
    bound_counts = (
        grid.group_by("sampleId", "cellId")
        .agg((pl.col("cellState") == State.BOUND.value).sum().alias("boundIdentities"))
        .with_columns(pl.col("boundIdentities").cast(pl.Int64))
    )

    # `state|reason`, two fields and nothing else. The set-level punch carries six because a verdict
    # rests on counts a reader needs beside it. A cell IS the evidence.
    punch = grid.with_columns(
        pl.concat_str([pl.col("cellState"), pl.col("reason").fill_null("")], separator="|").alias("punch")
    ).pivot(on="identity", index=["sampleId", "cellId"], values="punch")

    # Every identity gets a column even where no cell was offered it, so the readout's columns are the
    # panel rather than whatever this run happened to ask.
    for identity in ordered_identities:
        if identity not in punch.columns:
            punch = punch.with_columns(pl.lit(None, dtype=pl.String).alias(identity))

    return (
        punch.join(member_frame, on=["sampleId", "cellId"], how="left")
        .join(bound_counts, on=["sampleId", "cellId"], how="left")
        .select("sampleId", "cellId", "setId", "boundIdentities", *ordered_identities),
        True,
    )
