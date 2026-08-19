---
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Clarify Settings tooltips. Each optional field now leads with when to use it (or to leave it blank), and the control, sample-column, off-target, combine-mode, dominance, and min-UMI descriptions are tightened for readability.

The Tag-feature CSV tooltip drops its closing sentence, which pointed at the two labelled fields directly below it, so the tooltip is short enough to stay on screen. The Barcode sequence column tooltip now says the `FEATURE` tag captures the barcode on Read 2 and names Read 2 as the second read of each pair, because the assembled pattern also holds a sibling group called `R2` and "the `FEATURE` capture on Read 2" could be read as containment.
