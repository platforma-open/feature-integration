---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': minor
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Carry a third number with every clonotype: how many of its cells were left with no count on any tag.

`support-travels-with-the-reading` asks for it beside the two counts a verdict already ships — how many of the clonotype's cells could have answered at an identity, and how many did. This one is a property of the cell rather than of a position, so it is counted once for the clonotype: a cell with nothing left is empty at every identity, and repeating the subtraction per position would report a per-identity failure that did not happen.

**It changes no verdict.** Those cells vote *not bound* like any other. What it carries is whether a negative rests on cells that read something or on cells that read nothing.

- **The baseline is part of the test, and that is the whole discriminator.** A cell whose antigen tags all fell below the minimum count while its baseline reading survived took up reagent and none of it was antigen — a real negative and a real vote. Only a cell with nothing anywhere read nothing. The existing per-sample `cellsEmptied` counter cannot see this: while the baseline is exempt from the minimum, that counter is scoped to the readings the minimum was allowed to remove, so the baseline is invisible to it. This is a new tally rather than a rename.
- **`Cells that read nothing` ships off by default**, in the table's column chooser, ordered next to `Cells` because it qualifies it — forty cells of which thirty-eight read nothing is a different clonotype from forty that all read something.
- **Turning `Apply the minimum count to the baseline` on moves this number**, and moves nothing else. The verdicts, the per-cell counts and the per-cell scalars are byte-identical across that switch, which is now pinned by a test.
- An emptied cell stays in the clonotype's cell count and stays in the vote. Dropping such cells would shrink the denominator and make verdicts more positive, and filtering them from the cell list is the same effect by another route.

Still to come: the per-sample alert for a run carrying many such cells, which fires whether or not a reader turned the column on. Where it lives and what counts as "many" are open.
