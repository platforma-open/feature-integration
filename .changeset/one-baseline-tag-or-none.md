---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration': minor
---

Read counts against one declared baseline tag, or none. Several are refused, not combined.

The block used to take the highest reading among several declared baseline tags. `baseline-scope` states that references are never combined, and taking the highest is a combination — so a panel declaring several no longer gets a comparator nobody specified.

Reading against several is **deferred to a later version**, not dropped. That atom builds the reference as a grouping over a declared panel property: each reference serves the group its declaration scopes it to. This block has no group-by half — a tag is a comparator for the whole panel or for none of it — so it cannot say which antigens a second comparator belongs to. Refusing is also what the field does: the ordinary antibody run rejects a second control outright, and the T-cell run requires one control per allele and rejects two.

The run stops and names the tags it found, rather than falling silently to no comparator. This is a panel a scientist fixes in a minute, and a silent fall to *unreliable* everywhere would not tell them how.

Scoped to the rung that reads a declared tag **as** the comparator. Under the panel rung and the tag-distribution rung, several declared tags are unambiguous — the first treats them as ordinary readings in its median, the second never sees the role at all — so those runs are unaffected.

One role value can mark several tags, so this is a limit on the tags found, not on how many values are picked. The settings tooltip says so.
