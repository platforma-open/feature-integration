---
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

The tag-distribution baseline is fitted over the sample's cells, and every verdict it served changes.

`what-plays-the-baseline` states the third rung as "that tag's own distribution across the sample's
cells". It was fitted over every observed barcode instead — the cell list unioned with the barcodes the
reads touched. In droplet data those outnumber the cells by one to two orders of magnitude, because
ambient reads land on most barcodes, so the population a background was estimated from was mostly empty
droplets. On the run this was found against, the fit ran over 2,633,996 barcodes for 25,032 cells.

Both components then land on that ambient mass. Every tag in that run reported a background sitting on
top of its own signal — the two means equal to three decimal places, on tags whose counts separate
cleanly when plotted. `what-plays-the-baseline` names this as the rung's one known failure, where a tag
that bound almost nothing has one population and the fit splits it anyway; fitting over barcodes puts
every tag in that state at once.

**Verdicts served by this rung change, and most of them change from bound to not bound.** A background
of a fraction of a count is cleared by almost any reading. On the run this was found against, bound
calls fall by roughly three quarters. Runs served by a declared reference tag are unaffected: that rung
fits nothing.

The fit still runs over every listed cell including the ones the admissibility gate later sets aside,
which is `baseline-over-all-returned-cells` and why it runs before the gate. That atom forbids the gate
narrowing this population; it does not widen it past the cells. A run that supplied no cell list keeps
the barcode union, because membership is unknown there rather than false, and `cellListSource` in the
run record says which case a run's verdicts were read under.

Two components that converge onto each other end the fit, as they were always meant to. The check
compared them with `==`, and two floats from separate reductions land on the same value only by luck, so
a converged pair went on to report itself as two populations. It is a relative tolerance now. This is a
numerical guard on the fit and not a test of whether a tag's counts separated — no such test exists, and
none is invented here.

Each panel of the fitted-background grid now carries the fit's own three numbers: the background mean,
the signal mean, and the share of cells the background component holds. `what-plays-the-baseline` makes
that panel the substitute for the check nobody has built — "the run shows the fit instead of judging it"
— and it cannot do that job while the fit's output is withheld from it.
