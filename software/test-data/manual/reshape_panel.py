#!/usr/bin/env python3
"""Rewrite a generated run's tags.csv into the two shapes a real panel file arrives in.

    python3 reshape_panel.py runs/tiny

Writes `tags_narrow.csv` and `tags_wide.csv` beside `tags.csv`, both carrying **the same barcodes**, so
either can be uploaded to the block against the same FASTQs. A reshaped panel with a changed barcode
joins to nothing, which is why nothing here touches the `tag` column.

Why this exists. `generate.py` emits one panel shape: `tag,feature,Type,Species,Class`, one panel for
every sample, with the control carrying its own `Decoy` role. Two other shapes were observed in use at
one account at the same time, on two of its projects, and neither looks like that:

  narrow  sample, barcode, antigen name -- and no fourth column. Nothing declares a role, so nothing can
          be named as the comparator and the panel's own readings have to serve.
  wide    sample, name, catalogue id, barcode, channel, a constant, role. The role column declares what
          a member is TO THE QUESTION (target, off-target) and carries **no** comparator value.

In both, the negative control is one antigen the scientist points at by name in the interface. So
neither shape can reach the declared-comparator path, for two different reasons. That is the thing these
files exist to make visible in the app rather than only in a CSV.

Both shapes rename a barcode between samples: the same sequence carries a different antigen name in
different samples, which is the tag-inventory reuse the per-sample keying of the panel exists for. Under
the per-tag grouping the identity is the barcode, so those identities lose their label and a reader
meets a raw 15-mer where every other row shows an antigen.

Deterministic: every choice below is positional, so a rerun over the same tags.csv is byte-identical.
Stdlib only, like the rest of this bed.
"""

import argparse
import csv
import os
import sys

# Four values that are three channels — one of them spelled two ways, so grouping on this column splits
# one channel in two. Assigned by position, cycling.
CHANNELS = ["PE", "PE", "APC", "APC", "PE Dazzle", "PE Dazzle", "PE-Dazzle 5120", "PE-Dazzle 5120"]

# One value on every row: a declared property carrying no information at all. Group on it and every tag
# lands in one identity, which is legal and useless.
RESIDUES = "ECD protein"

# The control's own role is folded into the off-target set on purpose. The observed wide file had no
# value meaning "comparator" anywhere in its role column, and that is the whole point of the shape.
ROLE_OF = {"Target": "Target (Primary)", "Off-Target": "Off-Target", "Decoy": "Off-Target"}


def _lowercased(role: str) -> str:
    """The role as the observed file also spelled it — the qualifier or the word after the hyphen."""
    return role.replace("(P", "(p").replace("(S", "(s").replace("-Target", "-target")


def read_tags(run_dir: str) -> list[dict]:
    path = os.path.join(run_dir, "tags.csv")
    if not os.path.exists(path):
        sys.exit(f"no tags.csv in {run_dir} — generate a run first (python3 generate.py tiny --arm antigen)")
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for col in ("tag", "feature"):
        if not rows or col not in rows[0]:
            sys.exit(f"{path} has no {col!r} column; columns are {list(rows[0]) if rows else '[]'}")
    return rows


def read_samples(run_dir: str) -> list[str]:
    """Sample names from the antigen arm's filenames, so they match Samples & Data exactly."""
    antigen = os.path.join(run_dir, "antigen")
    if not os.path.isdir(antigen):
        sys.exit(f"no antigen/ arm in {run_dir} — run: python3 generate.py <preset> --arm antigen")
    names = sorted({f.split("_R")[0] for f in os.listdir(antigen) if f.endswith(".fastq.gz")})
    if not names:
        sys.exit(f"no FASTQs in {antigen}")
    return names


def _renamed(name: str, sample_index: int) -> str:
    """A plainly different antigen name for a reused barcode in a later sample."""
    return f"{name}__alt{sample_index}"


def write_narrow(path: str, tags: list[dict], samples: list[str], rename: int, drop: int) -> int:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Sample", "Sequence", "Antigen"])
        rows = 0
        for s_i, sample in enumerate(samples):
            # A later sample may declare fewer tags, which is what makes *never asked* reachable: a set
            # whose cells sit only in that sample was never offered the dropped identities.
            offered = tags[: len(tags) - drop] if s_i else tags
            for t_i, tag in enumerate(offered):
                name = _renamed(tag["feature"], s_i) if (s_i and t_i < rename) else tag["feature"]
                w.writerow([sample, tag["tag"], name])
                rows += 1
    return rows


def write_wide(path: str, tags: list[dict], samples: list[str], rename: int, drop: int) -> int:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Samples", "Name", "Barcode", "Sequence", "Channel", "Residues", "Type"])
        rows = 0
        for s_i, sample in enumerate(samples):
            offered = tags[: len(tags) - drop] if s_i else tags
            for t_i, tag in enumerate(offered):
                name = _renamed(tag["feature"], s_i) if (s_i and t_i < rename) else tag["feature"]
                role = ROLE_OF.get(tag.get("Type", "Target"), "Target (Primary)")
                # Two case-variant failure modes, kept apart so each can be told from the other. Tag 0
                # reads one spelling in the first sample and another in the rest, so it carries two
                # values, the property is dropped for it, and it ends up with no role at all. Tag 1
                # reads the other spelling everywhere, so it keeps its role but no longer matches the
                # same role written normally elsewhere.
                if (t_i == 0 and s_i) or t_i == 1:
                    role = _lowercased(role)
                w.writerow(
                    [
                        sample,
                        name,
                        f"T{100 + t_i:04d}",
                        tag["tag"],
                        CHANNELS[t_i % len(CHANNELS)],
                        RESIDUES,
                        role,
                    ]
                )
                rows += 1
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", help="a generated run directory, e.g. runs/tiny")
    p.add_argument(
        "--rename",
        type=int,
        default=2,
        help="barcodes carrying a different antigen name in later samples (default 2; 0 disables)",
    )
    p.add_argument(
        "--drop-from-later",
        type=int,
        default=0,
        help="tags a later sample does not declare, making *never asked* reachable (default 0)",
    )
    args = p.parse_args()

    tags = read_tags(args.run_dir)
    samples = read_samples(args.run_dir)
    if args.rename > len(tags) or args.drop_from_later >= len(tags):
        sys.exit(f"--rename/--drop-from-later exceed the panel's {len(tags)} tags")

    narrow = os.path.join(args.run_dir, "tags_narrow.csv")
    wide = os.path.join(args.run_dir, "tags_wide.csv")
    n_rows = write_narrow(narrow, tags, samples, args.rename, args.drop_from_later)
    w_rows = write_wide(wide, tags, samples, args.rename, args.drop_from_later)

    kept = len(tags) - args.drop_from_later
    print(f"{len(tags)} tags x {len(samples)} samples -> {samples}")
    print(f"  {narrow}  ({n_rows} rows, 3 columns, no role column)")
    print(f"  {wide}  ({w_rows} rows, 7 columns, role column with no comparator value)")
    print(f"  {args.rename} barcode(s) renamed in later samples; later samples declare {kept} of {len(tags)}")
    if len(tags) < 8:
        print(f"  WARNING: {len(tags)} tags is below the shipped panel minimum of 8, so the panel's own")
        print("           readings cannot serve and every verdict will read unreliable. Regenerate with")
        print("           a larger --panel-size, or lower the minimum in the block's settings.")


if __name__ == "__main__":
    main()
