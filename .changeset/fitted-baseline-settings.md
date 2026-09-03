---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration.test': patch
'@platforma-open/milaboratories.feature-integration': minor
---

The fitted baseline states where it starts, and the scientist can move it

Two settings appear under the fitted baseline, and nowhere else — neither reaches the declared or
panel rung, so neither is offered there.

**Expected binder %.** Roughly what share of cells are expected to bind an antigen. The fit splits the
counts at the matching quantile and seeds one component from each side. It is not a threshold: the EM
re-estimates both components from there, so the split the run ends up with is an output of the fit.

It changes answers anyway, because the EM is not globally convergent on these distributions and the
start decides which optimum it reaches. On a panel where 27% of cells really did bind, the shipped
value put the split at 953 counts; told 30%, the same fit put it at 13 — which is where the gap in that
tag's histogram actually is. The published value comes from a rare-binder regime, and the study behind
this rung never tested a positive fraction above 25%.

The trade runs one way, so no single value is right: raising it also makes the fit readier to carve a
signal component out of a single population, so a tag that bound nothing invents more binders. Only
the scientist knows which side of that to be on, which is why it is a setting.

**Bound probability.** How sure the fit must be before a cell counts as bound. Previously fixed at
0.9 with no way to see or move it. Now shown, with 0.9 as both the default and the lowest accepted
value — below it a cell holding none of a tag could cross the line, and the run counts those cells by
arithmetic rather than reading each one, so the two halves would disagree with nothing raised.

**The fit now starts where the method says.** The split was taken at the median, which
`what-plays-the-baseline` never specified. A median start begins from two halves of equal size, which
is far from the truth on a mostly-background population — every tag here — and pulls the fit toward
calling much of that background signal. On a control reagent, whose counts hold one population, a
median start gives a background weight near 0.8 against 0.95 from the published split.

That trade is not free, and the direction is recorded in the suite: on a background whose long tail
puts its mean above the binders', the published start decomposes the counts into the bulk and the
tail rather than into background and binders, and calls the tail the signal. The median start got that
shape right and the mostly-background case wrong instead. Neither wins both. The run gives no warning
in either case, which is why the fitted grid puts both means in front of the reader.
