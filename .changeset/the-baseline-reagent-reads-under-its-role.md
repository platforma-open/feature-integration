---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

The baseline reagent's row in the reagent table names its role.

A declared baseline tag is held out of every identity, and the reagent table gives a tag absent from
the grouping a row keyed on its own barcode. The identity label table was written over the identity
universe alone, so that row asked for a name nothing had emitted and rendered blank — a table whose
every other row names an antigen, with one row naming nothing, beside a Barcode axis showing the raw
15-mer. The sibling tag-label table already covers reference tags, which is why the same row's Tag
column reads correctly; the reasoning was applied to one of the two tables and not the other.

The label is the ROLE, `baseline reagent`. Not the reagent's own name, which the same row already
carries in its Tag column. Not the value it holds in the grouping column — `Decoy` beside
`Off-Target` and two targets reads as a fourth identity, and the run scores three.

The row itself stays. It is where a run shows what its comparator delivered, and a baseline reagent
present in a few percent of cells is a fact about every verdict the run read against it.

`result_identity_labels.csv` now carries a row per identity plus one per reference tag. The two key
sets are disjoint, since the grouping builder excludes reference tags. The run record's
`identityLabels` is unchanged and still spans the identity universe alone: it titles the punchcard's
columns, which must not gain one.
