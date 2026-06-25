# Feature Integration

Feature Integration assigns features to single cells from feature-barcode reads. Antigen binding is
the headline use case (BEAM — Barcode-Enabled Antigen Mapping), but a tag can map to any feature
given the right structure.

Per cell, it reports which antigens that cell bound and how strongly: a feature abundance matrix
(UMI counts), within-cell feature fractions, a consensus (dominant) feature, and — when a
negative-control antigen is designated — a per-antigen specificity score. This per-cell output is
the contract that the VDJ Multiomic Integration block consumes to produce per-clonotype binding
profiles for lead selection.

The block delivers value on its own: sample demultiplexing is a separate, optional step, never a
prerequisite.
