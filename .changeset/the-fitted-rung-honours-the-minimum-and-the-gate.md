---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

The fitted rung honours the minimum count and the gate, and its unfitted positions read *unreliable*.

**A count below the minimum is not evidence on this rung either.** `read_states` branched on the fitted
probabilities before it read the count, so the state came from the probability alone and the floored
reading was emitted beside it without ever being consulted. A weak reagent — a signal population around
five counts beside a near-silent background — fits a distribution that calls a count of three bound with
near certainty, so with the shipped floor of four a position could read *bound* on a row carrying a
count of zero. The fit is still taken over the raw counts, which is what the rung is specified on; what
changed is that each cell is scored on the reading the minimum left it, which is how a floored count
contributes everywhere else. A declared baseline tag stays exempt, as it is in `apply_floor`.

**A silent position in a sample the rung could not fit reads *unreliable*.** The punchcard corrected a
silent position only through a per-(sample, identity) comparator, which nothing in production sets — the
fitted rung's comparator is keyed per (sample, cell, identity) and had no branch, so every such position
fell through to the not-bound default. A clonotype's cells in a sample below the 300-cell floor were
counted as unreliable in the verdict and drawn as *not bound* on the card beneath it.

**A declared admissibility gate acts under every rung.** The gate reads a declared baseline tag and the
comparator is whatever rung was selected; they are separate roles and which rung serves must not reach
the gate. The fitted rung handed `gate_cells` an empty reading map, so a stored threshold set nothing
aside and reported nothing from the moment a scientist switched the baseline source. The gate's readings
are now built wherever the panel declares a baseline tag, and the exposure count is withheld on the
absence of readings rather than on the rung.

**Two declared baseline tags serve together, by the highest of them.** `baseline-scope` combines
replicates within one group that way, and with no scope construct nothing declared separates two
references, so the whole panel is one group. Refusing the panel sent the scientist back to edit a file
over a case the corpus had already settled. Taking the highest is also what stops a dead reference from
making the background look cleaner than it was.

**The gate sets aside a cell above its threshold, not at it**, and `silent_tally` refuses a
position-keyed comparator on its sample-keyed path rather than hoisting a term that is not a fact about
the cell.
