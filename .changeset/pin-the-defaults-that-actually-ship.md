---
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

Pin the reading defaults that actually ship, and collect the generator tests.

The five numbers that decide verdicts — the minimum count, the cutoff, the fewest voting cells, the
panel-member floor and the fitted-baseline cell floor — were written independently in three places. The
Python copies were pinned by tests and the Tengo copies were pinned by a Tengo test; the model's copies
were pinned by nothing. That is the wrong one to leave loose: `verdict-args.lib.tengo` adds `--floor`,
`--cutoff`, `--min-voters`, `--panel-min-members` and `--distribution-min-cells` unconditionally,
substituting the model's value wherever the stored one is undefined, so on any workflow-driven run the
model's copy is the number in force and the Python's argparse default never governs. A cutoff of 90 in
the model would have scored every run at 90 and passed the whole suite.

The five are now one exported `VERDICT_DEFAULTS` map, and `qcDefaults.test.ts` asserts each value
against the Tengo file and against the Python module that owns it — the device it already applied to the
nine QC lines, which are the numbers nobody tunes.

A run with no `--cutoff` is now asserted through the CLI as well as through the module constant. Every
count in the acceptance beds scored either about zero or about one hundred, so the cutoff had no
reachable neighbourhood and a bed could not tell 50 from 75 from 90. Two counts either side of it —
scoring 69.02 and 79.36 against the beds' comparator — bracket the entrypoint's default to the band it
belongs in.

`software/test-data/manual/tests` is now collected. It had never run: `testpaths` named only the package
test directory, and among the 43 tests it holds is the one place the specificity score's published
values are pinned. Collecting it needed importlib import mode, because both test roots carry a
`test_panel.py` and the default import mode names a module by its basename alone.
