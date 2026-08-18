"""The entrypoint: counts, a panel and a cell list become a four-state verdict.

Composes the reading in one order, and the order is load-bearing at every
step: the floor works on the raw per-(cell, tag) counts; a cell's reference
reading is taken from the floored frame; tags combine into an identity by the
highest of their counts; the identity's count is read against that cell's own
reference; and a set's cells combine by majority. Reversing any pair changes
the answer -- flooring after combining would floor one reading where two were
taken, and taking the reference before the floor would compare against a
number the floor has already been applied to elsewhere.

**The grid of every cell against every identity is never built.** A silent
cell -- one asked about an identity and showing no reading for it -- scores
`specificity_score(0, r)`, which is at most ~0.0422 and falls as the
reference rises, so it settles *not bound* unless the cell itself cannot be
compared. `silent_tally` counts those positions analytically, and this
entrypoint never materializes them: on a realistic panel the grid is 11-20x
the sparse input and a pMHC panel does not fit at all. Two consequences are
enforced here rather than downstream. A `--cutoff` at or below that ~0.0422
bound is refused, because below it the analytic count and the row-per-position
reference disagree with no error raised. And the row-per-position reference
implementation in verdict.py is never called from production; the test suite
asserts this file does not name it.

`offered` is keyed by SAMPLE throughout and is never regrouped by set. What a
panel offered is a property of the staining, which is done per sample; a set
spanning two samples was offered whatever either sample's panel offered, and
`combine_cells` takes that union itself. Keying the map by set instead makes
every lookup miss, reads every offered set as empty, and raises nothing.

One `Admissibility` bundle is built and handed to `read_states`,
`combine_cells` and `self_disagreement` alike. The bundle exists so those
cannot be given different reference dicts and then disagree about which cells
"cannot be compared", which shows up as a silent-position count that is wrong
or negative rather than as an error.

Every frame is sorted before it is written. `combine_tags_to_identities`
groups without maintaining order, so an unsorted frame varies run to run, and
a p-column's identity is its content -- an unstable byte order costs every
downstream node its dedup with nothing to show for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from typing import NamedTuple

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
    Status,
    antigen_count_deciles,
    attach_alerting_identities,
    outlier_status,
    per_antigen_measures,
    reads_per_cell,
    roll_up,
    roll_up_capture,
    roll_up_panel,
    status_for,
)
from verdict import (
    BOUND_CUTOFF,
    DEFAULT_FLOOR,
    DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE,
    DEFAULT_PANEL_MIN_MEMBERS,
    DEFAULT_REFERENCE_THIN_LINE,
    Admissibility,
    ReferenceChoice,
    _cell_admissibility_reason,
    apply_floor,
    combine_tags_to_identities,
    gate_cells,
    read_states,
    reference_by_cell,
    resolve_default_source,
    specificity_score,
)

CellKey = tuple[str, str]

# A silent cell's count is zero, and a zero count's best possible score is
# specificity_score(0, 0). At or below it the analytic silent count and the
# row-per-position reference part company over a silent admissible cell,
# quietly: one calls it bound, the other not bound, and nothing raises.
# `silent_tally` states that refusing such a cutoff belongs to the CLI.
ANALYTIC_CUTOFF_BOUND = float(specificity_score(0, 0))

# The pivoted per-identity summary costs one column per identity, so it is
# emitted only for panels small enough that a wide frame is still a table a
# reader can open. Declared rather than derived -- nothing published says
# where a table stops being readable -- and deliberately well under the
# thousand-plus identities a pMHC panel carries.
IDENTITY_SUMMARY_MAX_IDENTITIES = 100

# A rollup is reported in the same frame as the measurements it aggregates,
# as a row whose measurement is the rollup itself. A measurement is an axis
# value here, so a level's summary costs a row rather than a column.
ROLLUP = "rollup"
ROLLUP_COUNTS = "The worst status among this level's measurements, and how much of it was checked."

MEASUREMENT_BY_ID = {m.id: m for m in MEASUREMENTS}


class QcRow(NamedTuple):
    """One measurement at one level entity, before its declaration is attached.

    `status` and `coverage` are both carried because a measurement's own
    status is not recoverable from a coverage triple: `roll_up` reports
    *not evaluated* for a level with nothing judgeable in it, so a row that
    was computed and left unjudged would come back saying nobody looked. The
    triple says how much of the level was checked; the status says whether
    what was checked is wrong.

    `panel_id` is set on tag-level and identity-level rows and left empty on
    the rest: a panel carries the worst status among its per-tag and
    per-identity measurements, so those rows have to say which panel they
    belong to or the panel rollup has nothing to gather.
    """

    level: str
    entity: str
    measurement: str
    value: float | None
    detail: str
    panel_id: str
    status: Status
    coverage: Coverage


def _write_sorted(frame: pl.DataFrame, path: str, by: list[str]) -> None:
    """Write a frame in a fixed row order, header-only when it has no rows.

    Every frame reaching here is built with an explicit schema, so an empty
    one still carries its columns and writes a header rather than an empty
    file. A consumer meeting a header-only frame knows the step ran and found
    nothing; one meeting an empty file cannot tell that from a step that
    never ran.
    """
    frame.sort(by).write_csv(path)


def _read_columns(path: str, columns: tuple[str, ...], what: str) -> pl.DataFrame:
    """Read a CSV as strings, keeping the named columns and stripping them.

    Read as strings and stripped because these columns are join keys against
    the panel, whose reader strips `tag` and `sample` for the same reason. A
    tag written " AAAA " on one side and "AAAA" on the other joins to nothing
    and reports the barcode as both undeclared and never seen.
    """
    frame = pl.read_csv(path, infer_schema_length=0)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise SystemExit(f"{what} {path!r} has no column(s) {missing}; columns are {frame.columns}")
    return frame.select([pl.col(c).str.strip_chars().fill_null("") for c in columns])


def _read_counts(path: str) -> pl.DataFrame:
    counts = _read_columns(path, ("sampleId", "cellId", "tag", "umiCount"), "counts file")
    return counts.with_columns(pl.col("umiCount").cast(pl.Int64))


def _json_arg(raw: str | None, flag: str):
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag} is not valid JSON: {exc}") from exc


def _build_grouping(
    rule: dict | None,
    panel: pl.DataFrame,
    properties: dict[str, dict[str, str]],
    reference_tags: set[str],
) -> tuple[Grouping, str, list[str]]:
    """The tag -> identity map the run reads at, and the id of the rule behind it.

    A property grouping is built from `consistent_properties`, never from the
    panel column: the panel reader strips `tag` and `sample` and carries
    property values through exactly as written, so reading the column
    directly makes " Spike " and "Spike" two identities that no fixture
    without stray whitespace would ever reveal.

    Reference tags are excluded here rather than by `identity_universe`,
    which takes no reference tags and never will -- one place decides, so the
    two cannot drift. Leaving them in would give the comparator an identity
    of its own, with a verdict read by comparing it against itself.

    A tag the grouping column says nothing about keeps its own identity
    instead of vanishing. Dropping it would remove a declared reagent from
    the answer with nothing downstream able to tell the panel was short.
    """
    by_tag = default_grouping(panel, reference_tags)
    if rule is None or rule.get("by") == "tag":
        return by_tag, "per-tag", []
    if rule.get("by") != "property":
        raise SystemExit(f"--grouping must be {{'by':'tag'}} or {{'by':'property','column':...}}; got {rule!r}")

    column = rule.get("column") or ""
    declared = property_columns(panel)
    if column not in declared:
        raise SystemExit(f"--grouping names property column {column!r}, which the panel does not declare: {declared}")

    grouping: Grouping = {}
    ungrouped: list[str] = []
    for tag in sorted(by_tag):
        value = properties.get(tag, {}).get(column)
        if value:
            grouping[tag] = value
        else:
            grouping[tag] = tag
            ungrouped.append(tag)
    if ungrouped:
        # Also returned, not only logged. A property the file does not carry
        # narrows what can be answered, and that narrowing has to be visible in
        # the output rather than in a log line nobody reads afterwards: these
        # tags are answered under a grouping that could not place them, so a
        # bare barcode sits among the family identities and only this says why.
        print(
            f"[emit-verdicts] {len(ungrouped)} tag(s) carry no agreed {column!r} value and stand as their own "
            f"identity: {ungrouped[:8]}",
            file=sys.stderr,
        )
    return grouping, f"property:{column}", ungrouped


def _identity_labels(
    grouping: Grouping, properties: dict[str, dict[str, str]], feature_col: str, rule_id: str
) -> dict[str, str]:
    """A readable name per identity, never two identities under one name.

    Under a property grouping the identity is the property value, which is
    already the name a reader recognises. Under the per-tag grouping the
    identity is a barcode, so the panel's feature name stands in -- and where
    two barcodes carry the same name the tag is appended, because two
    identities sharing a label are two rows a reader cannot tell apart.
    """
    if rule_id != "per-tag":
        return {identity: identity for identity in set(grouping.values())}
    names = {tag: (properties.get(tag, {}).get(feature_col) or tag) for tag in grouping}
    collisions = Counter(names.values())
    return {tag: (f"{name} ({tag})" if collisions[name] > 1 else name) for tag, name in names.items()}


def _panel_id(tags: frozenset[str]) -> str:
    """A stable id for a declared tag set.

    No panel file names its panel, so the id is derived from the sorted tag
    list and is the same in every re-run of the same declaration. Where one
    panel covers every sample the axis takes a single value and drops out.
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

    `combine_cells` asserts the map is disjoint, so a cell listed under two
    sets fails loudly there rather than being counted twice into a tally that
    counts every cell once.
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


def _pivot_identity_summary(verdicts: pl.DataFrame, universe: set[str]) -> tuple[pl.DataFrame, bool]:
    """The per-set verdict row, one column per identity.

    Pivoted onto the set axis alone because a column carrying an axis the
    clonotype anchor does not have is dropped with no error by the block that
    consumes this, so a `(set, identity)` column is invisible there. Gated on
    identity count: the pivot costs a column per identity and a large panel
    would turn one artifact into a thousand.
    """
    if len(universe) > IDENTITY_SUMMARY_MAX_IDENTITIES or verdicts.height == 0:
        sets = verdicts.select("setId").unique() if verdicts.height else pl.DataFrame(schema={"setId": pl.String})
        return sets, False
    wide = verdicts.pivot(on="identity", index="setId", values="state")
    return wide.select(["setId", *sorted(universe)]), True


def _leaf(level, entity, measurement, value, detail, panel_id, status: Status) -> QcRow:
    """One measurement's row: its own status, and the coverage of that one status.

    The triple comes from `roll_up` so a leaf and a rollup are counted by one
    rule, but the row keeps the status `roll_up` would have flattened.
    """
    return QcRow(level, entity, measurement, value, detail, panel_id, status, roll_up([status]))


def _sum_coverage(status: Status, parts: list[Coverage]) -> Coverage:
    """A rollup over rollups: the status from the rollup rule, the counts summed.

    `roll_up_capture` takes statuses, so handing it the statuses of levels
    that were themselves rolled up gives the right status and the wrong
    counts -- a sample that was fully computed but had nothing judgeable
    arrives as *not evaluated* and increments the capture's not-evaluated
    count, collapsing "nothing was wrong" into "nobody looked". Summing the
    constituent coverages keeps the two apart.
    """
    return Coverage(
        status,
        sum(c.judged for c in parts),
        sum(c.unjudged for c in parts),
        sum(c.not_evaluated for c in parts),
    )


def _qc_frame(rows: list[QcRow]) -> pl.DataFrame:
    """The measurement set as a frame keyed (level, entity, measurement).

    Every declared measurement keeps its place whether or not this run could
    compute it, and a measurement nothing computed reads *not evaluated* with
    its reason rather than being absent: a reader must never mistake "nothing
    computed this yet" for "this was checked and found fine".

    A field with nothing in it is written null rather than as an empty string.
    polars quotes an empty string to keep it apart from a null, and a quoted
    empty cell is a value a downstream import would carry as one.
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
                "value": row.value,
                "detail": row.detail or None,
                "status": row.status.value,
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

    `status_for` refuses the two measurements judged against the run itself;
    those are added through `outlier_status` at their own call sites and
    never reach here.
    """
    rows.append(
        _leaf(level, entity, measurement, value, detail, panel_id, status_for(measurement, value, DEFAULT_LINES))
    )


def _median(values: list[float]) -> float | None:
    return float(pl.Series(values).median()) if values else None


# Long on purpose, and not decomposed: this is one composition taken in the
# one order the reading has, and splitting it into stages would put the order
# in the call sites rather than in the code a reader follows top to bottom.
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
        default=None,
        choices=["declared", "panel", "none"],
        help="which comparator to ask for; the run may serve 'none' instead, never a different one",
    )
    p.add_argument("--panel-min-members", type=int, default=DEFAULT_PANEL_MIN_MEMBERS)
    p.add_argument("--reference-thin-line", type=int, default=DEFAULT_REFERENCE_THIN_LINE)
    p.add_argument("--floor", type=int, default=DEFAULT_FLOOR, help="zero every non-comparator reading below this")
    p.add_argument(
        "--cutoff", type=float, default=BOUND_CUTOFF, help="specificity score at or above which a cell binds"
    )
    p.add_argument("--min-voters", type=int, default=DEFAULT_MIN_VOTERS)
    p.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT)
    p.add_argument("--gate-threshold", type=int, default=None, help="set aside cells whose comparator reads this high")
    p.add_argument("--high-reference-line", type=int, default=DEFAULT_HIGH_REFERENCE_OBSERVATION_LINE)
    p.add_argument("--grouping", default=None, help="JSON: {'by':'tag'} or {'by':'property','column':...}")
    p.add_argument("--contending", default=None, help="JSON: groups of identities that contend, as a list of lists")
    p.add_argument("--capture-map", default=None, help="JSON: sampleId -> captureId")
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

    prop_cols = property_columns(panel)
    properties, inconsistent = consistent_properties(panel, prop_cols)
    for tag, column, values in inconsistent:
        print(
            f"[emit-verdicts] tag {tag!r} declares {column!r} as {values}; it carries no agreed value", file=sys.stderr
        )

    # The reference designation is read through `consistent_properties`, which
    # strips the value and drops any property a tag's rows disagree about. A
    # per-sample comparator designation is therefore discarded rather than
    # honoured, which is what `apply_floor` documents: a tag is a comparator in
    # every sample or in none.
    reference_values = {v.strip() for v in args.reference_values.split(",") if v.strip()}
    reference_tags: set[str] = set()
    if args.role_column and reference_values:
        if args.role_column not in prop_cols:
            raise SystemExit(f"--role-column {args.role_column!r} is not a panel column; columns are {prop_cols}")
        reference_tags = {t for t, props in properties.items() if props.get(args.role_column) in reference_values}

    grouping_rule = _json_arg(args.grouping, "--grouping")
    grouping, grouping_id, ungrouped_tags = _build_grouping(grouping_rule, panel, properties, reference_tags)
    universe = identity_universe(panel, grouping)
    by_tag_grouping = default_grouping(panel, reference_tags)
    tag_universe = identity_universe(panel, by_tag_grouping)

    contending_raw = _json_arg(args.contending, "--contending") or []
    contending = [set(group) for group in contending_raw]
    capture_of_sample: dict[str, str] = _json_arg(args.capture_map, "--capture-map") or {}

    counts = _read_counts(args.counts_csv)

    # The cell list is an input and never derived from the antigen readings:
    # nothing in the counts separates a cell from a droplet that held none.
    # `--cells` wins over the linker where both arrive, because a list from
    # gene expression covers cells whose receptor never assembled and the
    # linker cannot.
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
        # No list arrived, and one is NOT derived from the counts. Nothing in
        # the antigen readings separates a cell from a droplet that held none,
        # so the observed barcodes are not a cell list -- in droplet data they
        # outnumber the cells by one to two orders of magnitude, because ambient
        # antigen material lands on most barcodes. Standing them in would not
        # merely be approximate: `readsPerCell` divides by this, so a healthy
        # library would read undersequenced and alert.
        #
        # Every barcode is still analysed and every count still emitted. What is
        # withheld is the claim that these barcodes are cells: `inCellList` is
        # unknown rather than true, and the measurements that need a cell list
        # read *not evaluated*, which is exactly the reading for "the run could
        # not supply what this needed".
        cell_list = None
        cell_list_source = "none"

    # `cell_list is None` means no list arrived, which is different from a list
    # that arrived empty: the first cannot answer "is this barcode a cell", the
    # second answers "no". `listed` collapses both for the set arithmetic below,
    # where either way there are no barcodes to add.
    listed = cell_list if cell_list is not None else set()

    observed_cells = set(counts.select("sampleId", "cellId").unique().iter_rows())
    # Barcodes outside the cell list stay in the frame, labelled: one dropped
    # here is indistinguishable afterwards from one that never existed, and
    # its antigen counts are real whatever the list says about it.
    analysed_cells = sorted(listed | observed_cells | linker_cells)

    panel_samples = {s for s in panel["sample"].to_list() if s != ANY_SAMPLE}
    samples = sorted(
        {s for s, _ in observed_cells} | {s for s, _ in listed} | {s for s, _ in linker_cells} | panel_samples
    )

    # The floor is applied per sample so the counters it returns land in each
    # sample's own QC row. A cell key carries its sample, so partitioning is
    # exact on both counters and the run totals are their sums -- there is no
    # second implementation of the rule to drift from this one.
    floor_stats: dict[str, dict[str, int]] = {}
    parts = []
    for sample in samples:
        floored_part = apply_floor(counts.filter(pl.col("sampleId") == sample), args.floor, reference_tags)
        parts.append(floored_part.counts)
        floor_stats[sample] = floored_part.stats
    floored = pl.concat(parts) if parts else counts
    readings_floored = sum(s["readingsFloored"] for s in floor_stats.values())
    cells_emptied = sum(s["cellsEmptied"] for s in floor_stats.values())

    # One panel size, read once and passed to both. Deriving it separately for
    # the default choice and for the resolution would let the two disagree about
    # whether the panel is large enough to serve as its own comparator.
    panel_size = int(panel["tag"].n_unique())

    source = ReferenceChoice[args.reference_source.upper()] if args.reference_source else None
    if source is None:
        source = resolve_default_source(reference_tags, panel_size, args.panel_min_members)
    reference = reference_by_cell(
        floored,
        reference_tags,
        source,
        cells=analysed_cells,
        panel_size=panel_size,
        min_members=args.panel_min_members,
    )
    gated, cells_high_reference = gate_cells(reference.by_cell, args.gate_threshold, args.high_reference_line)

    # Built once and handed to every consumer. Two bundles built from two
    # reference dicts do not raise; they disagree about which cells cannot be
    # compared, and the silent-position count comes out wrong or negative.
    admissibility = Admissibility(reference.by_cell, args.reference_thin_line, gated)

    non_reference = floored.filter(~pl.col("tag").is_in(list(reference_tags))) if reference_tags else floored
    identities = combine_tags_to_identities(non_reference, grouping)
    states = read_states(identities, admissibility, args.cutoff)

    # The per-tag reading is diagnostic only -- it compares each tag against
    # the reference separately, which no verdict is built from -- but the
    # measurement set carries it at both levels always, so where the chosen
    # grouping is not the per-tag one it is read a second time.
    if grouping == by_tag_grouping:
        tag_states = states
    else:
        tag_states = read_states(combine_tags_to_identities(non_reference, by_tag_grouping), admissibility, args.cutoff)

    offered_by_sample = {s: offered_identities(panel, grouping, [s]) for s in samples}
    tag_offered_by_sample = {s: offered_identities(panel, by_tag_grouping, [s]) for s in samples}

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
    _write_sorted(verdicts, f"{prefix}_verdicts.csv", ["setId", "identity"])
    _write_sorted(set_counts(verdicts), f"{prefix}_set_counts.csv", ["setId"])

    summary, summary_emitted = _pivot_identity_summary(verdicts, universe)
    _write_sorted(summary, f"{prefix}_identity_summary.csv", ["setId"])

    # The sparse per-tag counts and the per-cell scalars together carry every
    # per-cell state, at a small fraction of the size a per-cell-per-identity
    # table would take. They stay inside the block: reading the same experiment
    # under another grouping is another execution rather than a re-derivation a
    # reader performs, and the grouping enters after the counting, so a second
    # execution over unchanged inputs re-does the verdict step alone.
    # With no list, membership is unknown rather than false: a barcode nobody
    # classified is not a barcode classified as "not a cell". "false" would be
    # a claim the run cannot support.
    unlisted_reads = "false" if cell_list is not None else "unknown"
    in_list = pl.DataFrame(
        [(s, c, "true") for s, c in sorted(listed)],
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "inCellList": pl.String},
    )
    reference_frame = pl.DataFrame(
        [(s, c, reference.by_cell.get((s, c))) for s, c in analysed_cells],
        orient="row",
        schema={"sampleId": pl.String, "cellId": pl.String, "referenceCount": pl.Int64},
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
        .with_columns(
            pl.Series(
                "admissibility",
                [
                    (lambda reason: "admissible" if reason is None else reason.value)(
                        _cell_admissibility_reason(key, admissibility)
                    )
                    for key in analysed_cells
                ],
                dtype=pl.String,
            )
        )
        .select(["sampleId", "cellId", "referenceCount", "admissibility", "inCellList"])
    )
    _write_sorted(cell_scalars, f"{prefix}_cell_scalars.csv", ["sampleId", "cellId"])

    # Both of these frames are pure key sets -- what a sample was offered, and
    # which identity a tag feeds -- and each carries a constant value column so
    # it can become a p-column at all. A frame of key columns alone imports as
    # nothing: columns are built from value columns, so a key-only file yields
    # no column and the fact it records never leaves the block.
    offered_frame = pl.DataFrame(
        [(sample, identity, "true") for sample in samples for identity in sorted(offered_by_sample[sample])],
        orient="row",
        schema={"sampleId": pl.String, "identity": pl.String, "offered": pl.String},
    )
    _write_sorted(offered_frame, f"{prefix}_offered.csv", ["sampleId", "identity"])

    # The value column is named "1" and holds 1, matching the cell-linker
    # convention already used for linker columns elsewhere in the platform.
    linker_frame = pl.DataFrame(
        [(tag, identity, 1) for tag, identity in sorted(grouping.items())],
        orient="row",
        schema={"tag": pl.String, "identity": pl.String, "1": pl.Int64},
    )
    _write_sorted(linker_frame, f"{prefix}_tag_identity.csv", ["tag", "identity"])

    labels = _identity_labels(grouping, properties, args.feature_col, grouping_id)
    identity_labels = pl.DataFrame(
        [(identity, labels.get(identity, identity)) for identity in sorted(universe)],
        orient="row",
        schema={"identity": pl.String, "label": pl.String},
    )
    _write_sorted(identity_labels, f"{prefix}_identity_labels.csv", ["identity"])

    declared = _declared_by_sample(panel, samples)
    panel_of_sample = {sample: _panel_id(tags) for sample, tags in declared.items()}
    tags_of_panel: dict[str, frozenset[str]] = {panel_of_sample[s]: declared[s] for s in samples}
    samples_of_panel: dict[str, list[str]] = {}
    for sample in samples:
        samples_of_panel.setdefault(panel_of_sample[sample], []).append(sample)

    panel_labels = pl.DataFrame(
        [
            (panel_id, f"{len(tags_of_panel[panel_id])} tags: {', '.join(samples_of_panel[panel_id])}")
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

    # Both directions of the panel-versus-reads check, re-keyed onto the
    # panel: a per-tag failure is a property of the declared tag set rather
    # than of any one sample that carries it. The samples reporting it travel
    # in the row so nothing about where it was seen is lost.
    seen = counts.select("sampleId", "tag").unique()
    unknown_panel = _panel_id(frozenset())
    mismatch_rows: dict[tuple[str, str, str], set[str]] = {}
    for row in panel_read_mismatch(panel, seen).iter_rows(named=True):
        # In the unkeyed case every row comes back under "*", which is not a
        # sample id: the declaration really is global, so it reports against
        # every sample in the run.
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

    identity_dis = self_disagreement(
        states.select("sampleId", "cellId", pl.col("identity").alias("key"), "state"),
        universe,
        offered_by_sample,
        cells_by_set,
        admissibility,
        "identity",
    )
    tag_dis = self_disagreement(
        tag_states.select("sampleId", "cellId", pl.col("identity").alias("key"), "state"),
        tag_universe,
        tag_offered_by_sample,
        cells_by_set,
        admissibility,
        "tag",
    )

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
        _add(rows, "sample", sample, "sequencingSaturation", None)
        # The denominator is the cell list, never the barcodes the reads
        # happened to touch: the five-thousand recommendation is per called
        # cell, and in droplet data observed barcodes run one to two orders of
        # magnitude higher, so dividing by them would alert on a healthy run.
        # No cell list means no denominator, so depth is *not evaluated* --
        # the run could not supply what the measurement needed. Substituting
        # the observed barcodes would answer a different question and, being
        # one to two orders of magnitude larger, would alert on a fine library.
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

        here = {key: value for key, value in reference.by_cell.items() if key[0] == sample}
        _, high_here = gate_cells(here, None, args.high_reference_line)
        _add(rows, "sample", sample, "highReferenceCells", float(high_here), f"cellsWithAComparator={len(here)}")
        _add(rows, "sample", sample, "knownAnswerRecovered", None)

        sample_coverage[sample] = roll_up([r.status for r in rows[first:]])

    tag_rate = dict(zip(tag_dis["key"].to_list(), tag_dis["disagreementRate"].to_list(), strict=True))
    identity_rate = dict(zip(identity_dis["key"].to_list(), identity_dis["disagreementRate"].to_list(), strict=True))
    identities_of_panel: dict[str, set[str]] = {
        panel_id: {grouping[t] for t in tags if t in grouping} for panel_id, tags in tags_of_panel.items()
    }
    per_sample_tag_total = {
        (row["sampleId"], row["tag"]): row["total"]
        for row in counts.group_by(["sampleId", "tag"])
        .agg(pl.col("umiCount").sum().alias("total"))
        .iter_rows(named=True)
    }

    panel_coverage: dict[str, Coverage] = {}
    for panel_id in sorted(tags_of_panel):
        first = len(rows)
        panel_samples_here = samples_of_panel[panel_id]
        panel_tags = tags_of_panel[panel_id]
        here_total = {
            tag: float(sum(per_sample_tag_total.get((s, tag), 0) for s in panel_samples_here))
            for tag in {t for (s, t) in per_sample_tag_total if s in panel_samples_here} | set(panel_tags)
        }
        observed_here = {tag for tag, total in here_total.items() if total > 0}

        # A declared tag is alerting at zero reads, so every declared tag gets
        # a row rather than only the ones that produced nothing: reporting
        # only the failures leaves a reader unable to tell a clean panel from
        # an unchecked one.
        for tag in sorted(panel_tags):
            _add(rows, "tag", tag, "declaredNeverSeen", here_total[tag], "", panel_id)
        for tag in sorted(observed_here - panel_tags):
            _add(rows, "tag", tag, "undeclaredBarcodes", here_total[tag], "", panel_id)

        panel_states = tag_states.filter(pl.col("sampleId").is_in(panel_samples_here)).rename({"identity": "tag"})
        for row in per_antigen_measures(panel_states).iter_rows(named=True):
            _add(
                rows,
                "tag",
                row["tag"],
                "perAntigen",
                float(row["cellsAboveTheLine"]),
                f"cellsWithSignal={row['cellsWithSignal']}|medianAboveTheLine={row['medianAboveTheLine']}",
                panel_id,
            )

        # Judged against the run rather than against a line, so `status_for`
        # refuses these and `outlier_status` answers instead. The peers are
        # the other members of the same panel and never include the value
        # being judged: including it would inflate the upper quartile it is
        # then measured against, so the one reading the measure exists to
        # catch is the one it would miss.
        disagreement_at = len(rows)
        for tag in sorted(panel_tags & set(tag_rate)):
            peers = [tag_rate[o] for o in panel_tags if o != tag and tag_rate.get(o) is not None]
            status = outlier_status(tag_rate[tag], peers)
            rows.append(_leaf("tag", tag, "tagDisagreement", tag_rate[tag], "", panel_id, status))

        # Beside an alerting tag, the figures for the identities it feeds. A
        # noisy reagent whose identities read steady is a reagent to replace,
        # not a run to distrust, and only the two numbers together say which.
        # Neither is suppressed: the identity rows are emitted in full below,
        # and this attaches a copy to the tag that raised the question so a
        # reader meeting the alert does not have to go looking.
        alerting_tags = {r.entity for r in rows[disagreement_at:] if r.status is Status.ALERTING}
        if alerting_tags:
            beside = attach_alerting_identities(
                pl.DataFrame(
                    [
                        (identity, identity_rate[identity])
                        for identity in sorted(identities_of_panel[panel_id] & set(identity_rate))
                    ],
                    orient="row",
                    schema={"key": pl.String, "identityDisagreement": pl.Float64},
                ),
                {tag: {grouping[tag]} for tag in panel_tags if tag in grouping},
                alerting_tags,
            )
            attached: dict[str, list[str]] = {}
            for row in beside.iter_rows(named=True):
                rate = row["identityDisagreement"]
                attached.setdefault(row["tag"], []).append(
                    f"{row['identity']}={'' if rate is None else round(float(rate), 4)}"
                )
            for i in range(disagreement_at, len(rows)):
                feeds = attached.get(rows[i].entity)
                if feeds:
                    rows[i] = rows[i]._replace(detail=f"identitiesFed={'|'.join(feeds)}")

        tag_statuses = [r.status for r in rows[first:]]

        identity_first = len(rows)
        panel_identities = identities_of_panel[panel_id]
        for identity in sorted(panel_identities & set(identity_rate)):
            peers = [identity_rate[o] for o in panel_identities if o != identity and identity_rate.get(o) is not None]
            status = outlier_status(identity_rate[identity], peers)
            rows.append(
                _leaf("identity", identity, "identityDisagreement", identity_rate[identity], "", panel_id, status)
            )
        identity_statuses = [r.status for r in rows[identity_first:]]
        panel_coverage[panel_id] = roll_up_panel(tag_statuses, identity_statuses)

    for sample in samples:
        coverage = sample_coverage[sample]
        rows.append(QcRow("sample", sample, ROLLUP, None, "", "", coverage.status, coverage))
    for panel_id in sorted(panel_coverage):
        coverage = panel_coverage[panel_id]
        rows.append(QcRow("panel", panel_id, ROLLUP, None, "", panel_id, coverage.status, coverage))

    # The capture axis ships whether or not a capture assignment reached the
    # block: adding an axis to a released column changes its identity, adding
    # a value does not. With no assignment the single row reads *not
    # evaluated*, which is what it is -- nobody looked -- and never an absence.
    # With no assignment every sample belongs to one unnamed capture, rather
    # than to a capture with no members. Emptying the membership would make the
    # one level whose whole job is that nothing hides aggregate nothing: it
    # would read *not evaluated* over a run whose samples and panels were
    # measured perfectly well.
    captures: dict[str, list[str]] = {}
    for sample in samples:
        captures.setdefault(capture_of_sample.get(sample, "unassigned"), []).append(sample)
    for capture, its_samples in sorted(captures.items()):
        its_panels = sorted({panel_of_sample[s] for s in its_samples})
        from_samples = [sample_coverage[s] for s in its_samples]
        from_panels = [panel_coverage[p] for p in its_panels]
        worst = roll_up_capture([c.status for c in from_samples], [c.status for c in from_panels]).status
        coverage = _sum_coverage(worst, from_samples + from_panels)
        rows.append(QcRow("capture", capture, ROLLUP, None, "", "", worst, coverage))

    _write_sorted(_qc_frame(rows), f"{prefix}_qc.csv", ["level", "entity", "panelId", "measurement"])

    meta = {
        "referenceChoice": reference.served.value,
        "referenceSourceRequested": source.value,
        "cellListSource": cell_list_source,
        "cellsInList": len(cell_list) if cell_list is not None else None,
        "cellsAnalysed": len(analysed_cells),
        "floor": args.floor,
        "cutoff": args.cutoff,
        "minVoters": args.min_voters,
        "minAgreement": args.min_agreement,
        "gateThreshold": args.gate_threshold,
        "highReferenceLine": args.high_reference_line,
        "panelMinMembers": args.panel_min_members,
        "referenceThinLine": args.reference_thin_line,
        "roleColumn": args.role_column,
        "referenceValues": sorted(reference_values),
        "referenceTags": sorted(reference_tags),
        "grouping": grouping_rule or {"by": "tag"},
        "groupingId": grouping_id,
        # The narrowing a short panel file costs, carried in the output
        # rather than only in a log line: these tags were answered under a
        # grouping that could not place them.
        "tagsWithoutGroupingValue": sorted(ungrouped_tags),
        "contending": [sorted(group) for group in contending],
        "identityCount": len(universe),
        # The identities themselves, in the order the pivot lays them out. The workflow builds one
        # p-column per column of result_identity_summary.csv, and the column names are the identities --
        # which are panel data, unknown until this runs. A count cannot name them, so without this the
        # pivoted summary imports as nothing and the only per-antigen state a clonotype-anchored reader
        # can see disappears with no error.
        "identities": sorted(universe),
        "identitySummaryEmitted": summary_emitted,
        "identitySummaryLimit": IDENTITY_SUMMARY_MAX_IDENTITIES,
        "readingsFloored": readings_floored,
        "cellsEmptied": cells_emptied,
        "cellsHighReference": cells_high_reference,
        "cellsSetAside": len(gated),
        "panelLinesDropped": dropped_lines,
        "samples": samples,
        "setCount": len(cells_by_set),
    }
    with open(f"{prefix}_run_meta.json", "w") as out:
        json.dump(meta, out, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
