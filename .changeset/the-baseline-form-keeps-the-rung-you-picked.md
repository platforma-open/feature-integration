---
'@platforma-open/milaboratories.feature-integration.model': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.per-cell-metrics': patch
---

The baseline form keeps the rung you picked, and Run quality shows only what this run can draw.

**Naming the role column no longer un-picks the baseline source.** Picking *Declared baseline tag* and then naming a role column cleared the baseline source, which hid the role column, the baseline value and the admissibility gate — the three fields the rung had just asked for. Naming the baseline value did the same thing again. Both gestures cleared `referenceSource` on the reasoning that a choice must not outlive the declaration it was made against, but the form works the other way round: the scientist picks the rung first, and the form then asks for what that rung needs. So the answer un-asked the question, and the value stayed in the data while the field showed nothing — which is why re-opening the source dropdown showed the role column set correctly.

Neither gesture touches the baseline source now. A new panel file still clears the role column and the baseline value, because those name columns and values of the file that was replaced.

**The two baseline-source descriptions are shorter and read one instruction at a time.** No fact left them. The declared rung's missing-requirement note is two sentences instead of one carrying two steps. The population rung's description no longer ends by pointing at the retired panel-size option.

**Run quality gives a plot no tab where the run's baseline cannot produce it.** A run read against each tag's own distribution offered Scores and Reference readings, and a run read against a declared baseline tag offered Fitted background; each opened onto a paragraph explaining why it was empty. Those three tabs now appear only on the runs that can draw them. While a run has not yet reported which rung served it, all three are offered, because the rung is unknown at that point rather than known to be wrong.

**Run quality shows the processing placeholder while the run is in flight, on the grids and on the plots.** Each of the four grids sat behind an alert saying its table had not arrived, and each of the three plots behind one saying its distributions had not arrived — which during a run reads as a finished run with nothing in it. The grids are now drawn straight away and answer all three states themselves: the processing placeholder while the model is unstable, a stated reason once the run settles with no frame, and the empty-table wording for a frame that arrived with no rows. The plots draw the same placeholder in its graph variant while the distributions are unstable, and keep a stated reason for a settled run that produced none. An errored output reaches the grid or the plot either way, which renders the error it was handed.

**The bound cutoff and the agreement limit share a row.** Both are conditions on one verdict — the cutoff decides what a single cell says, the agreement limit how many cells must say it — and they sat on separate lines. Under the declared rung they are now side by side; where the cutoff does not apply, the agreement field takes the whole row rather than half of one.

**Every *Minimum* under Threshold Parameters is now *Min*.** Paired on one row, the full word pushed each label past the field it names.

**Two surfaces come out.** The *Not measured, and why* list under the measurement table restated four method-level exclusions that hold on every run and never change; they stay recorded beside the measurements they sit next to in the software. The run-level progress bar above the sample grid restated the percent and step that the grid's own Progress column already carries for each sample.

**The Aggregate-barcode detection settings come out of the form.** All three were calibrated as a set — moving one changes what the aggregate-barcode quality line is judging, and no published line covers the moved position. The parameters and their defaults are unchanged, so a stored project keeps whatever it set and a new one runs on the shipped constants.

## Also on this branch

Four earlier changes that shipped no changeset of their own, released here.

**The aggregate-barcode figure reaches the combined QC summary.** The three aggregate fields were computed per sample and then dropped in transit, because the summary's column list did not carry them. The reading that follows fell to "this sample's read QC reports no reads, so the share has no denominator" — false on a sample whose reads number in the millions. The column list now carries the three fields, and the reading has a third branch for a genuinely absent denominator.

**The distribution rung says what it checks, and what it does not.** Nothing checks that a tag's two components stand apart, and the rung's own description said otherwise.

**The cell gate's own limit reaches the run record**, so the export gates on the readout name the figure they stopped at rather than only that a limit exists.

**A barcode's own number no longer flags its sample.** The undeclared-barcode share is a property of the barcode, and its status was reaching the samples it was measured on.

Plus the entity cell reading its parameters where ag-grid puts them, the quality-line settings named the way the rest are, and model comments carrying fact and constraint without rationale.
