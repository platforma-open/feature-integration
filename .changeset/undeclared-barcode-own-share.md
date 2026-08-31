---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration': minor
---

Give each undeclared barcode its own share, and publish what correction rescued

The undeclared-barcode table carried one share, labelled "Share of the sample's reads",
and it was the SAMPLE's whole undeclared share repeated on every row. On a real BEAM run
that read as the same percentage against 153,106 different barcodes, which reads as a bug
whether or not the number is right.

- Each row now carries its own share as well: that sequence's weight over every pre-refine
  read of its sample. On the run above the two heaviest sequences read 16.51% and 10.22%
  instead of 31.39% each. The sample-level figure stays, relabelled "Undeclared share
  (whole sample)", because the field publishes a line for it and the Status column reads it.
- The table is the PRE-refine pass, so a row is not a read the run lost: refine-tags snaps a
  sequence close enough to a panel barcode onto it. That was nowhere on the page, so a heavy
  near-neighbour row read as loss. Each sample now reports the share correction rescued --
  the undeclared share less the reads refine-tags dropped. On the run above that is 1.18% of
  the library, ~394k reads, against 258 sequences one substitution from a panel barcode.
