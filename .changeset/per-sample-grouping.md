---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
---

Group tags into identities per tag and sample. The panel file declares what a tag carries in each sample, so a barcode reused across panels now resolves to the antigen its own sample declared instead of standing alone under its raw sequence. On a per-sample panel the punchcard renders identities across rather than tags across, and each cell's reading combines only the tags its own sample offered.
