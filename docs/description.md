# Overview

This block uses feature-barcode sequencing reads to identify which antigen each single cell bound, and how strongly. The headline use case is antigen binding (BEAM- and LIBRA-seq–style experiments), but it works for any barcoded feature, such as surface-protein tags.

The block takes as input feature-barcode FASTQ files and a CSV mapping each barcode to its feature (an antigen, in the main use case). For each cell it reports the antigen abundance (UMI counts), each antigen's fraction within that cell, the dominant (consensus) antigen, and — when a negative-control antigen is designated — a per-antigen specificity score that separates genuine binders from background.

The per-cell output can then be used in downstream blocks such as VDJ Multiomic Integration, which aggregates it onto V(D)J clonotypes to link each cell's antibody or TCR sequence to the antigen(s) it recognizes — the basis for selecting antigen-specific leads.

This block uses mitool, which is developed by MiLaboratories Inc. For more information, please see the [mitool reference](https://docs.platforma.bio/mixcr/reference/mitool-parse).
