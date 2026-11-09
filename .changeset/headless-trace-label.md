---
"@platforma-open/milaboratories.feature-integration.model": patch
"@platforma-open/milaboratories.feature-integration.ui": patch
---

Resolve the subtitle label in the UI instead of a block output

The sidebar subtitle is the picked antigen barcode FASTQ dataset's label, resolved by the
block's UI from the result pool instead of being copied from a block output, and written
only when it changes. Existing projects keep their subtitle. The trace label was already independent of the UI and is unchanged.
