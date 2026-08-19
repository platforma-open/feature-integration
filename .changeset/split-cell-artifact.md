---
'@platforma-open/milaboratories.feature-integration.workflow': patch
---

The verdict stage's cell artifact is split, so that no frame leaving the block carries a column keyed on `(sample, cell, tag)`.

One frame previously bundled two differently-keyed columns and exported the pair: the per-cell, per-tag counts, and the per-cell scalars — the reference reading and whether a declared gate set the cell aside. Only the second belongs outside. The per-cell per-tag states stay inside the block, because labelling and lead selection read verdicts and never cells, so exporting them shipped the run's largest artifact across the boundary to a consumer that does not exist. The per-cell reference readings are the opposite case: the block is required to report the cells carrying a high reference reading whether or not a gate is declared, and which of them a declared gate set aside.

**The per-cell per-tag counts are no longer imported at all**, not merely un-exported. Nothing read them on either side of the boundary, so importing them built the run's biggest p-frame for no reader on every verdict run — their grain is cell × tag, which on a realistic panel is 11-20× the rows of the sparse reads they derive from. The Python is untouched: it still writes the counts table and the exec template still collects it, so the states are computed and exist within the run. What stops is turning them into p-columns.

**Renamed output:** `antigenCellTable` → `antigenCellReference`. What remains in the frame is per-cell reference readings and gate outcomes, so the old name described a shape the frame no longer has.

Renaming an output is breaking for any downstream consumer, which is why this is worth checking rather than taking on faith — but there are none, so this ships as a patch. The claim is verifiable in one command: `git grep -n -E "antigenCellTable|cellCounts|cellScalars"` over `model/src`, `ui/src` and `workflow/src` returns hits only inside the workflow that builds them. Neither the model nor the UI reads the frame; the model's `perCellTable` output resolves a different, identically-named workflow output and is unaffected. The punchcard, verdicts, QC and panel-mismatch frames are unchanged.
