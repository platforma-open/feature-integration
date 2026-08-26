---
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
---

The support pair names the set it counts, an antigen cannot be dropped by an id collision, and the
aggregate-barcode knobs reach a command line.

**`cellsCouldAnswer` carried the cells the question was put to.** `four-state-verdict` names two sets
and keeps them apart: the cells a question was *put to* is a fact about the experiment, and the cells
that *could answer* is that set narrowed by what the data and the settings allow. The column labelled
*Cells that could answer* held the first. With a gate setting seven of ten cells aside, a clonotype whose
three survivors all read bound reported ten — so a scientist reading the spec's pair saw three of ten and
inferred seven negatives that never existed. With no gate declared and a comparator for every cell the
two numbers agree, which is why it went unnoticed.

`pl7.app/antigen/cellsCouldAnswer` is now `pl7.app/antigen/cellsAsked`, labelled *Cells the question was
put to*. `pl7.app/antigen/cellsAnswered` keeps its name and is labelled *Cells that could answer* — every
cell that could answer did, so it is one set under two true descriptions, and it is the number the vote
limit acts on. The punch value's field order is unchanged, so a project stored before this still decodes.
**A consumer reading `pl7.app/antigen/cellsCouldAnswer` must move to `cellsAnswered`, not to
`cellsAsked`.**

The punch tooltip now shows both counts wherever they differ, not only where the run carried panels that
differ, and shows how many cells read bound — which is the whole split where a tie or a refused majority
leaves no agreement figure.

**An identity id collision could drop an antigen silently.** Column ids ran through
`substituteSpecialCharacters`, which collapses every run of punctuation to a single `_`, so `SARS-CoV-2`,
`SARS CoV 2` and `SARS.CoV.2` produced one id — and the importer writes ids into a map with no duplicate
check, so the second column overwrote the first and an antigen left the answer with no error raised
anywhere. Unreachable under the shipped per-tag grouping, where identities are barcodes; reachable under
a property grouping, where they are the scientist's own antigen names. Ids are disambiguated by position
now, deterministically, and the column header is still the identity itself.

**Every parameter travels with the verdicts.** Only the minimum count and the cutoff were carried, on the
verdict column alone. The gate and the agreement floor decide *which verdicts exist*, and two runs
differing on them emitted columns of identical identity and identical annotations, with the record only
in a block-local output that labelling and lead selection never see. All of them now ride the verdicts,
the set counts and the exported identity pivot, with an unset parameter carrying no note rather than a
zero.

**The three aggregate-barcode knobs reach a command line.** `fb-pipeline` never passed them to
`fb-downstream`, which is written to read exactly those three, so the guards there could never fire and
`qc_report.py`'s own defaults always applied. They still travelled in the per-sample body's identity, so
moving one re-ran parse, refine-tags and tag-stat for every sample and changed no number. A test now
asserts that fb-pipeline hands fb-downstream everything it reads.

**`argsValid` bounds three parameters it did not.** An agreement floor at or below half can never fire,
since a majority is above half by construction, so the run recorded a limit that did nothing. A
fitted-baseline cell condition below one has no population. A gate stored as a fraction rounded to zero on
projection and reached the workflow as *off* while the settings field still showed a number.
