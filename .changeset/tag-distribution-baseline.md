---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

Add the tag-distribution baseline, and build every baseline from raw counts.

A run with no declared control tag can now read each count against that tag's own distribution across the sample's cells, split into two components. It serves where the sample holds at least 300 cells and the tag's counts actually separate; a tag that does not separate reports no comparator rather than an invented one, and only the identities built from that tag are affected. Both conditions are settings on the CLI (`--distribution-min-cells`, `--distribution-separation`) and both are recorded in the run record, alongside a per-tag list of what could not be fitted.

This is the first comparator that varies by identity rather than by cell, so the shared admissibility bundle now carries the identity a comparison is being made about.

Separately, and independent of the new rung: every comparator is now computed from the raw counts rather than the floored ones. The minimum count applies to the reading being judged, never to what it is judged against — and because reference tags are exempt from the minimum and antigen tags are not, the panel comparator was previously a median taken over a mixture of raw and floored values. Panel comparators rise, so fewer cells read *bound*.
