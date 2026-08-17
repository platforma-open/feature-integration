# Verdict Fixture Bed

The bed the binding-verdict tests run the whole CLI against. Committed rather than generated at test
time, so a test never has to run the generator, and excluded from ruff. Regenerate with
`python generate.py` — it is stdlib only and takes one fixed seed, so the files come back byte
identical.

Entirely synthetic. This repository is public: barcodes are random ACGT strings, antigens are `AgNN`,
samples are `SNN`, and no real sequence, antigen or sample identifier appears anywhere.

## Files

| File | What it is |
|---|---|
| `panel.csv` | Four samples, panels of 3, 4, 4 and 5 tags. **No** comparator tag. |
| `panel_with_reference.csv` | The same panels plus **one** comparator tag (`Ctrl1`) on every sample. |
| `panel_multi_reference.csv` | The same panels plus **two** comparator tags (`Ctrl1`, `Ctrl2`) on every sample. |
| `counts.csv` | Sparse per-(sample, cell, barcode) UMI counts for all eleven cells. |
| `linker.csv` | Cell to clonotype set: `K01`, `K02`, `K03` (spanning two samples), `K04` (a singleton). |

Columns are `Samples,Name,Sequence,Type` in the panels, so a run reads the bed with
`--barcode-col Sequence --feature-col Name --sample-col Samples --role-column Type
--reference-values Control`.

One `counts.csv` serves all three panels, so the two comparator barcodes are read in every run
including those whose panel does not declare them. Those readings surface as `undeclared-in-panel`
rows and are expected: `panel_multi_reference.csv` is the only panel here that declares every barcode
the counts carry, so it is the bed to use when the mismatch table itself is under test.

## Panel shapes covered

| Shape | How the bed carries it |
|---|---|
| Per-sample panels of differing size | 3, 4, 4 and 5 tags. `K01` is drawn from the three-tag sample alone, so five of the eight identities read *never asked*. |
| Same barcode, different names across samples | Four barcodes carry two `AgNN` names each. A fifth recurs under one name, so a test can tell "recurs" from "recurs inconsistently". |
| One panel over every sample | Not a separate file: the comparator rows are declared on all four samples, which is the unkeyed case within a keyed panel. |
| A designated negative control | `panel_with_reference.csv`. |
| **No** negative control | `panel.csv`. Eight distinct barcodes, exactly the shipped minimum of eight, so the panel serves as its own comparator rather than falling silent. |
| **Several** designated controls | `panel_multi_reference.csv`. `Ctrl2` reads above `Ctrl1` in every cell, so the served comparator is 60 and not 6 — and bindings that hold at 6 fall away at 60. |
| One antigen on several barcodes | `Ag07` is carried on two barcodes, both on the fourth sample. |
| A barcode declared in one sample, read in another | `Ag06`'s barcode is declared by the third sample only and read in the second only, so both directions of the check fire on different samples at once. |
| Free-text properties, inconsistently spelled | Not carried here. The panel has no free-text property column beyond `Name`; `test_panel.py` covers the hygiene measurement. |

## The counts, and which threshold each one is for

Shipped defaults in `verdict.py`: floor **4**, comparator thin line **2**, bound cutoff **75** on
`specificity_score`, high-reference observation line **100**. The score is a beta function and not a
ratio, so the useful values are not where intuition puts them — against a comparator of 6 a count of
8 scores 0.0001, 50 scores 3.1, 60 scores 7.2 and 500 scores 100.

| Count | Chosen against |
|---|---|
| `8` | The *not bound* reading. Above the floor of 4, so it survives to be compared, and 0.0001 against a comparator of 6, so it is compared and fails. A count of 2 would be zeroed by the floor and read *not bound* for a different reason. |
| `500` | The *bound* reading while the comparator is 6 (score 100) — and a *not bound* reading against 60 (score 0.1). That difference is what the two-comparator panel measures. |
| `5000` | Bound against either comparator, so one binding survives on the two-comparator panel and the bed does not degenerate into all *not bound*. |
| `2` (one reading only) | Below the floor of 4, so it is zeroed and counted in `readingsFloored`. |
| `6` (`Ctrl1`) | Above the thin line of 2 so cells can be compared, and far below 500 so a real binding clears the cutoff. |
| `60` (`Ctrl2`) | Above `Ctrl1` so the highest-member rule is observable, and below the high-reference line of 100 so that measurement stays quiet. |
| `1` (`Ctrl1` in one cell) | Below the thin line of 2. This is the bed's only source of *unreliable*: raise it and the fourth state disappears. |

## What each set reads, on `panel_with_reference.csv`

| Set | Cells | Reads |
|---|---|---|
| `K01` | three cells of the three-tag sample | *bound* twice, *not bound* once, *never asked* five times. One of its cells is silent on a bound identity and votes *not bound* against two that bind it. |
| `K02` | three cells of a four-tag sample | *bound* twice, *not bound* twice, *never asked* four times. One of its readings is floored. |
| `K03` | four cells across two samples | Offered the union of two panels, so nothing in it reads *never asked*. Two identities read *unreliable* on a tie. |
| `K04` | one cell whose comparator reads 1 | *unreliable* everywhere it was offered, *never asked* elsewhere. |

Two readings are worth naming, because both are states an earlier revision got wrong:

- `Ag06`'s barcode is declared by the third sample and read in none of its cells. `K03` draws from
  that sample, so it was offered `Ag06`, its cells could be compared, and they read nothing — which
  is *not bound*, not *never asked*. A silent cell that can be compared is a negative answer.
- The same barcode is read in the second sample, which never declared it. `K02` therefore reads
  *never asked* at that identity while a real count of 500 sits in `counts.csv`. The verdict follows
  the panel; the mismatch table is what makes the reading visible.
