---
"@platforma-open/milaboratories.feature-integration.model": patch
"@platforma-open/milaboratories.feature-integration.ui": patch
---

Compute the block subtitle without opening the block

The sidebar subtitle is the picked antigen barcode FASTQ dataset's label, stored when the
user picks the dataset instead of being copied from a block output each time the block is
opened. Opening the block no longer writes block data. Existing projects keep their
subtitle. The trace label was already independent of the UI and is unchanged.
