---
'@platforma-open/milaboratories.feature-integration.ui': minor
---

One value marks the baseline tag, not several.

"Values that mark the baseline tag" was a multi-select. It is now a single-select, "Value that marks the baseline tag".

`040-glossary` splits the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference is a job given to one of them, declared with the run. So the value that nominates the baseline is singular.

Several values only ever described a panel whose role column spells one role more than one way. A panel that does that is asking to be corrected, not accommodated.

`referenceValues` stays a list in the block's data, so the `--reference-values` flag and every stored project keep their shape. A project that had picked several values keeps them until the field is next touched, and the run still refuses if they mark more than one tag. That refusal is unchanged and is now the only one that can fire: one value can still mark several tags, which is a panel fact the control cannot see.
