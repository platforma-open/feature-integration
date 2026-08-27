# Overview

This block reads feature-barcode sequencing and answers, for every clonotype in the run, what it bound.
The headline use case is antigen binding — BEAM- and LIBRA-seq–style experiments — but it works for any
barcoded feature, such as surface-protein tags.

It takes the feature-barcode FASTQ files, a panel file declaring what each barcode carries in each sample,
and the single-cell V(D)J dataset the clonotypes come from. It counts the barcodes per cell, reads each
count against a baseline you choose, and combines the cells of a clonotype into one answer per antigen.

**The answer has four states, not two.** A clonotype against an antigen reads *bound*, *not bound*, *never
asked* — no sample its cells came from offered that antigen, or the reagent returned nothing — or
*unreliable*, where the experiment did ask and the data cannot settle it. The two non-answers reach you
rather than being folded into *not bound*, because a clone shown to be clean of an off-target and a clone
never tested against it are different candidates, and the difference otherwise surfaces months later at the
cost of a made molecule.

Each verdict carries what it rests on: how many of the clonotype's cells could answer, how many read
bound, and — where you declared two antigens as competing for one site — the competitor that bound
instead.

**Which barcodes are read as one antigen is your choice, and it belongs to the question.** Group on the
antigen column and ten strains on twelve barcodes are ten answers; group on a family column and they are
one. Regrouping re-runs the reading alone, not the counting.

**What a count is read against is also your choice**, from the baselines the run supports: a control tag
declared in the panel and read in the same cell, or each barcode's own distribution across a sample's
cells. The block computes which are available and refuses to pick for you, because a baseline nobody chose
is a method nobody knows they used — and which one served travels with every verdict, since no two produce
comparable numbers.

The block also reports the quality of the run before you spend time on its biology — per sample, per
reagent, and per barcode the panel never declared — so a failed reagent or a mis-declared panel is visible
as itself rather than as a clone that did not bind.

The verdicts are keyed on the clonotype, so downstream blocks read them directly to filter and rank leads.

This block uses mitool, which is developed by MiLaboratories Inc. For more information, please see the
[mitool reference](https://mixcr.com/mixcr/reference/mitool-parse).
