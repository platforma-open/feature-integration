---
'@platforma-open/milaboratories.feature-integration.model': major
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
---

The minimum count never reaches the baseline tag, and the setting that could make it is gone.

`minimum-count-before-any-reference` states it as a rule rather than a preference: the minimum asks whether a count is evidence of binding, a tag declared to be bound by nothing is never evidence of binding, so the question does not arise for it. A cell's reference reading enters the comparison as it came back, and a small one is the measurement rather than noise to be cleared away.

"Apply the minimum count to the baseline tag" is removed, along with `--minimum-applies-to-baseline` and the `minimumAppliesToBaseline` field. The exemption is now unconditional in `apply_floor`.

**No verdict changes.** The setting was off by default and every rung already built its comparator from raw counts, so the default behaviour is what shipped. What changes is that the other behaviour can no longer be selected. A stored project that had switched it on now runs with the exemption in force, and reports fewer removed readings and fewer emptied cells than it did.
