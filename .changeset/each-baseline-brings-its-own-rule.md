---
'@platforma-open/milaboratories.feature-integration.model': major
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.workflow': major
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': major
---

The tag-distribution baseline brings its own rule, and it is the one its method was published with.

`what-plays-the-baseline` fixes that rule: on the raw counts, drop the counts above the 99th percentile, fit a two-component negative binomial mixture, label the higher-median component the signal one, and give each cell the probability that its count belongs to it. A cell reads bound at 0.9 or above. `count-becomes-a-state` is explicit that the score and cutoff of the declared rung do not apply — each baseline brings its own rule, a run selects one baseline, so exactly one rule calls the state.

**This changes every verdict on a distribution-rung run.** The rung previously fitted a kernel density over log2 counts, split it at the deepest trough, took the median of the background as a comparator count, and then handed that count to the declared rung's score. The published method it followed never defines that number, and no run was reading the rule the rung was validated with.

The separation test goes with it, and that is also the spec's instruction rather than an omission. The old fit rejected a tag whose two components did not stand far enough apart, on a threshold this block invented. `what-plays-the-baseline` refuses exactly that: the method assumes two components exist, a tag that bound nothing will be split anyway and its upper slice called signal, no published test replaces the eye, and "a test invented here would be this corpus doing the thing it refuses everywhere else". So the run shows the fit instead of judging it. **A tag nothing bound can now report bound cells.**

`Maximum dip height` is removed from the settings, along with `--distribution-separation`, because a mixture has no trough to measure. The 300-cell condition stays.

`referenceCount` is now null on every distribution-rung row. That rung's comparator is a fitted distribution rather than a reading, and a number there would read as a comparator that served.
