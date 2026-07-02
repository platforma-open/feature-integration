---
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Remove the cell-barcode whitelist selector from the UI; Feature Integration is de-novo only for v1.
The selector was not spec-required, de-novo already yields the ~99% cross-block join empirically, and
only the 5' v2 option was verified while the others carried real footguns (the whitelist and UMI-length
fields could disagree, and the VDJ-side alignment for non-5'v2 chemistries was unconfirmed). Aligning
cell barcodes deterministically across producers is a chain-level concern (chemistry belongs to the
dataset, set once upstream) to revisit once the downstream join can be verified end to end. The
`#builtin:` workflow/model plumbing is kept dormant (`cellWhitelist` stays `""`) as a documented seam.
