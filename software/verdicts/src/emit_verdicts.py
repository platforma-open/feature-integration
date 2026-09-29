"""The entrypoint: counts, a panel and a cell list become a four-state verdict.

Composes the reading in one order, and the order is load-bearing at every step. The floor
works on the raw per-(cell, tag) counts. A cell's reference reading is taken from the
floored frame. Tags combine into an identity by the highest of their counts. The identity's
count is read against that cell's own reference. A set's cells combine by majority.
Reversing any pair changes the answer.

**The grid of every cell against every identity is never built.** A silent cell scores
`specificity_score(0, r)`, at most ~0.0422 and falling as the reference rises, so it settles
*not bound* unless the cell itself cannot be compared. `silent_tally` counts those positions
analytically, because on a realistic panel the grid is 11-20x the sparse input and a pMHC
panel does not fit at all. Two consequences are enforced here: a `--cutoff` at or below that
~0.0422 bound is refused, and the row-per-position reference implementation is never called from
production -- it lives in the test package (test/dense_oracle.py), so this file cannot reach it.

`offered` is keyed by SAMPLE throughout and is never regrouped by set. Staining is done per
sample, so a set spanning two samples was offered whatever either panel offered, and
`combine_cells` takes that union itself. Keying the map by set instead makes every lookup
miss, reads every offered set as empty, and raises nothing.

One `Admissibility` bundle is built and handed to `read_states`, `combine_cells` and
`self_disagreement` alike, so they cannot be given different reference dicts and then
disagree about which cells "cannot be compared".

Every frame is sorted before it is written. `combine_tags_to_identities` groups without
maintaining order, so an unsorted frame varies run to run. A p-column's identity is its
content, and an unstable byte order costs every downstream node its dedup.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys

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
from frame_io import (
    UNDECLARED_BARCODES_KEPT,
    UndeclaredTally,
    _json_arg,
    _read_columns,
    _read_counts,
    _write_sorted,
    raw_feature_summary,
)
from identity_tables import (
    CELL_PUNCH_MAX_CELLS,
    IDENTITY_KEY_COLUMN,
    IDENTITY_SUMMARY_MAX_IDENTITIES,
    REFERENCE_IDENTITY_LABEL,
    SET_TAG_MEDIAN_MAX_TAGS,
    CellKey,
    _build_grouping,
    _cells_by_set,
    _declared_by_sample,
    _grouping_columns,
    _identity_labels,
    _identity_properties,
    _linker_frame,
    _panel_id,
    _pivot_cell_punch,
    _pivot_identity_summary,
    count_by_set,
    tag_labels,
)
from panel import (
    ANY_SAMPLE,
    consistent_properties,
    default_grouping,
    identity_universe,
    offered_identities,
    property_columns,
    read_panel,
)
from qc_measures import (
    DEFAULT_LINES,
    Line,
    bin_values,
    linear_bin_edges,
    log1p_bin_edges,
    log1p_edges_for,
    per_antigen_measures,
    per_tag_count_bins,
    reads_per_cell,
    sibling_disagreement,
    status_expr,
    usable_read_fraction,
    usable_reads,
)
from qc_rows import (
    _BACKGROUND_SCHEMA,
    _REAGENT_SCHEMA,
    _UNDECLARED_BARCODE_SCHEMA,
    QcRow,
    _add,
    _cells_set_aside,
    _median,
    _median_control_reading,
    _number,
    rescued_share,
    sample_report_rows,
    sample_summary_rows,
)
from tag_distribution import (
    DEFAULT_DISTRIBUTION_MIN_CELLS,
    TagFits,
    bound_at_count,
    fit_tag_probabilities_by_pair,
)
from verdict import (
    BOUND_CUTOFF,
    DEFAULT_FLOOR,
    DEFAULT_PANEL_MIN_MEMBERS,
    DISTRIBUTION_BOUND_PROBABILITY,
    Admissibility,
    Reference,
    ReferenceChoice,
    State,
    apply_floor,
    cell_admissibility_reason,
    cells_reading_nothing,
    combine_tags_to_identities,
    count_to_reach,
    gate_cells,
    read_states,
    reference_by_cell,
    specificity_score,
)


def _identity_probabilities(fits, grouping) -> dict[tuple[str, str, str], float]:
    """Per (sample, cell, identity), the highest probability among the identity's own tags.

    An identity's reading in a cell is the highest of its tags and never their sum, because tags
    differ in uptake and a sum would need the baseline scaled to match. The same rule applies to a
    probability: the identity is bound in that cell where any one of its tags says so.

    A (tag, sample) pair that established nothing contributes nothing, so an identity all of whose
    tags missed carries no key and reads *unreliable* rather than a low probability.
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

    Raw counts, never floored. The minimum acts on the identity's reading -- the numerator -- never
    on the comparator. The floored frame would make the panel rung's median a mixture of raw values
    and floored ones. The declared rung is unaffected either way.
    """
    return reference_by_cell(
        counts,
        reference_tags,
        source,
        cells=analysed_cells,
        panel_size=panel_size,
        min_members=args.panel_min_members,
    )


def _counted(n: int, noun: str) -> str:
    """`n` and its noun, pluralised. A generated label reading "1 tags" looks like a bug in the label."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


# A silent cell's count is zero, and a zero count's best possible score is specificity_score(0, 0).
# At or below it, the analytic silent count and the row-per-position reference part company over a
# silent admissible cell, quietly: one calls it bound, the other not bound, and nothing raises.
ANALYTIC_CUTOFF_BOUND = float(specificity_score(0, 0))


# Long on purpose and not decomposed. This is one composition taken in the one order the reading
# has, and splitting it into stages would put that order in the call sites rather than in the code
# a reader follows top to bottom.
def main() -> None:
    p = argparse.ArgumentParser(description="Read antigen counts into a four-state binding verdict per set.")
    p.add_argument(
        "counts_csv", help="sparse per-(sampleId, cellId, tag) UMI counts, with an optional totalWeight column"
    )
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
        # Derived from the enum, never restated. A hard-coded list lets the CLI reject a new rung
        # that every layer above it accepts.
        choices=[choice.value for choice in ReferenceChoice],
        help=(
            "which comparator to ask for; the run may serve 'none' instead, never a different one. "
            "Required: nothing here picks a rung for a scientist who did not"
        ),
    )
    p.add_argument(
        "--bound-probability",
        type=float,
        default=DISTRIBUTION_BOUND_PROBABILITY,
        help=(
            "how sure the fit must be before a cell counts as bound, as a probability that the cell's "
            f"count came from the signal component. The lowest allowed value is "
            f"{DISTRIBUTION_BOUND_PROBABILITY}, which is also the default. Lower is refused: a cell "
            "holding none of the tag could then cross the line, and this run counts those cells by "
            "arithmetic instead of checking each one, so the two halves would disagree"
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
    # Roughly what share of cells are expected to bind an antigen.
    p.add_argument("--initial-signal-weight", type=float, default=None)
    p.add_argument("--gate-threshold", type=int, default=None, help="set aside cells whose comparator reads above this")
    p.add_argument("--grouping", default=None, help="JSON: {'by':'tag'} or {'by':'property','column':...}")
    p.add_argument("--contending", default=None, help="JSON: groups of identities that contend, as a list of lists")
    # Accepted and not yet read. The capture rollup was its only reader, and only the sample carries
    # an aggregated status now. It stays declared because the capture axis ships on the QC columns for
    # the same reason: adding an axis to a released column changes that column's identity, where
    # adding a value does not.
    p.add_argument("--capture-map", default=None, help="JSON: sampleId -> captureId (accepted, not yet read)")
    p.add_argument(
        "--sample-labels",
        default=None,
        help="JSON: sampleId -> the label the panel file writes for it, when the two differ",
    )
    p.add_argument(
        "--qc-summary", default=None, help="per-sample read QC CSV (sampleId, readsTotal, readsMatched, ...)"
    )
    p.add_argument(
        "--raw-feature-counts",
        default=None,
        help="gathered pre-refine tag-stat -t FEATURE table (sampleId, FEATURE, totalWeight)",
    )
    # Every line, each with a shipped default. Restated here rather than left to
    # qc_measures.DEFAULT_LINES, so the value that scored a run is always on the command line. `error`
    # is omitted for readsPerCell: the field published one boundary, so depth warns and never alerts.
    default_lines = DEFAULT_LINES
    p.add_argument("--panel-assigned-warn", type=float, default=default_lines["panelAssignedFraction"].warn)
    p.add_argument("--panel-assigned-error", type=float, default=default_lines["panelAssignedFraction"].error)
    p.add_argument("--match-rate-warn", type=float, default=default_lines["matchedFraction"].warn)
    p.add_argument("--match-rate-error", type=float, default=default_lines["matchedFraction"].error)
    p.add_argument("--cell-barcode-quality-warn", type=float, default=default_lines["cellBarcodeValidFraction"].warn)
    p.add_argument("--cell-barcode-quality-error", type=float, default=default_lines["cellBarcodeValidFraction"].error)
    p.add_argument("--reads-per-cell-warn", type=float, default=default_lines["readsPerCell"].warn)
    p.add_argument("--aggregate-barcode-warn", type=float, default=default_lines["aggregateBarcodeFraction"].warn)
    p.add_argument("--aggregate-barcode-error", type=float, default=default_lines["aggregateBarcodeFraction"].error)
    p.add_argument("--undeclared-barcode-warn", type=float, default=default_lines["undeclaredBarcodeShare"].warn)
    p.add_argument("--undeclared-barcode-error", type=float, default=default_lines["undeclaredBarcodeShare"].error)
    p.add_argument("--usable-read-warn", type=float, default=default_lines["usableReadFraction"].warn)
    p.add_argument("--usable-read-error", type=float, default=default_lines["usableReadFraction"].error)
    p.add_argument("--rescued-share-warn", type=float, default=default_lines["refineRescuedShare"].warn)
    p.add_argument("--rescued-share-error", type=float, default=default_lines["refineRescuedShare"].error)
    p.add_argument("--vdj-antigen-count-warn", type=float, default=default_lines["uniqueCountsPerCell"].warn)
    p.add_argument("--vdj-antigen-count-error", type=float, default=default_lines["uniqueCountsPerCell"].error)
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    # Every line an operator may move, none invented, and EVERY key of `DEFAULT_LINES`. `status_for`
    # answers None for a measurement absent from this dict, so a line declared there and missing here
    # carries no status, whatever DEFAULT_LINES says elsewhere.
    lines: dict[str, Line] = {
        "panelAssignedFraction": Line(warn=args.panel_assigned_warn, error=args.panel_assigned_error),
        "matchedFraction": Line(warn=args.match_rate_warn, error=args.match_rate_error),
        "cellBarcodeValidFraction": Line(warn=args.cell_barcode_quality_warn, error=args.cell_barcode_quality_error),
        "readsPerCell": Line(warn=args.reads_per_cell_warn),
        "aggregateBarcodeFraction": Line(warn=args.aggregate_barcode_warn, error=args.aggregate_barcode_error),
        "undeclaredBarcodeShare": Line(warn=args.undeclared_barcode_warn, error=args.undeclared_barcode_error),
        "usableReadFraction": Line(warn=args.usable_read_warn, error=args.usable_read_error),
        "refineRescuedShare": Line(warn=args.rescued_share_warn, error=args.rescued_share_error),
        "uniqueCountsPerCell": Line(warn=args.vdj_antigen_count_warn, error=args.vdj_antigen_count_error),
    }
    add = functools.partial(_add, lines=lines)

    # Must be above 0 and below 1. At either end the split hands every cell to one side, and the fit
    # quietly falls back to splitting at the mean instead. The run would then report a value it never
    # used, so refuse rather than let that happen silently.
    # Absent is the ordinary case and means the pivot is derived from each tag's own counts. A value
    # given is a statement about the experiment, and only then does the range apply.
    if args.initial_signal_weight is not None and not 0.0 < args.initial_signal_weight < 1.0:
        raise SystemExit(
            f"the expected binder fraction is a share strictly between 0 and 1. Got {args.initial_signal_weight}."
        )
    if args.bound_probability < DISTRIBUTION_BOUND_PROBABILITY:
        raise SystemExit(
            f"--bound-probability must be at least {DISTRIBUTION_BOUND_PROBABILITY}. Below that a cell "
            f"holding none of a tag could be called bound. Most cells hold none of most tags, and the run "
            f"counts them by arithmetic rather than checking each one, so those cells would be counted "
            f"not-bound in one place and called bound in another. Got {args.bound_probability}."
        )
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

    # The panel file names samples the way a scientist does, "donor01", while the counts, the linker
    # and every axis this run emits are keyed by the platform's sampleId. Unbridged, `offered` ends up
    # keyed by labels, no sample that exists is offered anything, and every verdict comes back *never
    # asked*, which raises nothing. Translation happens HERE, once, before the panel is used for
    # anything. A value the map does not mention is left alone rather than dropped, because a panel row
    # naming a sample this run does not have is a real mismatch.
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
    # Kept, not merely reported: the values a tag disagreed about are what a fallback identity is
    # labelled with, and `properties` holds only what a tag agreed on.
    disagreed_by_column: dict[str, dict[str, list[str]]] = {}
    for tag, column, values in inconsistent:
        disagreed_by_column.setdefault(column, {})[tag] = sorted(values)
    for tag, column, values in inconsistent:
        print(
            f"[emit-verdicts] tag {tag!r} declares {column!r} as {values}; it carries no agreed value", file=sys.stderr
        )

    # The reference designation is read through `consistent_properties`, which strips the value and
    # drops any property a tag's rows disagree about. A per-sample comparator designation is therefore
    # discarded rather than honoured.
    reference_values = {v.strip() for v in args.reference_values.split(",") if v.strip()}
    reference_tags: set[str] = set()
    # The column is checked whenever one is named, never only when values are named with it. Gating
    # the check on `reference_values` leaves the worse half silent: a role column the panel does not
    # declare designates no tag, and the baseline falls back to the panel's own readings without a
    # word.
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

    # Validated as a list of lists before it is read as one. A flat `["AgA","AgB"]` is valid JSON, and
    # `set("AgA")` is a set of CHARACTERS, so the run completes, no competitor note fires, every
    # `wasCompeted` reads false, and the run record states a contention that was never tested.
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
    # separates a cell from a droplet that held none. `--cells` wins over the linker where both arrive,
    # because a list from gene expression covers cells whose receptor never assembled.
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
        # No list arrived, and one is NOT derived from the counts. In droplet data the observed
        # barcodes outnumber the cells by one to two orders of magnitude, because ambient material
        # lands on most barcodes. Standing them in would be worse than approximate, since
        # `readsPerCell` divides by this and a healthy library would read undersequenced and alert.
        # Every barcode is still analysed and every count still emitted. What is withheld is the claim
        # that these barcodes are cells: `inCellList` is unknown rather than true, and the measurements
        # needing a cell list read *not evaluated*.
        cell_list = None
        cell_list_source = "none"

    # `cell_list is None` means no list arrived, which differs from a list that arrived empty: the
    # first cannot answer "is this barcode a cell", the second answers "no". `listed` collapses both
    # for the set arithmetic below.
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

    # The floor is applied per sample, so the counters it returns land in each sample's own QC row. A
    # cell key carries its sample, so partitioning is exact on both counters and the run totals are
    # their sums.
    # Partitioned once. A filter per sample is a full scan of the whole run's counts per sample, and
    # this loop and the QC loop below each ran one.
    counts_by_sample: dict[str, pl.DataFrame] = {
        key[0]: part for key, part in counts.partition_by("sampleId", as_dict=True).items()
    }
    empty_counts = counts.head(0)
    floor_stats: dict[str, dict[str, int]] = {}
    parts = []
    for sample in samples:
        floored_part = apply_floor(
            counts_by_sample.get(sample, empty_counts),
            args.floor,
            reference_tags,
        )
        parts.append(floored_part.counts)
        floor_stats[sample] = floored_part.stats
    floored = pl.concat(parts) if parts else counts
    readings_floored = sum(s["readingsFloored"] for s in floor_stats.values())
    cells_emptied = sum(s["cellsEmptied"] for s in floor_stats.values())

    # One panel size, read once and passed to both. Deriving it separately for the default choice and
    # for the resolution would let the two disagree about whether the panel is large enough to serve
    # as its own comparator.
    panel_size = int(panel["tag"].n_unique())

    # No default and no derivation. The rung is the scientist's choice, and a run that carried none is
    # a configuration error rather than a run to guess at. argparse refuses it above.
    source = ReferenceChoice[args.reference_source.upper()]
    tag_fits: TagFits | None = None
    # Set only by the one rung whose conditions the settings cannot answer. The other two refuse in
    # `served_source` before any of this runs.
    no_baseline_reason: str | None = None
    if source is ReferenceChoice.DISTRIBUTION:
        # Keyed by (sample, identity) and never by cell: this rung fits one distribution per tag
        # across a sample's cells, so its answer is the same number for every cell of a sample and a
        # different one for every identity. `reference_by_cell` has nothing to return for it.
        #
        # Fitted over the RAW counts of the CELL LIST. Observed barcodes are not cells and outnumber
        # them by one to two orders of magnitude, and a background fitted over them is fitted over
        # ambient droplets.
        #
        # EVERY listed cell, including the ones `gate_cells` will set aside below, which is why the fit
        # runs first. The gate must not narrow this population, and it does not widen past the returned
        # cells.
        #
        # A run with no cell list keeps the barcode union. Membership is unknown rather than false
        # there, and `cellListSource` in the run record says which case the verdicts were read under.
        fit_universe = sorted(cell_list) if cell_list is not None else analysed_cells
        tag_fits = fit_tag_probabilities_by_pair(
            counts,
            fit_universe,
            panel,
            args.distribution_min_cells,
            floor=args.floor,
            reference_tags=reference_tags,
            initial_signal_weight=args.initial_signal_weight,
        )
        probabilities = _identity_probabilities(tag_fits, grouping)
        # A run where no tag fitted anywhere established no baseline. This is the one refusal that
        # cannot be caught from the settings: whether a sample holds three hundred cells whose counts
        # admit a two-component fit is a property of the data. So the run FINISHES, says so, and draws
        # no punchcard.
        # The gate is not the comparator. A declared baseline tag has two
        # roles apart: comparator always, admissibility gate only where a threshold is declared. Which
        # rung supplies the comparator does not reach the gate, so the declared readings are built here
        # too wherever the panel carries them. Without them a stored gate goes silently inert the moment
        # a scientist switches the baseline source, and the sticky measurement loses the population it
        # is taken over.
        #
        # `served` stays DISTRIBUTION. These readings gate and are reported; no verdict is read against
        # them, which is why `_comparator` returns null for every position on this rung.
        reference = Reference(
            reference_by_cell(counts, reference_tags, ReferenceChoice.DECLARED, cells=analysed_cells).by_cell
            if reference_tags
            else {},
            ReferenceChoice.DISTRIBUTION,
        )
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
    if not reference.by_cell:
        # No cell carries a baseline reading, so there is no population to count high ones in. The
        # condition is the readings and not the rung: a panel declaring a baseline tag gates under every
        # rung, and one declaring none gates under no rung. None, never 0 -- a zero would report a run
        # with no high background rather than one where the question does not arise.
        cells_high_reference = None

    # Built once and handed to every consumer. Two bundles built from two reference dicts do not
    # raise. They disagree about which cells cannot be compared, and the silent-position count comes
    # out wrong or negative.
    admissibility = Admissibility(reference.by_cell, gated, by_identity, probabilities)

    non_reference = floored.filter(~pl.col("tag").is_in(list(reference_tags))) if reference_tags else floored
    identities = combine_tags_to_identities(non_reference, grouping)
    states = read_states(identities, admissibility, args.cutoff, args.bound_probability)

    # The per-tag reading is diagnostic only: it compares each tag against the reference separately,
    # and no verdict is built from it. The measurement set carries it at both levels always, so where
    # the chosen grouping is not the per-tag one it is read a second time.
    if grouping == by_tag_grouping:
        tag_states = states
    else:
        # A second bundle, because the per-tag read asks about different identities. Where the
        # comparator is keyed by identity, the bundle built for the chosen grouping answers about
        # identities this read never mentions.
        tag_admissibility = (
            Admissibility(reference.by_cell, gated, None, tag_probabilities)
            if (by_identity is not None or tag_probabilities is not None)
            else admissibility
        )
        tag_states = read_states(
            combine_tags_to_identities(non_reference, by_tag_grouping),
            tag_admissibility,
            args.cutoff,
            args.bound_probability,
        )

    # Which (sample, tag) pairs the reads actually carry, from the RAW counts. Never from `floored`: a
    # count the minimum zeroed is a reading that happened and failed, and settles *not bound*, while a
    # tag with no reads at all is a question nobody put. Reading the floored frame here would turn a
    # dead reagent into a confident clean negative on every clonotype in the run.
    seen_pairs = {
        (row["sampleId"], row["tag"]) for row in counts.select("sampleId", "tag").unique().iter_rows(named=True)
    }
    offered_by_sample = {s: offered_identities(panel, grouping, [s], seen_pairs) for s in samples}
    tag_offered_by_sample = {s: offered_identities(panel, by_tag_grouping, [s], seen_pairs) for s in samples}

    def _answers(frame: pl.DataFrame) -> pl.DataFrame:
        """The frame, or its headers alone where the run established no baseline.

        A run with no baseline read no verdicts, so the frames carrying answers carry no rows. They
        keep their schemas, because every reader still needs to find its columns, and a missing file
        reads as a stage that crashed rather than one that finished and said why.

        Emitting the answers instead would fill every position with *unreliable*: honest and useless.

        The STRUCTURAL frames are written in full either way -- which tags feed which identity, what
        each sample was offered, the panel and identity labels. A reader working out why no baseline
        could be established needs them.
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
    # The set's own cell count, joined on rather than computed inside `set_counts`, which is a pure
    # reading of the verdicts frame at its (setId, identity) grain where a cell count does not live. It
    # is the set's cells, NOT its answering cells: that number varies by identity and travels with the
    # verdict as support.
    per_set_cells = pl.DataFrame(
        [(set_id, len(cells)) for set_id, cells in sorted(cells_by_set.items())],
        orient="row",
        schema={"setId": pl.String, "cellCount": pl.Int64},
    )
    # Set-aside cells PER CLONOTYPE, never per run. The run-level total in the run meta answers a
    # different question that cannot be split back apart. `gated` holds (sampleId, cellId) keys and
    # `cells_by_set` maps a set to its members.
    per_set_gated = pl.DataFrame(
        list(count_by_set(cells_by_set, gated).items()),
        orient="row",
        schema={"setId": pl.String, "cellsSetAside": pl.Int64},
    )
    # Cells that read nothing at all, PER CLONOTYPE. Carried beside the clonotype's cell count rather
    # than at every identity, because a cell with nothing left is empty at every identity and repeating
    # the subtraction per position would report a per-identity failure that did not happen. It changes
    # no verdict.
    per_set_empty = pl.DataFrame(
        list(count_by_set(cells_by_set, cells_reading_nothing(floored, linker_cells)).items()),
        orient="row",
        schema={"setId": pl.String, "cellsReadingNothing": pl.Int64},
    )
    counts_frame = (
        set_counts(verdicts)
        .join(per_set_cells, on="setId", how="left")
        # Filled rather than asserted, unlike cellCount below: with no gate declared `gated` is empty,
        # so every set legitimately has nothing set aside and 0 is the true answer.
        .join(per_set_gated, on="setId", how="left")
        # Filled for the same reason, and it bites harder here: this column ships off by default, so
        # the reader who turns it on is the one asking the question, and a null would answer it with a
        # blank where zero is the truth.
        .join(per_set_empty, on="setId", how="left")
        .with_columns(pl.col("cellsSetAside").fill_null(0), pl.col("cellsReadingNothing").fill_null(0))
    )
    # Every set comes FROM the linker, so every set has cells. Asserted rather than filled with zero: a
    # set with no cells is a contradiction, and writing 0 would report it as a real, empty clonotype.
    missing = counts_frame.filter(pl.col("cellCount").is_null())["setId"].to_list()
    if missing:
        raise SystemExit(f"sets carry verdicts but no cells, which cannot happen: {missing[:8]}")
    _write_sorted(_answers(counts_frame), f"{prefix}_set_counts.csv", ["setId"])

    # --- Median antigen depth per (clonotype, tag): result_set_tag_median.csv ------------------
    #
    # One row per clonotype, one COLUMN per tag.
    # The population for (set, tag) is the clonotype's cells whose SAMPLE was offered that tag.
    # Raw counts, never floored.
    #
    # The following section is meant to compute the median without building the dense grid

    # Build the (sample, tag) pairs each sample's panel actually offered:
    offered_tag_pairs = pl.DataFrame(
        [(sample, tag) for sample, tags in sorted(tag_offered_by_sample.items()) for tag in sorted(tags)],
        orient="row",
        schema={"sampleId": pl.String, "tag": pl.String},
    )
    # Which cell is in which clonotype
    set_of_cell = pl.DataFrame(
        [(set_id, sample, cell) for set_id, cells in sorted(cells_by_set.items()) for sample, cell in cells],
        orient="row",
        schema={"setId": pl.String, "sampleId": pl.String, "cellId": pl.String},
    )
    # Count the non-zero clonotype's cells, but only in samples where the barcode was found ("nz").
    nonzero_by_set_tag = (
        # Glue each cell to its reads. Keep only the ones present in a clonotype.
        set_of_cell.join(
            counts.select(["sampleId", "cellId", "tag", "umiCount"]), on=["sampleId", "cellId"], how="inner"
        )
        # Only keep cells whose (sample, tag) was already offered in offered_tag_pairs
        .join(offered_tag_pairs, on=["sampleId", "tag"], how="inner")
        # Then pile the numbers up per (set, tag) and count how many we get per pile (nz)
        # With this we filter-out (set, tag) combinations with zero presence
        .group_by(["setId", "tag"])
        .agg(pl.col("umiCount").alias("values"), pl.len().alias("nz"))
    )
    # Count the clonotype's cells, but only in samples where the barcode was found ("n").
    # Cells are counted per (set, sample) first, so this never expands to a row per cell per tag.
    cells_per_set_sample = set_of_cell.group_by(["setId", "sampleId"]).agg(pl.len().alias("cells"))
    population_by_set_tag = (
        nonzero_by_set_tag.select(["setId", "tag"])
        #  for each pair, list every sample the clonotype has cells in:
        .join(cells_per_set_sample, on="setId", how="inner")
        # Drop samples that where not offered in offered_tag_pairs
        .join(offered_tag_pairs, on=["sampleId", "tag"], how="inner")
        .group_by(["setId", "tag"])
        .agg(pl.col("cells").sum().alias("n"))
    )
    # Check the pairs whose median can clear zero
    set_tag_median = (
        nonzero_by_set_tag.join(population_by_set_tag, on=["setId", "tag"], how="inner")
        # If non-zero cells (nz) * 2 >= number of cells (n), then the median wont be zero
        .filter(pl.col("nz") * 2 >= pl.col("n"))
        .with_columns(pl.col("values").list.concat(pl.lit(0, dtype=pl.Int64).repeat_by(pl.col("n") - pl.col("nz"))))
        .select("setId", "tag", pl.col("values").list.median().alias("medianUmiCount"))
    )
    # Take the tag column list from the panel
    set_tag_median_tags = sorted({tag for tag, _sample in by_tag_grouping})
    # One p-column per tag as long as we don't have too many (SET_TAG_MEDIAN_MAX_TAGS)
    if len(set_tag_median_tags) > SET_TAG_MEDIAN_MAX_TAGS:
        set_tag_median_tags = []
    if set_tag_median_tags:
        pivoted = set_tag_median.pivot(on="tag", index="setId", values="medianUmiCount")
        # A tag no clonotype cleared zero for is missing from the pivot. Add it back as a column of
        # nulls, so the header carries every declared tag.
        set_tag_median_frame = pivoted.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(tag) for tag in set_tag_median_tags if tag not in pivoted.columns]
        ).select(["setId", *set_tag_median_tags])
    else:
        # Headers only. A key-only frame imports as no columns, where a missing file would fail the import.
        set_tag_median_frame = pl.DataFrame(schema={"setId": pl.String})
    # Store (set, tag) median values
    _write_sorted(set_tag_median_frame, f"{prefix}_set_tag_median.csv", ["setId"])

    summary, punch, summary_emitted = _pivot_identity_summary(verdicts, universe)
    _write_sorted(_answers(summary), f"{prefix}_identity_summary.csv", ["setId"])
    _write_sorted(_answers(punch), f"{prefix}_identity_punch.csv", ["setId"])

    cell_punch, cell_punch_emitted = _pivot_cell_punch(states, cells_by_set, offered_by_sample, admissibility, universe)
    _write_sorted(_answers(cell_punch), f"{prefix}_cell_punch.csv", ["setId", "sampleId", "cellId"])

    # The sparse per-tag counts and the per-cell scalars together carry every per-cell state, at a
    # small fraction of the dense grid's size. They stay inside the block: reading the same experiment
    # under another grouping is another execution rather than a re-derivation a reader performs.
    #
    # `_pivot_cell_punch` above does export the dense grid, because a readout showing one clonotype's
    # cells against the panel cannot be assembled from a sparse frame by a grid. What survives is the
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

    def _listed(frame: pl.DataFrame) -> pl.DataFrame:
        """`frame` narrowed to the cell list, or unchanged where no list arrived.

        Every figure a reader takes as a per-CELL number goes through here: the reagent table's counts
        and medians, and the score and reference spreads. Observed barcodes outnumber cells by one to
        two orders of magnitude, so a figure taken over them is the ambient population's.

        A run with no cell list keeps every barcode. Membership is unknown rather than false there, and
        `cellListSource` in the run record says which case a figure was computed under.
        """
        return frame if cell_list is None else frame.join(in_list, on=["sampleId", "cellId"], how="inner")

    # The scored readings, narrowed once.
    scored = (
        _listed(states.filter(pl.col("unreliableReason").is_null()))
        if reference.served is ReferenceChoice.DECLARED
        else states.head(0)
    )
    # Per sample, the three figures a reader needs to tell a shallow sample from a quiet one: how much
    # antigen its typical reading carries, what the cutoff asks of a typical cell there IN UMI, and what
    # share of its readings the cutoff actually called bound.
    score_by_sample: dict[str, dict[str, object]] = {}
    if scored.height > 0:
        for (sample_id,), frame in scored.group_by(["sampleId"]):
            antigen = frame["umiCount"].to_numpy()
            median_baseline = float(
                np.median(np.nan_to_num(frame["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0))
            )
            bound = int((frame["state"] == State.BOUND.value).sum())
            score_by_sample[str(sample_id)] = {
                # Per READING, not per cell: a cell asked about four antigens carries four of them, and a
                # reading is what the cutoff acts on.
                "medianAntigenReading": float(np.median(antigen)),
                # A cell's own baseline sets its own boundary; this is the boundary of the MIDDLE cell,
                # not one any single cell was held to. None where no count reaches the cutoff.
                "cutoffCountNeeded": count_to_reach(args.cutoff, median_baseline),
                "boundReadingShare": bound / frame.height if frame.height else None,
            }

    def _admissibility(key: CellKey) -> str:
        reason = cell_admissibility_reason(key, admissibility)
        return "admissible" if reason is None else reason.value

    # Admissibility is built HERE, in the same row as its own cell, and not attached to a later frame
    # as a positional column. Polars does not promise a left frame's row order survives a join
    # (`maintain_order` defaults to "none"), so a positional attach after the joins below can give cells
    # each other's labels -- and `_write_sorted` then sorts the file, which hides it rather than
    # repairing it.
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

    # The same (cell, tag) counts as the table above, but taken before the floor and including the
    # control tag.
    raw_cell_counts = _listed(counts).select(["sampleId", "cellId", "tag", "umiCount"])
    _write_sorted(raw_cell_counts, f"{prefix}_cell_raw_counts.csv", ["sampleId", "cellId", "tag"])

    # The same raw counts pivoted to ONE COLUMN PER TAG, keyed (sampleId, cellId).
    cell_tag_pivot = raw_cell_counts.pivot(on="tag", index=["sampleId", "cellId"], values="umiCount")
    _write_sorted(cell_tag_pivot, f"{prefix}_cell_tag_pivot.csv", ["sampleId", "cellId"])
    cell_tag_pivot_tags = sorted(c for c in cell_tag_pivot.columns if c not in ("sampleId", "cellId"))

    cell_scalars = (
        reference_frame.join(in_list, on=["sampleId", "cellId"], how="left")
        .with_columns(pl.col("inCellList").fill_null(unlisted_reads))
        .select(["sampleId", "cellId", "referenceCount", "admissibility", "inCellList"])
    )
    _write_sorted(_answers(cell_scalars), f"{prefix}_cell_scalars.csv", ["sampleId", "cellId"])

    # The cell list ITSELF, as its own frame: a row per cell the V(D)J data matched and none for any other
    # barcode.
    _write_sorted(in_list, f"{prefix}_cell_in_list.csv", ["sampleId", "cellId"])

    # Both frames are pure key sets -- what a sample was offered, and which identity a tag feeds -- and
    # each carries a constant value column so it can become a p-column at all. A frame of key columns
    # alone imports as nothing: columns are built from value columns.
    offered_frame = pl.DataFrame(
        [(sample, identity, "true") for sample in samples for identity in sorted(offered_by_sample[sample])],
        orient="row",
        schema={"sampleId": pl.String, "identity": pl.String, "offered": pl.String},
    )
    _write_sorted(offered_frame, f"{prefix}_offered.csv", ["sampleId", "identity"])

    # The value column is named "1" and holds 1, matching the cell-linker convention already used for
    # linker columns elsewhere in the platform.
    #
    # Deliberately NOT keyed by sample. The reason is the join, not the declaration. This linker puts a
    # tag-keyed figure beside an identity-keyed verdict, and neither side carries a sample: verdicts are
    # (set, identity) over clonotypes that span samples, and the per-tag figures are run-level. A sample
    # axis here is an axis no participating table has -- it makes the join malformed, and
    # `createPlDataTableV3` label discovery then rejects the spec frame.
    #
    # Under (tag, sample) grouping one tag can feed several identities, so this frame is many-to-many
    # with one row per pair. Distinct rows matter: duplicate axis keys break a grid silently.
    linker_frame = _linker_frame(grouping)
    _write_sorted(linker_frame, f"{prefix}_tag_identity.csv", ["tag", "identity"])

    # Only disagreements in the column that SUPPLIES the label matter: a tag that disagrees about some
    # other property still carries an ordinary name. Which column that is depends on the rule -- a
    # property grouping labels by the value it grouped on, while the per-tag grouping borrows the
    # feature name. Passing the grouping column either way made every per-tag run look up "", so a
    # barcode two samples named differently fell through to its raw 15-mer.
    #
    # Under a property grouping on ONE column, that column supplies the rescue. Under several there is
    # no single such column, so the feature column supplies it.
    grouping_columns = (
        _grouping_columns(grouping_rule, property_columns(panel))
        if (isinstance(grouping_rule, dict) and grouping_rule.get("by") == "property")
        else []
    )
    label_column = grouping_columns[0] if len(grouping_columns) == 1 else args.feature_col
    # Bound once and passed to both readers below. `_identity_labels` joins these names into the label
    # a reader sees. The run record carries the same names apart so the readout can say WHY a label is
    # joined. Deriving the second from the first -- splitting the label back on " / " -- would guess
    # wrong for a reagent whose own name contains a slash.
    label_disagreements = disagreed_by_column.get(label_column or "", {})
    labels = _identity_labels(
        grouping,
        properties,
        args.feature_col,
        grouping_id,
        label_disagreements,
    )
    # Reference tags carry a row too. They are held out of `universe`, but the reagent table gives a
    # tag absent from the grouping a row under its own barcode, so without a label here that row
    # renders a blank where every other row names an antigen. The label is the ROLE, not the tag's
    # feature name, which the same row already carries in its Tag column, and not a grouping value,
    # which would read as a fourth identity beside an identity count of three.
    #
    # `universe` and `reference_tags` are disjoint: `_build_grouping` excludes reference tags.
    identity_labels = pl.DataFrame(
        [(identity, labels.get(identity, identity)) for identity in sorted(universe)]
        + [(tag, REFERENCE_IDENTITY_LABEL) for tag in sorted(reference_tags)],
        orient="row",
        schema={"identity": pl.String, "label": pl.String},
    )
    _write_sorted(identity_labels, f"{prefix}_identity_labels.csv", ["identity"])

    # A readable name per TAG, for the surfaces keyed by tag rather than by identity. The reagent
    # table's leading column is this name, and without it that column is a barcode sequence.
    #
    # Keyed by tag, not by identity. Under a property grouping an identity is a group of tags and its
    # label is the group's name, so borrowing it would put the group's name on every member. Reference
    # tags included: they hold reagent figures too.
    #
    # The FEATURE column's disagreements, never `label_disagreements`. That dict is scoped to whatever
    # column labels an IDENTITY. A tag's name comes from the feature column under every grouping.
    tag_names = tag_labels(
        set(panel["tag"].to_list()),
        properties,
        args.feature_col,
        disagreed_by_column.get(args.feature_col, {}),
    )
    _write_sorted(
        pl.DataFrame(
            [(tag, tag_names[tag]) for tag in sorted(tag_names)],
            orient="row",
            schema={"tag": pl.String, "label": pl.String},
        ),
        f"{prefix}_tag_labels.csv",
        ["tag"],
    )

    # The declarations, keyed the same way the verdicts are. Wide -- one column per property -- because
    # the workflow turns each into its own p-column with the property name in the DOMAIN, which is what
    # makes two properties two distinct columns.
    #
    # A property no identity agreed on is left out rather than exported empty: an all-blank filterable
    # column offers a reader a filter with nothing to filter by. The surviving names are recorded in the
    # run meta, because the headers are panel data, unknown until this runs.
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

    # No panel file names its panel, so a reader is given a short generated name plus the two figures that
    # tell two panels apart at a glance.
    # The number is POSITIONAL, following the panel-id sort the frame is written in, so P-1 is the first row
    # a reader meets.
    panel_order = sorted(tags_of_panel)
    panel_label_of = {
        panel_id: "P-{} ({}, {})".format(
            position,
            _counted(len(tags_of_panel[panel_id]), "tag"),
            _counted(len(samples_of_panel[panel_id]), "sample"),
        )
        for position, panel_id in enumerate(panel_order, start=1)
    }
    panel_labels = pl.DataFrame(
        [(panel_id, panel_label_of[panel_id]) for panel_id in panel_order],
        orient="row",
        schema={"panelId": pl.String, "label": pl.String},
    )
    _write_sorted(panel_labels, f"{prefix}_panel_labels.csv", ["panelId"])

    # The panel a sample was stained with, carrying the LABEL rather than the id.
    sample_panel = pl.DataFrame(
        [(sample, panel_label_of[panel_of_sample[sample]]) for sample in samples],
        orient="row",
        schema={"sampleId": pl.String, "panelLabel": pl.String},
    )
    _write_sorted(sample_panel, f"{prefix}_sample_panel.csv", ["sampleId"])

    # ---- the quality measurements -------------------------------------------------

    def _disagreement_rates(samples_here: list[str]) -> dict[str, float | None]:
        """The per-tag self-disagreement rate over one panel's samples only.

        Scoped per panel rather than over the run, because the row carrying this figure is keyed
        `(tag, panelId)`. A reagent declared in panels P and Q but misbehaving only in Q's samples
        would otherwise show the same inflated rate on P's row.

        Measured at the tag and nowhere else. The identity-level figure has nothing to compare
        against, so it cannot separate a faulty reagent from a panel full of weak binders.

        The cell sets are restricted too, not only the states. A set spanning two panels' samples
        would otherwise bring its other panel's cells into this panel's evaluable count.
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

    # Why a read-level figure has no number, per source. The summary is one row per sample built by
    # `qc_report.py`: it leaves `panelAssignedFraction` and `cellBarcodeValidFraction` blank where the
    # refine-tags report is absent or unreadable, carries no step for that tag, or the step read no
    # input. `readsTotal` comes from the parse report and is blank only when no summary reached this run.
    NO_READ_QC = "no read QC summary row reached this sample"
    NO_REFINE_STEP = "no refine-tags report was produced, or it supplied no %s step with input reads"
    NO_READS_TO_DIVIDE = "this sample's read QC reports no reads, so the share has no denominator"
    NO_AGGREGATE_FIGURE = "this sample's read QC reports nonzero reads but no aggregate-barcode figure"
    NO_READ_COUNT = "this sample's read QC row carries no read count, so the share has no denominator"
    NO_MATCHED_COUNT = "this sample's read QC row carries no matched-read count, so the share has no numerator"
    # Both sides of the subtraction, since either can be the missing one and the reader cannot tell
    # which from the row.
    NO_RESCUED_FIGURE = (
        "this sample has no pre-refine pass, no panel-assigned fraction, or the two disagree, so what "
        "correction rescued cannot be read"
    )

    # The pre-refine pass: one FEATURE tag-stat row per sequence the reads carried, before refine-tags
    # snaps each one onto the panel. Without this file the table below stays the ordinary empty case
    # rather than raising, because a run wired without it has not checked for an undeclared barcode,
    # which is a different fact from having checked and found none.
    raw_tallies = raw_feature_summary(args.raw_feature_counts, declared) if args.raw_feature_counts else None
    undeclared_barcode_frames: list[pl.DataFrame] = []

    # `totalWeight` reaches `counts` only from a gather step built after this column existed. Checked
    # once, not per sample: its presence is a property of the file, not of any one sample's rows.
    has_total_weight = "totalWeight" in counts.columns

    # Bucketed once, in one sorted pass. Sorting the whole cell list inside the loop below is
    # O(samples x cells log cells) over a set that reaches millions of cells on a real run.
    listed_by_sample: dict[str, list[CellKey]] = {}
    for key in sorted(listed):
        listed_by_sample.setdefault(key[0], []).append(key)

    # No control tag, no control checks. Both control-tag rows are DROPPED rather than shown with a
    # reason: with no such tag there is no such thing as a control reading, so the question was never
    # put, and a row carrying a reason claims we looked. Reachable from the app on exactly one setting --
    # the tag-distribution baseline, under which the model sends no role column at all.
    #
    # The reason below is the remaining case: a control tag EXISTS and this particular sample has no
    # listed cell carrying a reading of it. That one was asked and cannot be answered, so it keeps a row.
    control_rows = frozenset() if reference_tags else frozenset({"cellsSetAside", "medianControlReading"})
    no_control = "no cell of this sample carries a control reading"

    rows: list[QcRow] = []
    sample_report: dict[str, dict] = {}
    for sample in samples:
        first = len(rows)
        sample_counts = counts_by_sample.get(sample, empty_counts)
        listed_here = listed_by_sample.get(sample, []) if cell_list is not None else None
        qc = read_qc.get(sample, {})

        reads_matched = _number(qc, "readsMatched")
        reads_total_for_match = _number(qc, "readsTotal")
        # Neither COUNT takes a row of its own. Both ride here as this share's two numbers, the way the
        # usable-read share carries its own -- a row saying only how many reads survived is a denominator
        # in search of a finding. Both still reach the across-samples table.
        # DERIVED from the two counts rather than read from `qc_report.py`'s own `matchedFraction`
        # column. The ratio is those two numbers and nothing else, so computing it here means a summary
        # carrying both counts but not their ratio still reports the share -- reading the column made
        # that case come back as "no denominator" while both inputs sat in the row. The column keeps its
        # place on the across-samples table; this is the one source the reading uses.
        match_share = (
            None if reads_matched is None or not reads_total_for_match else reads_matched / reads_total_for_match
        )
        match_detail = (
            "" if match_share is None else f"{int(reads_matched):,} out of {int(reads_total_for_match):,} reads parsed"
        )
        # Four ways to have no number, and they send a reader to different places: no row at all, a row
        # reporting zero reads, a row with no read count, and a row with no matched count.
        match_reason = (
            NO_READ_QC
            if not qc
            else NO_READ_COUNT
            if reads_total_for_match is None
            else NO_READS_TO_DIVIDE
            if reads_total_for_match == 0
            else NO_MATCHED_COUNT
        )
        add(rows, "sample", sample, "matchedFraction", match_share, match_detail, reason=match_reason)
        # `qc_report.py` computes this from the tag-stat TSV directly, the same required input
        # `readsTotal` reads from the parse report -- a missing figure means no read-QC row reached this
        # sample at all.
        add(rows, "sample", sample, "cellsDetected", _number(qc, "cellsDetected"), reason=NO_READ_QC)
        add(
            rows,
            "sample",
            sample,
            "panelAssignedFraction",
            _number(qc, "panelAssignedFraction"),
            reason=NO_READ_QC if not qc else NO_REFINE_STEP % "FEATURE",
        )

        # `usable_read_fraction` takes the cell IDs alone: `sample_counts` is already scoped to this
        # sample, so the sampleId half of each `listed_here` key would only be checked against itself.
        cell_ids_here = [key[1] for key in listed_here] if listed_here is not None else None
        reads_total = _number(qc, "readsTotal")
        if has_total_weight:
            usable_value, usable_detail = usable_read_fraction(
                sample_counts, "cellId", cell_ids_here, int(reads_total) if reads_total is not None else None
            )
        else:
            usable_value, usable_detail = None, "the counts file carries no totalWeight column"
        # `usable_read_fraction` returns one string for both roles, and they are not the same role: a
        # detail rides alongside a number, a reason stands in place of one. Passing it as the detail only
        # when a number came back keeps QcRow's invariant.
        add(
            rows,
            "sample",
            sample,
            "usableReadFraction",
            usable_value,
            "" if usable_value is None else usable_detail,
            reason=usable_detail,
        )

        # Both counts, because the share alone hides the scale it was taken over. Blank rather than a
        # guess where either count is missing: the share can still arrive without them from a read-QC row
        # written before these two columns existed.
        cell_in = _number(qc, "cellBarcodeIn")
        cell_out = _number(qc, "cellBarcodeOut")
        cell_detail = (
            ""
            if cell_in is None or cell_out is None
            else f"{cell_in:,.0f} reads entered correction, {cell_out:,.0f} passed"
        )
        add(
            rows,
            "sample",
            sample,
            "cellBarcodeValidFraction",
            _number(qc, "cellBarcodeValidFraction"),
            cell_detail,
            reason=NO_READ_QC if not qc else NO_REFINE_STEP % "CELL",
        )

        # The undeclared-barcode table: keyed by sequence, never by (panel, tag), because an
        # undeclared barcode has no row in the panel to sit beside. Read on the PRE-refine pass, where
        # a sequence the panel never declared can still be seen -- `counts` above has already been
        # snapped onto the panel by refine-tags.
        if raw_tallies is not None:
            tally = raw_tallies.get(sample, UndeclaredTally())
            # Two shares at two levels. `barcodeShare` is the row's own weight over every pre-refine
            # read of this sample. `readShare` is the SAMPLE's, computed once over every row of that
            # sample -- kept or elided by the tally's cap -- and carried on every row written. The
            # status reads `barcodeShare`, so it differs down the table; `readShare` carries no status.
            # It is the barcode's and never the sample's, so it is written here rather than added to
            # `rows` / `sample_report_rows`.
            barcode_share = (
                (pl.col("totalWeight") / tally.total_weight) if tally.total_weight > 0 else pl.lit(None, pl.Float64)
            )
            # `status_expr`, not the scalar `status_for` in a loop: `heaviest`'s row cap is a parameter
            # that accepts None, so a loop here is a loop over every distinct pre-refine sequence, and
            # materialising the column to drive it undoes this stage's memory work. Both read the same
            # lines and the same directions.
            undeclared_barcode_frames.append(
                tally.heaviest.select(
                    pl.lit(sample, pl.String).alias("sampleId"),
                    "tag",
                    "totalWeight",
                    barcode_share.cast(pl.Float64).alias("barcodeShare"),
                    pl.lit(tally.share, pl.Float64).alias("readShare"),
                ).with_columns(status_expr("undeclaredBarcodeShare", pl.col("barcodeShare"), lines).alias("status"))
            )
            # What correction then recovered. `tally.share` counts every pre-refine read the panel does
            # not declare; `featureDroppedShare` counts the reads the antigen-barcode step could not place
            # on it. BOTH over matched reads, which is what makes the difference the reads a sequence off
            # the panel carried that correction snapped onto a panel entry -- the rows of this table that
            # cost the run nothing.
            #
            # NOT `1 - panelAssignedFraction`: that was this figure's subtrahend until the two shares were
            # found to have different denominators, since a refine-tags step's own input is the previous
            # step's survivors rather than the matched-read count.
            add(
                rows,
                "sample",
                sample,
                "refineRescuedShare",
                rescued_share(tally.share, _number(qc, "featureDroppedShare")),
                reason=NO_RESCUED_FIGURE,
            )
        # ONE POPULATION, top and bottom: the reads inside the listed cells, over those same cells. The
        # numerator is `usable_reads` -- the identical set the `usableReadFraction` row is a share of, so
        # the two rows cannot describe different reads. A numerator over every matched read instead would
        # track the V(D)J match rate rather than this library's depth; see `reads_per_cell`.
        #
        # Both inputs are the counts table and the cell list, NOT the read-QC row -- so a run with no
        # `--qc-summary` still reports depth, where before it could not.
        depth = (
            reads_per_cell(usable_reads(sample_counts, "cellId", cell_ids_here), len(listed_here))
            if has_total_weight and cell_ids_here is not None and listed_here is not None
            else None
        )
        # No detail line, unchanged. The pairing a reader wants -- usable reads against the sample's whole
        # read count -- is the `usableReadFraction` row's detail already.
        #
        # Three cases, and the third is new: the numerator now comes from the counts table, so a table
        # with no `totalWeight` column leaves it with no numerator, exactly as it does for the share.
        depth_reason = (
            "no cell list supplied, so depth has no denominator"
            if listed_here is None
            else "no cell of this sample is in the cell list, so depth has no denominator"
            if not listed_here
            else "the counts file carries no totalWeight column, so depth has no numerator"
        )
        add(rows, "sample", sample, "readsPerCell", depth, reason=depth_reason)

        # qc_report.py computes this from the tag-stat TSV and the parse report. It blanks the figure on
        # two distinct conditions: no read-QC row for this sample at all, and a row whose readsTotal is
        # zero, which leaves the fraction no denominator. The second is reachable through the empty-input
        # path in parse_gate.py. A row present with nonzero readsTotal but no figure is a third,
        # distinct condition.
        agg_fraction = _number(qc, "aggregateBarcodeFraction")
        agg_threshold = _number(qc, "aggregateBarcodeThreshold")
        # The threshold alone. How many barcodes it flagged is the fraction beside it said a second way,
        # and a count of barcodes is not a quantity a reader of a read share can act on.
        agg_detail = (
            "" if agg_fraction is None or agg_threshold is None else f"Threshold set at {agg_threshold:,.0f} UMIs"
        )
        # `reads_total` is None where the row carries no readsTotal at all, which is neither of the two
        # cases below: it reports no read count rather than a count of zero.
        agg_reason = (
            NO_READ_QC
            if not qc
            else NO_READS_TO_DIVIDE
            if reads_total == 0
            else NO_AGGREGATE_FIGURE
            if reads_total is not None
            else NO_READ_COUNT
        )
        add(
            rows,
            "sample",
            sample,
            "aggregateBarcodeFraction",
            agg_fraction,
            agg_detail,
            reason=agg_reason,
        )

        stats = floor_stats.get(sample, {"readingsFloored": 0, "cellsEmptied": 0, "cellsWithAntigenReadings": 0})
        add(
            rows,
            "sample",
            sample,
            "floorRemoved",
            float(stats["readingsFloored"]),
            # The setting first, under the name the form gives it, so a reader who wants to move it knows
            # where to go. Then the cell count, as a share of the cells the minimum could have emptied --
            # cells carrying at least one antigen reading before it ran. NOT of `cellsDetected`, which
            # counts every barcode in the tag-stat table including ones whose only reading is the control
            # tag, and so would quietly widen the denominator.
            f"Min count is currently set to {args.floor:,} under Threshold Parameters"
            f"|Cell barcodes left with nothing above zero: {stats['cellsEmptied']:,}"
            f" out of {stats['cellsWithAntigenReadings']:,}",
        )

        listed_totals = (
            sample_counts.join(in_list, on=["sampleId", "cellId"], how="semi")
            .group_by("cellId")
            .agg(pl.col("umiCount").sum().alias("total"))["total"]
            .to_list()
        )
        add(
            rows,
            "sample",
            sample,
            "uniqueCountsPerCell",
            _median([float(v) for v in listed_totals]),
            f"Cell barcodes with a reading: {len(listed_totals):,}",
            # `in_list` is empty whenever no list arrived, so the join yields nothing for every sample of
            # such a run. Branching on the same fact `readsPerCell` branches on keeps the two rows from
            # giving one run two incompatible accounts.
            reason=(
                "no cell list supplied, so no cell of this sample is listed"
                if cell_list is None
                else "no listed cell in this sample holds a counted reading"
            ),
        )

        # Both control-tag rows, over the V(D)J-MATCHED cells of this sample. Narrowed like every other
        # per-cell figure on this page: `reference.by_cell` is zero-filled over every analysed barcode,
        # and in droplet data those outnumber cells by one to two orders of magnitude, so a median taken
        # over them is the ambient population's and reads zero on a sample with real sticky cells.
        listed_keys = set(listed_here) if listed_here is not None else None
        here = {
            key: value
            for key, value in reference.by_cell.items()
            if key[0] == sample and (listed_keys is None or key in listed_keys)
        }
        if not control_rows:
            add(rows, "sample", sample, "cellsSetAside", *_cells_set_aside(here, args.gate_threshold, no_control))
            add(rows, "sample", sample, "medianControlReading", *_median_control_reading(here, no_control))

        # What the cutoff asked of this sample and what it returned.
        # A sample that produced no score leaves them blank.
        stats = score_by_sample.get(sample, {})
        for measurement in ("medianAntigenReading", "cutoffCountNeeded", "boundReadingShare"):
            add(rows, "sample", sample, measurement, stats.get(measurement))

        # Every measurement here is the SAMPLE's, so every one of them rolls up. A reagent's condition
        # never reaches a sample's status: one bad reagent marking twenty samples is how a sample status
        # becomes noise, and the reagent table publishes no status column at all, which is that decision
        # made structural rather than left to a per-measurement exemption.
        #
        # The report and the rollup come out of one call, so the tag a reader sees beside the list
        # cannot disagree with the list.
        report, coverage = sample_report_rows(sample, rows[first:], control_rows)
        sample_report[sample] = {
            "status": None if coverage.status is None else coverage.status.value,
            "judged": coverage.judged,
            "unjudged": coverage.unjudged,
            "notEvaluated": coverage.not_evaluated,
            "measurements": report,
        }

    reagent_rows: list[dict] = []
    for panel_id in sorted(tags_of_panel):
        panel_samples_here = samples_of_panel[panel_id]
        panel_tags = tags_of_panel[panel_id]

        # The identity -> tags map comes from `grouping`, the one place that settles which tags an identity
        # carries. Scoped to this panel's samples and declarations.
        siblings_of_identity: dict[str, list[str]] = {}
        # A barcode reused for a different antigen in different samples carries two identities and takes a
        # row under each, so a tag maps to a LIST of identities and never to one.
        identities_of_tag: dict[str, list[str]] = {}
        # The samples where a tag carried one identity. Two identities of one tag hold disjoint sample
        # sets, because `grouping` gives each (tag, sample) exactly one identity.
        samples_of_pair: dict[tuple[str, str], set[str]] = {}
        for (tag, sample), identity in grouping.items():
            if tag not in panel_tags or (sample not in set(panel_samples_here) and sample != ANY_SAMPLE):
                continue
            members = siblings_of_identity.setdefault(identity, [])
            if tag not in members:
                members.append(tag)
            carried = identities_of_tag.setdefault(tag, [])
            if identity not in carried:
                carried.append(identity)
            # ANY_SAMPLE declares the identity for every sample of the panel.
            samples_of_pair.setdefault((tag, identity), set()).update(
                panel_samples_here if sample == ANY_SAMPLE else [sample]
            )

        # One row per (tag, identity), with every figure scoped to the samples where the tag carried that
        # identity. A tag absent from `grouping` here takes one row under its own barcode over the whole
        # panel.
        reference_here = set(reference_tags)
        pairs_of_subset: dict[frozenset[str], list[tuple[str, str]]] = {}
        for tag in sorted(panel_tags):
            for identity in sorted(identities_of_tag.get(tag, [tag])):
                scope = samples_of_pair.get((tag, identity), set()) & set(panel_samples_here)
                pairs_of_subset.setdefault(frozenset(scope or panel_samples_here), []).append((tag, identity))

        for scope, pairs in sorted(pairs_of_subset.items(), key=lambda kv: sorted(kv[0])):
            scope_samples = sorted(scope)
            scope_tags = {tag for tag, _ in pairs}
            scope_states = _listed(tag_states.filter(pl.col("sampleId").is_in(scope_samples))).rename(
                {"identity": "tag"}
            )
            scope_measure = {
                row["tag"]: row
                for row in per_antigen_measures(
                    _listed(counts.filter(pl.col("sampleId").is_in(scope_samples))),
                    scope_states,
                    scope_tags,
                    scope_samples,
                    reference_tags,
                ).iter_rows(named=True)
            }
            # A tag's siblings are the other tags of the identity on the row, and both rates are taken over
            # the row's samples only. Within one subset a tag appears under one identity, so the tag-keyed
            # rates these return are unambiguous.
            scope_siblings = {
                identity: siblings_of_identity[identity] for _, identity in pairs if identity in siblings_of_identity
            }
            scope_sibling_rate = sibling_disagreement(scope_states, scope_siblings)
            scope_tag_rate = _disagreement_rates(scope_samples)
            scope_held_a_cell = set(scope_states["tag"].unique().to_list())

            for tag, identity in sorted(pairs):
                measure = scope_measure.get(tag, {})
                above = measure.get("cellsAboveTheLine")
                median = measure.get("medianCountPerCell")
                sibling = scope_sibling_rate.get(tag)
                own = scope_tag_rate.get(tag)
                # Scoped to the row's own identity. `identity_of_tag` keeps one identity per tag and would
                # give a reused barcode's two rows the same reason.
                members = siblings_of_identity.get(identity, [])
                absences = []
                if above is None:
                    absences.append("cellsAboveTheLine=none asked, this tag supplies the baseline")
                if median is None:
                    absences.append("medianCountPerCell=no cell holds a count of this tag")
                # The words the sibling column prints where no rate exists, one per cause, kept short enough
                # to read in a cell. `reason` carries the same four causes in full.
                sibling_shown = f"{sibling:.2f}" if sibling is not None else ""
                if sibling is None:
                    if tag in reference_here:
                        absences.append("siblingDisagreement=this tag is held out of the verdict read")
                        sibling_shown = "held out of the read"
                    elif len(members) < 2:
                        absences.append("siblingDisagreement=this identity carries one tag, so it has no sibling")
                        sibling_shown = "no sibling"
                    elif tag not in scope_held_a_cell:
                        absences.append("siblingDisagreement=this tag holds no cell beside a sibling")
                        sibling_shown = "no cell beside a sibling"
                    else:
                        absences.append("siblingDisagreement=no cell gave this tag's siblings a majority")
                        sibling_shown = "no sibling majority"
                own_shown = f"{own:.2f}" if own is not None else "nothing to compare"
                if own is None:
                    absences.append("selfDisagreement=no cell set held this tag under an evaluable read")
                # Named beside the counts above rather than replacing them: samplesSeenIn and samplesInPanel
                # are released p-columns and keep their id. Sample ids are mapped through `label_of_sample`,
                # the same map that resolves the panel file's own names.
                reagent_rows.append(
                    {
                        "panelId": panel_id,
                        "tag": tag,
                        "identity": identity,
                        "samplesSeenIn": int(measure.get("samplesSeenIn") or 0),
                        "samplesInPanel": int(measure.get("samplesInPanel") or 0),
                        # The ratio the quality view prints in place of the two counts beside it.
                        "seenIn": (
                            f"{int(measure.get('samplesSeenIn') or 0)}/{int(measure.get('samplesInPanel') or 0)}"
                        ),
                        "samplesSeenInNames": ", ".join(
                            label_of_sample.get(s, s) for s in measure.get("samplesSeenInNames") or []
                        ),
                        "samplesInPanelNames": ", ".join(
                            label_of_sample.get(s, s) for s in measure.get("samplesInPanelNames") or []
                        ),
                        "cellsWithCount": int(measure.get("cellsWithCount") or 0),
                        "cellsAboveTheLine": float(above) if above is not None else None,
                        "medianCountPerCell": float(median) if median is not None else None,
                        "siblingDisagreement": float(sibling) if sibling is not None else None,
                        "siblingDisagreementShown": sibling_shown,
                        "selfDisagreement": float(own) if own is not None else None,
                        "selfDisagreementShown": own_shown,
                        "reason": "|".join(absences),
                    }
                )

    # The sample-level measurements, keyed by sample. Read as content and not as a table: the sample
    # detail view holds one sample at a time and resolves it synchronously.
    #
    # Only the sample carries an aggregated status, over its OWN measurements. A per-tag failure is
    # usually a property of the reagent across the whole run, so feeding a dead reagent in a panel of
    # twenty tags into a sample status would mark every sample alerting. It does not hide: the reagent
    # table states that finding on its own row, keyed by the panel that has it.
    with open(f"{prefix}_qc_by_sample.json", "w") as out:
        json.dump(sample_report, out, indent=2, sort_keys=True)

    # The across-samples table: one row per sample, one column per sample-level measurement, carrying
    # the sample's own rolled-up status. Pivoted from `sample_report` rather than walked a second time,
    # so it cannot disagree with the sample's own report above.
    _write_sorted(sample_summary_rows(samples, sample_report, read_qc), f"{prefix}_qc_summary.csv", ["sampleId"])

    # The two spreads the readout puts last, and a reader settles the cutoff and the gate by looking at
    # them. Both are pooled across samples and both are narrowed to the CELL LIST, through the same
    # `_listed` the count plots on this page use: the cutoff is one number for the run and so is the
    # gate, each acts on cells, and a spread taken over observed barcodes is a different population from
    # the one beside it on the page.
    #
    # BINNED, and nothing else. Quantile points cannot show WHERE a distribution separates, which is the
    # one thing both plots are read for. Binning is how the plot shows every cell without shipping one
    # row per cell.
    spread_bins: dict[str, dict[str, object]] = {}
    # ONE spread for the whole run, pooled across samples.
    # Absent on every rung but the declared one: no score, nothing to spread.
    if scored.height > 0:
        scores = np.asarray(
            specificity_score(
                scored["umiCount"].to_numpy(),
                np.nan_to_num(scored["referenceCount"].cast(pl.Float64).to_numpy(), nan=0.0),
            ),
            dtype=float,
        )
        score_edges = linear_bin_edges(scores)
        spread_bins["score"] = {"edges": score_edges, "weights": bin_values(scores, score_edges)}
    # Narrowed by key rather than through `_listed`: the comparator is a dict keyed by cell, not a frame.
    # An empty result is possible where a list arrived and no listed cell carries a comparator, and it
    # writes no rows rather than an all-zero spread.
    listed_readings = [value for key, value in reference.by_cell.items() if cell_list is None or key in cell_list]
    if listed_readings:
        readings = np.asarray(listed_readings, dtype=float)
        reading_edges = log1p_bin_edges(int(readings.max())) or log1p_edges_for(1)
        spread_bins["referenceReading"] = {
            "edges": reading_edges,
            "weights": bin_values(readings, reading_edges),
        }
    # Binned count distributions, per (sample, tag), for the plots that ask a reader to judge whether a
    # tag's counts fall into two separated humps. JSON rather than a p-frame: the chart these feed takes
    # its bins as values in the UI. Weights only, with one shared edge list beside them, keeps it small
    # -- a run of 24 samples over a 64-tag panel is 24 x 64 lists of 24 integers.
    #
    # Taken from the RAW counts. The floor and the reference hold-out both happen later, and a plot read
    # in order to SET the floor cannot have the floor already applied to it.
    #
    # Restricted to the CELL LIST, as every per-cell figure on this page is. In droplet data the observed
    # barcodes outnumber the cells by one to two orders of magnitude, and an ambient population that size
    # is the only hump a panel shows.
    #
    # A run with no cell list bins every barcode, and `cellListSource` in the run meta says which case a
    # plot was drawn under. The edges are taken from the same filtered frame, so the shared domain ends
    # at the highest count among cells rather than among barcodes.
    # The plot is binned over exactly the cells the fit ran on: one entry per cell in the sample, with a
    # cell that read nothing counted as a zero.
    fitted_bins = tag_fits.bins if tag_fits is not None else {}
    bin_counts = _listed(counts)
    if fitted_bins:
        tag_bins: dict[str, dict[str, list[int]]] = {}
        for (sample, tag), weights in fitted_bins.items():
            if weights:
                tag_bins.setdefault(str(sample), {})[str(tag)] = weights
        bin_edges = log1p_edges_for(max((len(w) for w in fitted_bins.values()), default=0))
    else:
        # No fit ran, so there is no fitted population to bin. The observed readings still carry a shape
        # worth showing, on the same equal-width edges.
        observed = int(bin_counts["umiCount"].max() or 0) if bin_counts.height else 0
        bin_edges = log1p_bin_edges(observed)
        tag_bins = per_tag_count_bins(bin_counts, bin_edges)
    # The fit's two means travel WITH the bins, at the same (sample, tag) grain. They are what a reader
    # judges the humps against, so reaching them through the p-frame beside this would mean a driver
    # query per panel of the grid. Absent under a declared baseline, which fits nothing.
    fits_by_sample: dict[str, dict[str, dict[str, float]]] = {}
    fit_curves = tag_fits.curves if tag_fits is not None else {}
    for (sample, tag), b in (tag_fits.backgrounds if tag_fits is not None else {}).items():
        # The count at which the run's bound probability starts calling a cell bound, resolved from this
        # pair's own scored curve.
        curve = fit_curves.get((sample, tag))
        crossing = None if curve is None else bound_at_count(curve, args.bound_probability)
        fits_by_sample.setdefault(str(sample), {})[str(tag)] = {
            "backgroundMean": float(b.mean),
            "signalMean": float(b.signal_mean),
            "backgroundWeight": float(b.weight),
            "boundAtCount": crossing,
        }
    with open(f"{prefix}_qc_tag_bins.json", "w") as out:
        json.dump(
            {
                "edges": bin_edges,
                "bySample": tag_bins,
                "fitsBySample": fits_by_sample,
                # The same names `result_tag_labels.csv` carries, so a tag reads under one name on the plots
                # and in the reagent table. The plots are drawn from this JSON rather than from a p-frame, and
                # a label column reaches only p-frame surfaces, so without this every panel title is a barcode.
                "tagLabels": tag_names,
                # The panel's OWN row order.
                "tagOrder": list(dict.fromkeys(panel["tag"].to_list())),
                # The run's score spread and its reference readings, on their own linear edges. Each is one
                # distribution for the whole run, pooled across samples and narrowed to the cell list, so it
                # carries the same population as the count bins beside it.
                "spreads": spread_bins,
            },
            out,
            sort_keys=True,
        )

    # One row per (sample, tag) the fit scored, at the fit's own grain. Aggregating to the tag would
    # hide a reagent that separated in one sample and not in another.
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

    _write_sorted(
        pl.DataFrame(reagent_rows, schema=_REAGENT_SCHEMA),
        f"{prefix}_reagents.csv",
        ["panelId", "identity", "tag"],
    )

    _write_sorted(
        pl.concat([pl.DataFrame(schema=_UNDECLARED_BARCODE_SCHEMA), *undeclared_barcode_frames]),
        f"{prefix}_undeclared_barcodes.csv",
        ["sampleId", "tag"],
    )

    meta = {
        "referenceChoice": reference.served.value,
        "referenceSourceRequested": source.value,
        # Whether a baseline was established, and where not, why. Only the tag-distribution rung can
        # reach false: its conditions are properties of the data, so a run resting on it proceeds and
        # reports afterwards. The other rungs refuse from the settings.
        #
        # The model reads this and draws no punchcard where it is false. The answer frames are
        # header-only in that case, so a consumer that reads them anyway finds no rows.
        "baselineEstablished": no_baseline_reason is None,
        "noBaselineReason": no_baseline_reason,
        "cellListSource": cell_list_source,
        "cellsInList": len(cell_list) if cell_list is not None else None,
        "cellsAnalysed": len(analysed_cells),
        "floor": args.floor,
        "cutoff": args.cutoff,
        "boundProbability": args.bound_probability,
        "minVoters": args.min_voters,
        "minAgreement": args.min_agreement,
        "gateThreshold": args.gate_threshold,
        "panelMinMembers": args.panel_min_members,
        "distributionMinCells": args.distribution_min_cells,
        "initialSignalWeight": args.initial_signal_weight,
        # Per (sample, tag), and only where that rung was asked for: which tags could not be fitted,
        # and why. A tag missing here fitted. The reader needs both halves to tell a panel that mostly
        # worked from one that mostly did not.
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
        # The narrowing a short panel file costs, carried in the output rather than only in a log line:
        # these tags were answered under a grouping that could not place them.
        "tagsWithoutGroupingValue": sorted(ungrouped_tags),
        "contending": [sorted(group) for group in contending],
        "identityCount": len(universe),
        # The identities themselves, in the order the pivot lays them out. The workflow builds one
        # p-column per column of result_identity_summary.csv, and the column names are the identities --
        # panel data, unknown until this runs. Without this the pivoted summary imports as nothing and
        # the only per-antigen state a clonotype-anchored reader can see disappears with no error.
        "identities": sorted(universe),
        # Read by the workflow to label the punchcard's columns. An identity whose grouping value was
        # dropped is labelled with the names it did declare, so the card shows a reagent rather than a
        # 15-mer. Every other identity labels itself.
        "identityLabels": {identity: labels.get(identity, identity) for identity in sorted(universe)},
        # Why a label above is two names joined. Keyed exactly as `_identity_labels` keys its own
        # lookup, so an entry appears for precisely the identities whose label was joined. Only genuine
        # conflicts: one declared name is the ordinary case. The workflow turns each entry into the
        # column's description annotation, shown as a header tooltip.
        "identityNameConflicts": {
            identity: sorted(names)
            for identity in sorted(universe)
            if len(names := label_disagreements.get(identity, [])) > 1
        },
        # The declaration columns that reached result_identity_properties.csv, and the distinct values
        # each carries. Both are panel data: the workflow builds one p-column per name and annotates it
        # with its own value set, so without these the declarations import as nothing.
        "identityProperties": emitted_properties,
        "identityPropertyValues": {c: property_values[c] for c in emitted_properties},
        "identitySummaryEmitted": summary_emitted,
        # False where the run was too wide or too deep for the dense per-cell grid, so the readout can
        # say which of the two it was rather than showing an empty tab.
        "cellPunchEmitted": cell_punch_emitted,
        "cellPunchCells": len(cell_punch),
        # The tags the per-cell pivot laid out, in column order. The workflow builds one p-column per
        # entry, so an absent list imports the pivot as nothing with no error raised anywhere.
        "cellTagPivotTags": cell_tag_pivot_tags,
        # And their names, because the pivot has no tag axis to hang a label column on: the axis became the
        # headers. Without this every column of the family is titled with a 15-mer barcode. Keyed over
        # exactly the columns above, and falling back to the barcode for a tag the panel never named.
        "cellTagPivotLabels": {t: tag_names.get(t, t) for t in cell_tag_pivot_tags},
        # The tags the per-clonotype median pivot laid out, and their names, on the same terms as the
        # per-cell pivot above. Unlike that one this is every barcode the panel declares, not every
        # barcode with something to report, so the column set holds still between runs.
        "setTagMedianTags": set_tag_median_tags,
        "setTagMedianLabels": {t: tag_names.get(t, t) for t in set_tag_median_tags},
        "identitySummaryLimit": IDENTITY_SUMMARY_MAX_IDENTITIES,
        "cellPunchLimit": CELL_PUNCH_MAX_CELLS,
        # The undeclared-barcode table holds the heaviest sequences per sample, not every one. The
        # share carries every row either way, so a run with an elided count still measures exactly.
        "undeclaredBarcodeLimit": UNDECLARED_BARCODES_KEPT,
        "undeclaredBarcodesElided": sum(t.elided for t in (raw_tallies or {}).values()),
        "readingsFloored": readings_floored,
        "cellsEmptied": cells_emptied,
        "cellsHighReference": cells_high_reference,
        "cellsSetAside": len(gated),
        # The same tally per clonotype, for the expansion, and present only when a gate was declared:
        # the UI's whole condition is an absent key. Sparse -- a clonotype that lost nothing carries no
        # entry, and an absent key reads as zero -- because this file is parsed on every render.
        **(
            {"cellsSetAsideBySet": {k: v for k, v in count_by_set(cells_by_set, gated).items() if v > 0}}
            if args.gate_threshold
            else {}
        ),
        "panelLinesDropped": dropped_lines,
        "samples": samples,
        "setCount": len(cells_by_set),
        # How many DISTINCT panels the run carried. One means every sample was stained with the same
        # tags, and then how many of a clonotype's cells could answer is the same at every identity --
        # its own cell count, which the grid already shows beside its name.
        "samplePanelCount": len(set(panel_of_sample.values())),
    }
    with open(f"{prefix}_run_meta.json", "w") as out:
        json.dump(meta, out, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
