"""Emit per-feature properties from the tag->feature CSV's EXTRA columns.

The user maps two columns of the tag CSV to roles: the feature-barcode sequence (the join key /
panel) and the feature name (the antigen identity). Every OTHER column is an arbitrary per-feature
property — antigen type, species of origin (human / cyno), pool, etc. This step imports each such
column generically (no hardcoded schema) so the workflow can surface it as a p-column keyed on the
feature axis, and the property then rides that axis into VDJ Multiomic Integration's per-feature
outputs and lead selection (see spec A-0026).

Two outputs (stdlib only -- a trivial, fast pre-step like emit_csv_meta / emit_panel):
  * ``<prefix>_feature_properties.csv`` -- one row per distinct feature; columns are ``feature``
    (the feature-name column, always renamed to ``feature`` so the downstream xsv import keys it on
    the feature axis) followed by every extra column under its own header. Rows sorted by feature.
  * ``<prefix>_feature_property_meta.json`` -- ``{columns, valuesByColumn}``: the property column
    names in file (header) order, and each property's distinct non-empty values (sorted). The
    workflow reads this to build one import column per property (and each column's discreteValues).

Deduplication: a feature barcode can map many-to-one onto a feature, so a feature appears on several
CSV rows. Properties are intrinsic to the feature, so they are expected to agree across those rows;
the FIRST non-empty value (in file order) wins and a differing later value is reported to stderr but
not treated as fatal -- Feature Integration imports values as given and does not validate them
(A-0026). The output is fully deterministic for a given input file.
"""

import argparse
import csv
import json
import sys


def _read_header(reader: "csv._reader") -> tuple[list[str], dict[str, int]]:
    """Return (ordered named columns, name -> first column index). Blank header cells are dropped;
    the first index wins for a duplicated header (read by position so order is preserved -- DictReader
    would collapse duplicates and lose order, mirroring emit_csv_meta)."""
    header = next(reader, None)
    if not header:
        raise SystemExit("no header row found in the tag->feature CSV")
    ordered: list[str] = []
    col_index: dict[str, int] = {}
    for i, h in enumerate(header):
        name = h.strip()
        if name and name not in col_index:
            col_index[name] = i
            ordered.append(name)
    return ordered, col_index


def parse_properties(
    rows: list[list[str]],
    col_index: dict[str, int],
    feature_col: str,
    property_cols: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """Collapse raw CSV rows into per-feature property values + each property's distinct value set.

    ``by_feature[feature][property]`` = the first non-empty value seen for that (feature, property),
    in file order. ``values[property]`` = the set of distinct non-empty values. A later row that
    disagrees with an already-recorded non-empty value is reported to stderr (kept, not overwritten).
    Pure and unit-tested; the CLI wires it to CSV I/O.
    """
    by_feature: dict[str, dict[str, str]] = {}
    values: dict[str, set[str]] = {c: set() for c in property_cols}
    fi = col_index[feature_col]
    for row in rows:
        if fi >= len(row):
            continue
        feature = row[fi].strip()
        if not feature:
            continue
        props = by_feature.setdefault(feature, {})
        for c in property_cols:
            ci = col_index[c]
            val = row[ci].strip() if ci < len(row) else ""
            if not val:
                continue
            values[c].add(val)
            if c not in props:
                props[c] = val
            elif props[c] != val:
                print(
                    f"[emit-feature-properties] feature {feature!r} has conflicting {c!r} values "
                    f"({props[c]!r} vs {val!r}); keeping the first",
                    file=sys.stderr,
                )
    return by_feature, values


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag_feature_csv", help="tag->feature CSV")
    p.add_argument(
        "--csv-barcode-col",
        default="tag",
        help="CSV column holding the feature barcode (a role column, excluded from properties)",
    )
    p.add_argument(
        "--csv-feature-col",
        default="feature",
        help="CSV column holding the feature/antigen name (the key; renamed to 'feature' on output)",
    )
    p.add_argument(
        "--sample-col",
        default="",
        help="optional sample column for sample-aware mapping (a role column, excluded from properties)",
    )
    p.add_argument("--output-prefix", default="result")
    args = p.parse_args()

    with open(args.tag_feature_csv, newline="") as fh:
        reader = csv.reader(fh)
        ordered, col_index = _read_header(reader)
        for role, name in (("--csv-barcode-col", args.csv_barcode_col), ("--csv-feature-col", args.csv_feature_col)):
            if name not in col_index:
                raise SystemExit(f"{role}={name!r} not found in the tag->feature CSV (columns: {ordered})")
        rows = list(reader)

    # Role columns are excluded; every remaining named column is a per-feature property. The sample
    # column (sample-aware mapping) is a role too, so it is excluded when set -- it is not a property.
    roles = {args.csv_barcode_col, args.csv_feature_col}
    if args.sample_col:
        roles.add(args.sample_col)
    property_cols = [c for c in ordered if c not in roles]

    by_feature, values = parse_properties(rows, col_index, args.csv_feature_col, property_cols)

    print(
        f"[emit-feature-properties] {len(property_cols)} extra column(s) {property_cols}; "
        f"{len(by_feature)} distinct feature(s)",
        file=sys.stderr,
    )

    # Wide per-feature CSV: feature + one column per property, one row per feature (sorted), missing
    # values blank. The feature-name column is always emitted as 'feature' so the downstream xsv import
    # keys it on pl7.app/feature/featureId regardless of the source header.
    with open(f"{args.output_prefix}_feature_properties.csv", "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(["feature"] + property_cols)
        for feature in sorted(by_feature):
            props = by_feature[feature]
            w.writerow([feature] + [props.get(c, "") for c in property_cols])

    # Meta: property column names (header order) + each property's distinct sorted values. The workflow
    # builds one import column per name and puts the values into that column's discreteValues.
    meta = {"columns": property_cols, "valuesByColumn": {c: sorted(values[c]) for c in property_cols}}
    with open(f"{args.output_prefix}_feature_property_meta.json", "w") as out:
        json.dump(meta, out)


if __name__ == "__main__":
    main()
