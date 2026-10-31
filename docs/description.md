# Overview

This block reads feature-barcode sequencing and determines, for every clonotype in the run, which antigens
it bound. The headline use case is antigen binding in BEAM- and LIBRA-seq-style experiments, but it works
for any barcoded feature, such as surface-protein tags. It links each antigen to the receptors that bound
it, producing results ready for lead selection.

The block takes the feature-barcode FASTQ files, a panel file declaring what each barcode carries in each
sample, and the single-cell V(D)J dataset the clonotypes come from. It uses mitool to parse the reads,
counts barcodes per cell, reads each count against a user-selected baseline — a control tag declared in the
panel, or each barcode's own distribution across a sample's cells — and combines the cells of a clonotype
into one verdict per antigen.

Every verdict is one of four states:

- **bound** — measured, and the cells cleared the defined bound threshold
- **not bound** — measured, and they did not
- **unreliable** — measured, but too few of its cells answered, or they disagreed
- **never asked** — no measurement exists: the antigen appeared in no read from any of those cells' samples

Each verdict also reports how many cells it rests on.

The block also reports run quality per sample and per reagent, along with any barcodes found in the reads
that the panel does not declare.

The verdicts are keyed on the clonotype, so downstream blocks read them directly — Labeling to turn
verdicts into named specificity labels, and Lead Selection to filter and rank candidates.

mitool is developed by MiLaboratories Inc. For more information, please see the
[mitool reference](https://mixcr.com/mixcr/reference/mitool-parse).
