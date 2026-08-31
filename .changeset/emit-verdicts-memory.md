---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration': minor
---

Cut the memory the antigen reading needs, and size its request from a measurement

The reading was killed by the OOM handler on a 44-sample BEAM run. Three causes, all
measured on a synthetic of that run's shape (28.5M counts rows, 1.83 GB counts.csv):

- The input tables were read a column at a time as strings and then re-scanned value by
  value in Python. Now projected and typed in one pass: 11.04 GB -> 6.48 GB, 5.9s -> 1.0s.
- The pre-refine FEATURE table carries one row per distinct sequence per sample --
  sequencing-error diversity, 240.7M rows on that run -- and was read whole at 187 B/row,
  ~45 GB. Now tallied in one batched pass at a flat ~0.5 GB. The undeclared-barcode table
  carries the heaviest 1000 sequences per sample instead of every one; the undeclared share
  is still computed over every row, so the QC measure is unchanged. The run record reports
  the limit and how many rows were elided.
- Combining tags into identities joined and grouped the whole run eagerly. Now lazy and
  projected: 7.4 GB -> 0.1 GB across its two calls.

Peak for the step falls from 26.2 GB to 17.5 GB with the pre-refine table no longer able to
add 45 GB, and wall time from 377s to 314s.

The RAM formula asked for `16 GiB + 8 x size(counts)`. The 8 was never reached because the
16 GiB floor covered any counts file below ~0.8 GiB; the first run past that knee died. It
is now 24x, measured from 614 bytes of peak per counts row against 64 bytes per row on the
wire, with room for a narrower row and a denser cell linker.
