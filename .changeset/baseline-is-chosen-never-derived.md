---
'@platforma-open/milaboratories.feature-integration.model': major
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': major
---

The baseline is the scientist's choice. The block no longer picks one.

`what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either. The block derived it in two layers. Neither derives now.

**This changes what an existing project computes.** A project that never touched the baseline field was silently answered under a derived rung — the declared tag where one existed, else the panel's own readings. It is now answered under the bottom rung: no baseline, and every verdict that needs one reads *unreliable*. The settings page says so in a warning while the field is unchosen. Choosing the rung that was being derived restores the previous numbers exactly.

An unselected run is not refused. Refusing to start would be the block deciding a scientist's methodology by withholding the run, which is the same act as choosing one for them. It completes, and the run record carries both what was asked for and what served.

"No baseline" is now on the list. It was withheld on the reasoning that nobody would choose a run with no answers, which was right about the consequence and wrong about the status: it is a position held in print, by scientists who argue that a tag declared to be bound by nothing is not truly negative and that a reference chosen that way lends false confidence. On that view the absence is a design choice rather than an omission.

A rung that stops being serviceable is still never swapped for another. The run reports no baseline and records the request beside it.
