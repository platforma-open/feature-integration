---
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Lower the per-sample mitool memory floor from 64 GiB to 16 GiB. The 64 GiB floor was applied on every run regardless of input size, and mitool's memory-from-limits launcher turns the grant into a JVM with `-Xms` = 50% of it — a ~32 GiB initial heap even for tiny datasets, which swaps on typical desktop RAM and stalls the "parsing reads" step. The `size("reads")*4` term still scales large inputs up (cap 256 GiB), so only small runs are affected. Also lower the per-sample mitool CPU default from 16 to 8 (matching peptide-extraction; 16 exceeded the core count on typical desktop machines) and fix the "mitool CPUs per sample" tooltip, which stated the default was 4.
