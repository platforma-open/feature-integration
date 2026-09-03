# @platforma-open/milaboratories.feature-integration.ui

## 2.4.0

### Minor Changes

- e873489: The fitted background draws the distribution the run holds

  The fitted-background grid is the only way to see whether a tag's counts separated into two
  populations, so a hump it invents, or one it hides, is the error this surface cannot carry. Three
  things were wrong with it.

  **The zeros were missing.** The plot was binned from the sparse counts frame, which has no rows for
  cells that read nothing. Those zeros are most of the background, so the plot showed one decaying hump
  whatever the fit had found — the left half of the distribution was simply absent. It is now binned over
  the cells the fit actually ran on, one entry per cell in the sample, and a cell that read nothing counts
  as a zero.

  **Every bar is now the same width.** Bins sit at `expm1(k * 0.2)`, uniform in `log1p`, which is what
  the plot's axis already is. The source paper histograms `log1p` counts at a fixed width for the same
  reason.

  Before, bins were whole numbers stepping geometrically, so their widths ran from 0.301 of a decade at
  `[0, 1)` down to 0.079 at `[4, 5)`. A raw count then made a wide bar stand above a narrow one holding
  the same density, so each bar had to be divided by its own width — and dividing by the wrong width hid
  a real signal component completely: on a mixture whose upper mode was cleanly separated at a mean of
  60, that hump drew at 0.9% of the background peak. Equal widths remove the division and the error with
  it. Bar height is a plain cell count again, so the hover readout reports a cell count.

  The cost of equal widths is that the edges are no longer whole numbers, and counts are — consecutive
  integers sit further apart than one bin until about count 13, so the low end is a comb of separated
  bars. The paper's own figures show the same gaps. `LOG1P_BIN_WIDTH` is coarser than the paper's 0.075
  for that reason: at 0.075 a real tag came back with 75 of 97 bins empty.

  **The bound line is drawn.** Under a fitted baseline the threshold is a probability, so a plot in counts
  had nothing to mark it against. Each fit now resolves the count at which the run's bound probability
  starts calling a cell bound, and the panel draws it. A fit that reaches no such count draws no line and
  says so, rather than marking one at the bottom.

  Each (sample, tag) is binned against its own range, so the emitted weight lists have different lengths
  and are shorter than the shared edge set. The plot pads them, which draws the same picture — past a
  pair's own maximum every bar is empty either way. Binning inside the fit rather than returning per-cell
  arrays takes the fitting step from 2.1 million numbers held to 7,764 on a 27-sample run.

- e873489: The fitted baseline states where it starts, and the scientist can move it

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

- e873489: The fitted-background grid reads one sample at a time

  The fit runs per (sample, tag), so the grid drew one panel per pair — 27 samples and 9 barcodes is 243
  panels on one page, and every title had to repeat the sample to tell them apart. It now shows one
  sample, chosen from a selector above it, so a panel is titled by its reagent alone and the sample is
  named once. Barcodes read in the order the panel file declares them, so a barcode holds the same slot
  whichever sample is shown.

  What that gives up is reading down one reagent across samples. The grid still supports that shape;
  nothing asks for it today.

  Each panel's caption now carries the bound count alone, and the fit's own numbers moved to the enlarged
  panel, where a value is read rather than scanned. The cell count that used to lead the caption was the
  sample's analysed population — the same number on every panel of the sample — and now sits once above
  the grid.

  **The Panel column is out of the tables.** It is a hash of the sorted barcode list, because no panel
  file names its panel, and a run declaring one panel for every sample repeated that hash identically on
  every row. It stays available in the column picker, and a multi-panel run should switch it on:
  `Seen in 2/3` cannot be read without knowing which three samples.

  **Fixes**

  - A fitted background mean of 0.000488 printed as `0`, a value the fit cannot produce — three
    significant figures were computed and then discarded by a formatter keeping three decimal places.
  - The Run quality page failed to render at all: a watch read a value declared further down the file.
  - A run computed before the bound count existed reported "no count reaches the bound probability",
    stating a finding no run had produced. Absent and null now read differently.
  - Resizing the window redrew every panel on every frame, and each redraw leaks a tooltip node in the
    uikit. A few pixels of tolerance takes a drag from hundreds of redraws to a handful. The leak itself
    is the uikit's.
  - The quality-report JSON was pretty-printed, which roughly doubled it for a file only the UI reads.

### Patch Changes

- b0818af: Run quality hides its tab strip while the run computes

  The tab set is derived from the baseline rung the run reports. Until the run
  settles that rung is unknown, so the strip offered every plot and then dropped
  the ones the served rung cannot draw — a reader could open a tab that then
  stopped existing.

  The strip is now hidden while the block computes. The open view's body keeps
  rendering and draws its own processing placeholder, so the page still shows the
  run's progress.

- Updated dependencies [e873489]
- Updated dependencies [e873489]
- Updated dependencies [e873489]
- Updated dependencies [3af02c5]
  - @platforma-open/milaboratories.feature-integration.model@3.1.0

## 2.3.3

### Patch Changes

- Updated dependencies [82a0c86]
- Updated dependencies [1791309]
  - @platforma-open/milaboratories.feature-integration.model@3.0.3

## 2.3.2

### Patch Changes

- fd74062: The block's description says what the block does.

  It still described the shipped feature-integration block: which antigen each cell bound "and how
  strongly", the dominant (consensus) antigen, and a per-antigen specificity score. The block emits none of
  those, and two of them are refused outright — `verdict-at-every-identity` forbids a dominant antigen
  standing in for the set, and `binary-narrowing` forbids a magnitude leaving as the answer. The catalogue
  entry promised a reader exactly the two things they cannot have, and said nothing about the four-state
  verdict that replaced them.

  Four smaller corrections. The punch legend said an unsettled reading has _seven_ ways to fail and the
  tooltip said six; there are five, the sixth reason belonging to _never asked_. The per-sample count
  distribution was labelled as a median and described as deciles. The model cited a `resolve_default_source`
  in `verdict.py` that deliberately does not exist and that a test pins as absent. And the workflow's
  no-dataset branch carried a comment describing a run the model refuses.

- 40844b3: The reagent table counts cells, and an unreadable sibling does not vote.

  **The per-tag figures are scoped to the cell list.** How many cells held any count of a tag, how many
  it called bound, and the median over the cells holding one were taken over every observed barcode.
  Ambient reagent reaches most barcodes, so observed barcodes outnumber cells by one to two orders of
  magnitude while carrying one or two counts each — and the median a reagent delivers is how this table
  reports a reagent working under the level at which anything is credited. On a real run every tag in the
  panel read as a failed reagent. The figures now say which cell list they were computed against, since
  two runs whose lists came from different sources do not share a denominator.

  **A sibling with no settled reading no longer votes.** The rule is to put every tag of an identity in
  bound or not bound on its own count, and a cell whose reading was gated or had no comparator is in
  neither. Counting _unreliable_ as a state let it form a sibling majority, so a tag that fitted where its
  siblings did not read as differing from all of them — reported as the panel's worst reagent, and its
  only working one in fact. With a gate on, the same bug diluted every real rate by the share of cells the
  gate set aside.

  **A fraction with no denominator prints blank rather than zero.** The matched-read fraction of a sample
  whose reads never arrived, and the median UMIs per cell of a sample holding no cell, both printed
  `0.00` — beside a neighbour's `0.98` that reads as a library that failed rather than one that is
  missing.

  **Two QC descriptions said the wrong denominator.** The usable-antigen-read fraction and the
  aggregate-barcode read fraction are both taken over the sample's total reads, and both column
  descriptions said _over reads matched_; the first also asserted a UMI-validity condition it does not
  carry. Both are measurements with published alert lines, so a reader hovering the column that alerted
  was told to divide by the wrong number.

  **The sample report shows each measurement's detail.** It was declared and populated and never
  rendered — which is what made the sticky measurement unreadable, since a count of cells above a declared
  gate and the median of the readings where none is declared arrive in the same column and only the
  detail says which. Every distribution-shaped measurement's deciles ride there too.

- ec31fb9: The fitted rung labels its signal component by median, the gate sets aside above its line, and a
  sticky count is not reported over no readings.

  **The signal component is the higher-median one.** It was the higher-mean one, justified by a comment
  saying a negative binomial's medians are ordered by its means. They are not: the median depends on the
  size as much as the mean, at mean 50 and size 0.05 it is 0 while at mean 5 and size 1e6 it is 5, and
  sizes are re-estimated per component from that component's own variance every round. An ambient
  population — mostly zero with a few enormous counts — fits a component whose mean sits far above a real
  binder population's while its median sits far below, so the two orderings pick opposite components.
  Labelling the wrong one inverts the tag: on the bed now committed, ordering by mean calls four hundred
  cells that read nothing bound, at a probability of exactly one. Where both medians are zero, which is
  the mostly-silent case, the mean breaks the tie.

  **The admissibility gate sets aside a cell above its threshold, not at it.** `reference-two-roles` says
  above, and says a cell is set aside where a reading exceeds the threshold — the same direction the
  minimum takes from the other side, where a count of four survives a minimum of four. The code, its
  CLI help, the QC column and the settings tooltip all said _reaches_. A cell reading exactly the gate
  value now stays in and answers.

  **Neither form of the sticky measurement reports a number over no readings.** Both are taken over the
  cells' own baseline readings, which only a declared baseline tag supplies. Under the tag-distribution
  rung no cell has one, and a gated count over an empty population came out `0.0` — a sample reported as
  checked and clean on a question the run never put, and disagreeing with the run record, which already
  reported nothing for the same condition.

- 817a04b: The support pair names the set it counts, an antigen cannot be dropped by an id collision, and the
  aggregate-barcode knobs reach a command line.

  **`cellsCouldAnswer` carried the cells the question was put to.** `four-state-verdict` names two sets
  and keeps them apart: the cells a question was _put to_ is a fact about the experiment, and the cells
  that _could answer_ is that set narrowed by what the data and the settings allow. The column labelled
  _Cells that could answer_ held the first. With a gate setting seven of ten cells aside, a clonotype whose
  three survivors all read bound reported ten — so a scientist reading the spec's pair saw three of ten and
  inferred seven negatives that never existed. With no gate declared and a comparator for every cell the
  two numbers agree, which is why it went unnoticed.

  `pl7.app/antigen/cellsCouldAnswer` is now `pl7.app/antigen/cellsAsked`, labelled _Cells the question was
  put to_. `pl7.app/antigen/cellsAnswered` keeps its name and is labelled _Cells that could answer_ — every
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
  verdict column alone. The gate and the agreement floor decide _which verdicts exist_, and two runs
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
  projection and reached the workflow as _off_ while the settings field still showed a number.

- Updated dependencies [acfde14]
- Updated dependencies [fd74062]
- Updated dependencies [817a04b]
  - @platforma-open/milaboratories.feature-integration.model@3.0.2

## 2.3.1

### Patch Changes

- 5ef1665: Count plots are drawn over the cell list, and each reads on the axis its number is typed on.

  The binned count distributions now count the cell list rather than every barcode the reads touched. In
  droplet data the observed barcodes outnumber the cells by one to two orders of magnitude, because ambient
  reads land on most barcodes, so an ambient population that size was the only hump any panel showed. The
  shared edge list is taken from the same filtered counts, so the axis ends at the highest count among cells.
  A run that supplied no cell list still counts every barcode — membership is then unknown rather than false
  — and `cellListSource` in the run record says which case a plot was drawn under.

  The score spread and the reference readings draw on a linear axis. Both are read against a number a
  scientist types in the same units, and both had been drawn on a log axis, which put that number where the
  reader could not find it. A declared gate of exactly zero now draws its marker; it drew none before, which
  read as no gate declared at all.

  The fitted-background grid and the sample's own count panels title on the reagent's name and sort by it,
  rather than on the barcode sequence behind it. Thumbnails in both grids draw bars only, so a small panel
  spends its width on the plot instead of on an axis gutter wider than the plot itself; an enlarged panel
  still carries its axes. The grid no longer offers a horizontal scrollbar with four pixels of travel.

  Per-sample QC draws each sample's rolled-up status as the same tag the sample list draws, and no longer as
  the bare word.

  `Cells detected` is now `Cell barcodes detected` on both surfaces that carry it. It counts distinct barcodes
  in the tag-stat table, before any cell-calling step, which is one to two orders of magnitude above the cell
  count — the measurement set already named it this way, and only the column label overstated it.

  The sample view's antigen counts per barcode are deferred, and the section is commented out with what to
  uncomment.

- 46268f1: Run quality reads by reagent, and the fitted background is a grid you can enlarge.

  The Reagents table leads with the reagent's name rather than its barcode. Every axis of that table now
  carries a label column, so no column renders a raw id: a tag reads as its name, an identity as its
  antigen, a panel as its panel. A barcode several panels name differently reads as those names joined,
  under every grouping — previously it fell back to its own sequence whenever the run grouped by a panel
  property.

  The fitted background is a grid of small multiples ordered by tag, so one reagent's samples sit side by
  side, and any panel enlarges to a dialog. Every count plot now sizes itself to its container; each drew
  at a fixed 674px before and overlapped its neighbours. The score and reference plots label their own x
  axis, which they had been passing and having ignored.

  The exported verdicts drop two columns a reader derives from the pair beside them: cells that read not
  bound is `answered - bound`, and cell agreement is that pair read against the state. Both are still
  computed, and the punchcard still carries them.

- bf00e26: The baseline form keeps the rung you picked, and Run quality shows only what this run can draw.

  **Naming the role column no longer un-picks the baseline source.** Picking _Declared baseline tag_ and then naming a role column cleared the baseline source, which hid the role column, the baseline value and the admissibility gate — the three fields the rung had just asked for. Naming the baseline value did the same thing again. Both gestures cleared `referenceSource` on the reasoning that a choice must not outlive the declaration it was made against, but the form works the other way round: the scientist picks the rung first, and the form then asks for what that rung needs. So the answer un-asked the question, and the value stayed in the data while the field showed nothing — which is why re-opening the source dropdown showed the role column set correctly.

  Neither gesture touches the baseline source now. A new panel file still clears the role column and the baseline value, because those name columns and values of the file that was replaced.

  **The two baseline-source descriptions are shorter and read one instruction at a time.** No fact left them. The declared rung's missing-requirement note is two sentences instead of one carrying two steps. The population rung's description no longer ends by pointing at the retired panel-size option.

  **Run quality gives a plot no tab where the run's baseline cannot produce it.** A run read against each tag's own distribution offered Scores and Reference readings, and a run read against a declared baseline tag offered Fitted background; each opened onto a paragraph explaining why it was empty. Those three tabs now appear only on the runs that can draw them. While a run has not yet reported which rung served it, all three are offered, because the rung is unknown at that point rather than known to be wrong.

  **Run quality shows the processing placeholder while the run is in flight, on the grids and on the plots.** Each of the four grids sat behind an alert saying its table had not arrived, and each of the three plots behind one saying its distributions had not arrived — which during a run reads as a finished run with nothing in it. The grids are now drawn straight away and answer all three states themselves: the processing placeholder while the model is unstable, a stated reason once the run settles with no frame, and the empty-table wording for a frame that arrived with no rows. The plots draw the same placeholder in its graph variant while the distributions are unstable, and keep a stated reason for a settled run that produced none. An errored output reaches the grid or the plot either way, which renders the error it was handed.

  **The bound cutoff and the agreement limit share a row.** Both are conditions on one verdict — the cutoff decides what a single cell says, the agreement limit how many cells must say it — and they sat on separate lines. Under the declared rung they are now side by side; where the cutoff does not apply, the agreement field takes the whole row rather than half of one.

  **Every _Minimum_ under Threshold Parameters is now _Min_.** Paired on one row, the full word pushed each label past the field it names.

  **Two surfaces come out.** The _Not measured, and why_ list under the measurement table restated four method-level exclusions that hold on every run and never change; they stay recorded beside the measurements they sit next to in the software. The run-level progress bar above the sample grid restated the percent and step that the grid's own Progress column already carries for each sample.

  **The Aggregate-barcode detection settings come out of the form.** All three were calibrated as a set — moving one changes what the aggregate-barcode quality line is judging, and no published line covers the moved position. The parameters and their defaults are unchanged, so a stored project keeps whatever it set and a new one runs on the shipped constants.

  ## Also on this branch

  Four earlier changes that shipped no changeset of their own, released here.

  **The aggregate-barcode figure reaches the combined QC summary.** The three aggregate fields were computed per sample and then dropped in transit, because the summary's column list did not carry them. The reading that follows fell to "this sample's read QC reports no reads, so the share has no denominator" — false on a sample whose reads number in the millions. The column list now carries the three fields, and the reading has a third branch for a genuinely absent denominator.

  **The distribution rung says what it checks, and what it does not.** Nothing checks that a tag's two components stand apart, and the rung's own description said otherwise.

  **The cell gate's own limit reaches the run record**, so the export gates on the readout name the figure they stopped at rather than only that a limit exists.

  **A barcode's own number no longer flags its sample.** The undeclared-barcode share is a property of the barcode, and its status was reaching the samples it was measured on.

  Plus the entity cell reading its parameters where ag-grid puts them, the quality-line settings named the way the rest are, and model comments carrying fact and constraint without rationale.

- 7feac4c: The tag-distribution baseline is fitted over the sample's cells, and every verdict it served changes.

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

- Updated dependencies [5ef1665]
- Updated dependencies [46268f1]
- Updated dependencies [bf00e26]
  - @platforma-open/milaboratories.feature-integration.model@3.0.1

## 2.3.0

### Minor Changes

- 32fd396: The agreement limit is a percentage, and the form's sections are named for what they hold.

  "Share of voting cells that must agree (0–1)" becomes "Voting cells that must agree (%)". A share is a number a reader has to translate, and this one has a floor most readers do not expect: agreement is measured among the cells that answered and the verdict takes the majority, so it can never fall to half or below. The field now runs from 51 to 100 and says why. The data keeps the 0–1 share, so `--min-agreement` and every stored project are unchanged.

  Its tooltip now leads with the default, which is **off**: no agreement test runs, a narrow majority stands, and the verdict reports how narrow it was.

  The tag-barcode FASTQ dataset is marked required, which `args()` has always enforced. The single-cell V(D)J dataset moves up beside it, so the two dataset inputs sit together above the panel.

  Three sections are renamed: a new **Panel Settings** header over the panel file and its columns, **Baseline (background) level** becomes **Baseline (Background) Parameters**, and **The reading** becomes **Threshold Parameters**.

- 3ad61a7: Antigen barcode binding profiling: verdicts, the baseline ladder, the quality layer and the readout.

  This release reconciles the block to the antigen-barcode binding-profiling spec. The entries below were written one change at a time and are grouped here; each keeps its own wording.

  ## Verdicts and the baseline ladder

  Antigen binding is reported as a four-state verdict per clonotype set and antigen identity: **bound**, **not bound**, **never asked**, or **unreliable**. The last two are not kinds of "not bound" — _never asked_ means the experiment did not put that antigen to those cells, and _unreliable_ means it did and the data cannot settle the result. Both are emitted as rows rather than left absent, so a reader can tell an unanswered question from a negative answer.

  An identity is a group of tags, not a feature name: the same barcode carries different names in different samples' panels, so name-keying splits one reagent and can merge two. Tags combine into an identity by the highest of their counts, and the grouping is a rule over the panel's declared properties rather than a frozen map, so the same run can be read at more than one grouping.

  **What each verdict rests on travels with it.** Every row carries how many of the set's cells could have answered at that identity, how many did, and the agreement among them — so a verdict resting on three cells is distinguishable from one resting on forty. Where an antigen read _not bound_ while something it was declared to compete with read _bound_ for the same clonotype, the row says so, and a downstream filter can test it.

  **Nothing is orderable.** No score, rank or per-antigen magnitude leaves the block, and a build-time assertion refuses any score annotation on an emitted column. A verdict is a statement about what the experiment could establish; ranking clonotypes by it is a downstream block's job, from these outputs plus other assays.

  **Quality measurements ship with the reading.** Fifteen measurements across sample, tag, identity, panel and capture levels — the spec's fourteen, with its single saturation-and-depth row shipping as two, because reads-per-cell is computable from what the parse step already reports while saturation is not, and one measurement cannot be half _not evaluated_. Each states what it counts and — only where a line can be defended — what a bad value implies. A measurement with no defensible line reads _unjudged_ rather than being given an invented threshold, and one the run could not supply inputs for reads _not evaluated_ with its reason. Every level reports coverage beside its status, so a run states both what is wrong and how much of it was actually checked. Sample and panel roll up as separate axes: a bad sample is prepared again, a bad reagent is replaced.

  **The panel's declarations travel with the verdicts.** Whatever the panel file says consistently about an identity's tags is exported beside that identity's readings — role, species, carrier, whatever the scientist's own table carries — each as its own filterable column keyed by identity. A property an identity's member tags disagree about is omitted rather than resolved to a winner: an identity read as one thing whose members disagree about what that thing is has no declaration to travel. Without this a reader can see that an identity was bound and not what it was declared to be, and cannot ask whether a clonotype bound its target and nothing in the control group.

  The panel-versus-reads check is emitted as a p-column in both directions — barcodes the reads carry that no panel declared, and tags a panel declared that the reads never showed.

  **Removed:** the dominant-feature ("consensus") call and the per-cell specificity score. A single dominant antigen per cell answers a different question from the one this block now answers, and a specificity magnitude is exactly the narrowing the four-state verdict replaces. The `pl7.app/feature/consensusFeature` and specificity p-columns are no longer emitted. No block in this workspace consumes them.

  **Unchanged:** the per-cell UMI count and fraction columns, the negative-control marker column, and the combine-mode settings.

  **A single-cell V(D)J dataset is required for the antigen stage.** Without one the block still runs and still emits its per-cell UMI counts and fractions, per-sample QC and per-feature properties exactly as before — but it produces no verdicts, no per-antigen columns and no panel check, rather than producing empty ones. The Verdicts page says so instead of showing an empty table.

  The baseline is the scientist's choice. The block no longer picks one.

  `what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either. The block derived it in two layers. Neither derives now.

  **This changes what an existing project computes.** A project that never touched the baseline field was silently answered under a derived rung — the declared tag where one existed, else the panel's own readings. It is now answered under the bottom rung: no baseline, and every verdict that needs one reads _unreliable_. The settings page says so in a warning while the field is unchosen. Choosing the rung that was being derived restores the previous numbers exactly.

  An unselected run is not refused. Refusing to start would be the block deciding a scientist's methodology by withholding the run, which is the same act as choosing one for them. It completes, and the run record carries both what was asked for and what served.

  "No baseline" is now on the list. It was withheld on the reasoning that nobody would choose a run with no answers, which was right about the consequence and wrong about the status: it is a position held in print, by scientists who argue that a tag declared to be bound by nothing is not truly negative and that a reference chosen that way lends false confidence. On that view the absence is a design choice rather than an omission.

  A rung that stops being serviceable is still never swapped for another. The run reports no baseline and records the request beside it.

  Stop the software picking a baseline rung, and require the choice on the command line.

  `what-plays-the-baseline` requires that the scientist selects among the rungs and that nothing selects for them: a baseline nobody chose is a methodology nobody knows they used, and two runs of one experiment would otherwise be answered by different rules with nobody choosing either.

  The software's own three-rung default is removed rather than left unused. Leaving it in place was a trap: the workflow omits `--reference-source` whenever the model's value is empty, so removing the derivation in the model alone would have silently promoted this one to the live rule, deriving in the layer furthest from the reader. `--reference-source` is now required, so there is nothing left to promote.

  `served_source` is unaffected and still degrades a rung that cannot serve to _none_, never to a different rung.

  The model still derives, which is a known deviation and the last one left. Removing it needs a ruling the spec does not settle — whether a run with nothing selected refuses to start or completes with every verdict that needs a baseline reading unreliable — because the two need different code. The docstring there now states this, and no longer claims such verdicts read _not evaluated_, which is a quality-measurement status and never a verdict state.

  Read counts against one declared baseline tag, or none. Several are refused, not combined.

  The block used to take the highest reading among several declared baseline tags. `baseline-scope` states that references are never combined, and taking the highest is a combination — so a panel declaring several no longer gets a comparator nobody specified.

  Reading against several is **deferred to a later version**, not dropped. That atom builds the reference as a grouping over a declared panel property: each reference serves the group its declaration scopes it to. This block has no group-by half — a tag is a comparator for the whole panel or for none of it — so it cannot say which antigens a second comparator belongs to. Refusing is also what the field does: the ordinary antibody run rejects a second control outright, and the T-cell run requires one control per allele and rejects two.

  The run stops and names the tags it found, rather than falling silently to no comparator. This is a panel a scientist fixes in a minute, and a silent fall to _unreliable_ everywhere would not tell them how.

  Scoped to the rung that reads a declared tag **as** the comparator. Under the panel rung and the tag-distribution rung, several declared tags are unambiguous — the first treats them as ordinary readings in its median, the second never sees the role at all — so those runs are unaffected.

  One role value can mark several tags, so this is a limit on the tags found, not on how many values are picked. The settings tooltip says so.

  One value marks the baseline tag, not several.

  "Values that mark the baseline tag" was a multi-select. It is now a single-select, "Value that marks the baseline tag".

  `040-glossary` splits the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference is a job given to one of them, declared with the run. So the value that nominates the baseline is singular.

  Several values only ever described a panel whose role column spells one role more than one way. A panel that does that is asking to be corrected, not accommodated.

  `referenceValues` stays a list in the block's data, so the `--reference-values` flag and every stored project keep their shape. A project that had picked several values keeps them until the field is next touched, and the run still refuses if they mark more than one tag. That refusal is unchanged and is now the only one that can fire: one value can still mark several tags, which is a panel fact the control cannot see.

  Whether the minimum count reaches the baseline tag is now a setting, off by default.

  The block exempted the declared baseline tag from the minimum count and hard-coded that. It is now "Apply the minimum count to the baseline tag" under Advanced reading settings, unticked.

  **It changes no verdict, and that is checked rather than asserted.** Each baseline source reads its own counts before the minimum, so the level a count is judged against is the same either way. An end-to-end test runs the same bed with the setting off and on and requires the verdicts, the per-cell counts and the per-cell scalars to be byte-identical, while requiring the removed-readings count to differ — so the test cannot pass on a bed where the setting reaches nothing.

  What it does change is the run's own accounting: how many readings the run reports as removed, how many cells it reports as emptied, and through those, which of a clonotype's cells count as empty.

  The emptied-cell population follows the same switch. With the baseline exempt, a cell holding only a below-minimum baseline reading never had evidence of binding for the minimum to remove. With the baseline subject to it, that cell has been emptied. Scoping the population one way while flooring the other would report a cell as keeping evidence it no longer has.

  One stale piece of reasoning is retired with this. The exemption's second stated ground was that flooring the comparator "lowers every denominator and shifts the whole run toward bound". Since each rung reads its own source raw, flooring here reaches no denominator at all, and that clause no longer holds.

  Add the tag-distribution baseline, and build every baseline from raw counts.

  A run with no declared control tag can now read each count against that tag's own distribution across the sample's cells, split into two components. It serves where the sample holds at least 300 cells and the tag's counts actually separate; a tag that does not separate reports no comparator rather than an invented one, and only the identities built from that tag are affected. Both conditions are settings on the CLI (`--distribution-min-cells`, `--distribution-separation`) and both are recorded in the run record, alongside a per-tag list of what could not be fitted.

  This is the first comparator that varies by identity rather than by cell, so the shared admissibility bundle now carries the identity a comparison is being made about.

  Separately, and independent of the new rung: every comparator is now computed from the raw counts rather than the floored ones. The minimum count applies to the reading being judged, never to what it is judged against — and because reference tags are exempt from the minimum and antigen tags are not, the panel comparator was previously a median taken over a mixture of raw and floored values. Panel comparators rise, so fewer cells read _bound_.

  Offer the tag-distribution baseline as a source, with its two conditions as settings.

  "Each tag's own distribution" joins the baseline dropdown. It reads each count against the lower of two components fitted to that tag's counts across the sample's cells, which is what serves a panel that declares no baseline tag and is too small to stand in for one — the shape every antibody kit has, since they cap at fifteen tags.

  It is the only source offered unconditionally. Whether it can serve turns on the sample's cell count and on whether each tag's counts separate, and the second is answered per tag rather than per run, so the conditions are stated in the option's description and the run reports what it managed: which tags fitted, which did not, and why. A tag that did not separate takes only the antigens it carries with it; every other antigen in the same cells is answered normally.

  Two new settings under "Baseline thresholds": the cells a sample needs before the rung may serve (300, from the study the method comes from) and how deep the dip between the two components must be (0.5, this block's choice — nothing published sets it). Both are sent on every run, so the record states the numbers a reading would have used whichever source served.

  Raise the panel rung's member minimum from 8 to 25.

  The figure comes from one preprint, whose own panels held 50 and 100 members, and nothing validates it lower. It gates the method rather than tuning it: below it, comparing a count against a handful of other antigens is not a background estimate, so the baseline it permits is not conservative but wrong.

  At 8 the rung was within reach of an antibody panel. It is not meant to be — those kits cap at fifteen tags — so a panel that declares no baseline tag no longer stands in as its own background. Such a run reads each tag against its own distribution instead, which is what that source was added for. A run that wants the old behaviour can still lower the setting, and says so wherever its verdicts appear.

  Carry a third number with every clonotype: how many of its cells were left with no count on any tag.

  `support-travels-with-the-reading` asks for it beside the two counts a verdict already ships — how many of the clonotype's cells could have answered at an identity, and how many did. This one is a property of the cell rather than of a position, so it is counted once for the clonotype: a cell with nothing left is empty at every identity, and repeating the subtraction per position would report a per-identity failure that did not happen.

  **It changes no verdict.** Those cells vote _not bound_ like any other. What it carries is whether a negative rests on cells that read something or on cells that read nothing.

  - **The baseline is part of the test, and that is the whole discriminator.** A cell whose antigen tags all fell below the minimum count while its baseline reading survived took up reagent and none of it was antigen — a real negative and a real vote. Only a cell with nothing anywhere read nothing. The existing per-sample `cellsEmptied` counter cannot see this: while the baseline is exempt from the minimum, that counter is scoped to the readings the minimum was allowed to remove, so the baseline is invisible to it. This is a new tally rather than a rename.
  - **`Cells that read nothing` ships off by default**, in the table's column chooser, ordered next to `Cells` because it qualifies it — forty cells of which thirty-eight read nothing is a different clonotype from forty that all read something.
  - **Turning `Apply the minimum count to the baseline` on moves this number**, and moves nothing else. The verdicts, the per-cell counts and the per-cell scalars are byte-identical across that switch, which is now pinned by a test.
  - An emptied cell stays in the clonotype's cell count and stays in the vote. Dropping such cells would shrink the denominator and make verdicts more positive, and filtering them from the cell list is the same effect by another route.

  Still to come: the per-sample alert for a run carrying many such cells, which fires whether or not a reader turned the column on. Where it lives and what counts as "many" are open.

  ## Declarations and identity grouping

  Group tags into identities per tag and sample. The panel file declares what a tag carries in each sample, so a barcode reused across panels now resolves to the antigen its own sample declared instead of standing alone under its raw sequence. On a per-sample panel the punchcard renders identities across rather than tags across, and each cell's reading combines only the tags its own sample offered.

  A panel member that contradicts itself no longer lets one member's declaration stand for the whole identity.

  A property holds of a grouped identity only where its member tags agree. A tag whose own rows contradict each other has no agreed value, so it reached that test as an empty string and was filtered out exactly like a tag whose cell was blank — and a blank member is deliberately not allowed to veto its neighbours.

  On a panel with barcode reuse that inverts the outcome. Measured on a real sixteen-row panel grouped on its role column: an identity whose five member tags declared six different antigen names between them came back carrying **one member's name**, because four of the five had contradicted themselves into silence and the survivor then agreed with nobody but itself. Nothing in the export marked it partial.

  A member that contradicted itself is a disagreement, not a silence, and now blocks the property. That is the direction the tag-grain rule already takes — it keeps disagreements rather than dropping them, because with barcode reuse an inconsistent declaration is the expected case and dropping it silently breaks the panel file's no-silent-drop rule. This stops that guarantee being undone one grain higher.

  Strictly more omission, never more assertion. Panels with heavy barcode reuse will carry fewer declarations on grouped identities than before, which is the correction rather than a side effect. A member that genuinely declared nothing still does not block its neighbours, and a column the identity was grouped on is still settled by construction.

  No new computation: the call site already built the disagreement map for its warnings and simply did not pass it down.

  A panel may mark several control features, not one.

  "Control feature marker (output only)" was a single-select. It is now "Control feature markers (output only)", a multi-select, and every chosen feature is marked in the `pl7.app/feature/negativeControl` column that downstream reads.

  `040-glossary` separates the two cardinalities. Being a control is a property of the tag, and a panel may carry several controls that are never nominated. Being the reference that supplies the baseline is a job given to exactly one of them. This setting marks controls and nominates nothing, so it takes as many as the panel has. The nomination is `referenceValues`, which stays singular.

  `--control-feature` is now repeatable. It is repeated rather than comma-joined because a feature name may contain a comma, and joining would split one name into two features that do not exist. Duplicates are dropped, so a feature is marked once and the axis it keys on cannot carry it twice.

  `controlFeature` is the shape a project saved before the setting took a list. It is still read: every reader goes through `controlFeatures()`, which reads either, so no stored project needs a migration. Nothing writes the singular form now.

  Emit the negative control on the feature axis. The chosen control feature is now surfaced as a dedicated hidden per-feature marker (`pl7.app/feature/negativeControl`), so VDJ Multiomic Integration can remove the control from its antigen metrics (restriction index, antigen breadth, per-antigen fraction columns, and the dominant call). No user-facing change — the marker is hidden and is not offered as a per-feature property.

  Rename the co-binding cell label from "cross-reactive" to "Target cross-reactive", making explicit that it means a cell binding two or more on-target antigens — distinct from unwanted, nonspecific polyreactivity. Also drop the remaining internal "Decoy" examples (retired in favour of "off-target").

  ## The quality layer

  Reconcile the quality layer: one rollup level, twelve measurements, and no invented lines.

  The quality output changes shape, so a reader of a previous run's report will find rows missing and one
  figure computing differently. Every change removes a claim the run could not support.

  **Only the sample rolls up.** The panel and capture statuses are gone. A panel status assumed its
  per-tag measurements would mostly carry statuses, and they do not — one is categorical and the rest are
  read as comparisons against the other tags in the same panel, which cannot be rolled into a severity
  without discarding the comparison that made them findings. A capture status was then the worst of every
  sample and every panel, which reduces to the worst of every sample: a statement that only repeats what
  sits beside it. Nothing hides, because a reagent finding states itself on its own per-tag row, keyed by
  the panel that has it. `--capture-map` is still accepted and is not read.

  **Three measurements are now stated exclusions rather than rows.** Sequencing saturation goes because a
  scientist cannot act on it for the run already collected, and whether the run was deep enough is
  answered by reads per cell against the vendor's recommendation. The known-answer check goes because
  nothing declares a known answer — no surface asks which clonotype the scientist already knows — so
  building the measurement means building that declaration first. Self-disagreement at an identity goes
  because it has nothing to compare against, and so cannot separate a faulty reagent from a panel full of
  weak binders. The obligation to show identity figures beside an alerting tag goes with it.

  **Self-disagreement is computed by pooling cells.** For one tag: every set with two or more cells that
  could answer contributes all of them, and the cells sitting in the minority of their own set are the
  numerator. The previous form scored sets — what share of sets disagreed at all — which needed a
  small-set cutoff, since a share over three cells takes only four values and would otherwise set the
  figure. **This changes the number on every run.** Two states cap the new figure at half.

  **A comparison is not a line, so it cannot produce a status.** The against-the-run route is removed
  along with the interquartile fence behind it. Per-tag self-disagreement now reads _unjudged_ and carries
  its value for a reader to compare against the tag's siblings. What that costs is real and accepted: a
  barcoded reagent binding something other than the receptor no longer announces itself, and a reader who
  does not scan the column sees a bad tag and a good one alike. The alternative was a multiplier nobody
  published, which moves the invention up a level rather than removing it — and an outlier rule fires on
  healthy runs, because marginal binding inflates disagreement across a whole panel.

  **The per-sample checks in the interface drop two invented cutoffs.** Reads assigned to the panel keeps
  its inherited 0.50 line and loses the second tier below it, which had no published source. Reads
  matching the read pattern now carries no status at all, the matched share being none of the four numbers
  the field publishes for this assay. Cells detected is unchanged and is now described as what it is, a
  categorical fact rather than a quantity judged against a cutoff.

  **A tag the reads never show is now _never asked_, not _not bound_.** Zero reads across a sample is
  categorical and cannot arise from biology: ambient reagent reaches every cell, so a tag that bound
  nothing still returns counts. What zero reads means is a reagent never added, a barcode mis-declared,
  or a library that failed — and none of those put the question the panel file says was put. Those cells
  now leave that identity's denominator instead of voting a confident negative on every clonotype in the
  run. A per-cell absence is unchanged: a cell that read nothing for a tag its sample did measure still
  votes _not bound_, which is a reading that happened and failed. `declaredNeverSeen` carries no status
  now, the verdict having taken that job.

  **A baseline is required, and a run without one does not happen.** The bottom rung is gone — "no
  baseline" is no longer a value a scientist can select, and an unselected baseline is refused rather
  than answered. The alternative was every position reading _unreliable_, which is honest and useless: a
  full punchcard of non-answers costs what a real run costs and looks like a result at a glance.

  Where the refusal falls follows from when the condition becomes knowable. A missing baseline tag and a
  panel below the tag count are properties of the **settings**, so they are caught before anything runs
  and the message names the condition that failed. Whether a sample holds enough cells whose counts
  separate is a property of the **data**, so a run on that rung proceeds, finishes, reports that no
  baseline could be established, and draws no punchcard. Its answer frames keep their headers and carry
  no rows; the frames describing the run's structure are written in full.

  A **Run quality** page shows the run's own quality report and the panel-versus-reads check. Both were computed by the verdict stage on every run and emitted, and neither had a page — so a measurement that came out alerting, and a barcode the panel declared that no read carried, were reported to nobody.

  The measurements table carries each measurement's status beside the coverage triple behind it — how many were judged, how many unjudged, how many not evaluated — and, where nothing computed a measurement, the reason it was deferred. All of those are shown without opening the column chooser: a status reading "nothing here is wrong" and a level where almost nothing was checkable must not look the same. Status is the plain word the run emitted, filterable through the discrete filter its column already declares, because `unjudged` and `not evaluated` are states rather than degrees of badness and no four-rank tag vocabulary can say that.

  Under a **Panel versus reads** separator, the mismatch check shows both directions in one table — barcodes the panel declared that no read carried, and barcodes the reads carried that the panel never declared — told apart by a filterable direction column.

  An absent report and an empty one say different things and are answered differently. No report at all means the verdict stage never ran, which happens when no single-cell V(D)J dataset was picked; the page says that instead of drawing a grid. An empty report means the stage ran and found nothing, which for the mismatch check is the outcome you want; the grid renders and says so in place of its rows.

  This page is the run's quality. The existing **Per-sample QC** page is unchanged and still shows the per-sample read statistics.

  Run quality rows carry the measurement's readable name, and the two columns explaining what a measurement counts and what a bad value means are shown by default rather than hidden behind the column chooser.

  ## The readout and its pages

  Replace the Binding verdicts and Quality checks tables with a punchcard

  The two result tables are removed as views. A punchcard takes their place: rows are clonotype sets,
  columns are the antigen identities picked from a dropdown, and a cell is one punch whose colour is the
  verdict and whose size is the support behind it. Every identity is already in the result, so picking one
  costs a redraw rather than a run.

  Both artifacts are still emitted. The verdicts and the run's own measurements are what the block owes,
  and dropping a view does not release it from producing them — the verdicts still export to downstream
  blocks, and the quality frames are still built by the workflow. What no longer exists is the pair of
  grids that presented them.

  The reading itself is unchanged: no threshold, default or verdict moves.

  Block data migrates to v4, dropping the three grid states the removed views owned and adding the
  punchcard's own state and its identity selection.

  Punchcard column headers show the identity's full name. They were cut to 20 characters to stop a long label auto-sizing its column off screen, but the cut fell on the barcode suffix that distinguishes two tags sharing a joined label, so distinct columns read as duplicates.

  The punchcard renders every identity column and is narrowed with the grid's own columns and filters panels. The "Antigens shown" multi-select is removed, along with the `punchcardIdentities` view state behind it.

  The per-sample slide-over is tabbed — Visual Report, Quality Checks and Log — following mixcr-clonotyping. Read recovery and the per-check quality statuses are shown for the selected sample instead of logs alone.

  ## Settings and config-time guards

  Read the tag-feature panel in the UI, so the column dropdowns fill on the pick.

  Choosing a panel CSV used to start a round trip — upload the blob, run a staging exec, read its JSON — before the barcode-sequence, feature-name and negative-control dropdowns had anything to offer. The exec itself was 59 lines of stdlib Python, but its artifact shared the package's `requirements.txt`, so the backend built a venv holding polars, numpy and scipy to run it, and paid that again after every version bump.

  The UI now reads the file directly. A local pick is read from disk on the gesture and the dropdowns fill immediately. A pick from remote storage, or a project opened where the original file never existed, is read from the CSV blob the prerun already exports — the same parser over the same bytes, so there is no second implementation to keep in agreement.

  - **`emit-csv-meta` is gone**: the entrypoint, `emit_csv_meta.py`, and its tests. The prerun now has no exec at all, so nothing builds a venv during staging. It still imports and exports the CSV, which is what drives the upload.
  - **`csvMetaSnapshot` in block data** carries the parsed panel, tagged with the handle it was read from. `readCsvMeta` returns it only while that tag matches the CSV currently picked, so a snapshot cannot be read against a different file.
  - **A failure is now shown, not logged.** With no workflow-side parser left to fall back on, a discarded parse error would leave empty dropdowns and no explanation, so the reason appears next to the file input.
  - Parsing uses `csv-parse`, as in the xsv-import block: RFC 4180 quoting, and both LF and CRLF endings. Real panel files are CRLF.

  The `prerunArgs` projection is unchanged and must stay that way — it is what keeps the UI's write to `csvMetaSnapshot` from re-rendering staging. The comment above it now says so.

  No change to `args()`, to the production workflow, or to any exported column. Existing projects re-read their panel from the exported blob on open.

  Warn at config time when the chosen barcode-sequence column holds no nucleotide sequences. A panel CSV often carries an identifier column beside the sequence column, and picking the identifier previously failed several stages into the run, inside barcode correction, with a Java stack trace. The block now names the offending values and the column that would work. The duplicate-barcode warning stays silent while this one shows, so the two never disagree about the fix.

  Clarify Settings tooltips. Each optional field now leads with when to use it (or to leave it blank), and the control, sample-column, off-target, combine-mode, dominance, and min-UMI descriptions are tightened for readability.

  The Tag-feature CSV tooltip drops its closing sentence, which pointed at the two labelled fields directly below it, so the tooltip is short enough to stay on screen. The Barcode sequence column tooltip now says the `FEATURE` tag captures the barcode on Read 2 and names Read 2 as the second read of each pair, because the assembled pattern also holds a sibling group called `R2` and "the `FEATURE` capture on Read 2" could be read as containment.

  Clarify off-target tooltips. The off-target property/values tooltips drop the retired "Decoy" term (in favour of "off-target antigen"), and the off-target-values tooltip now correctly states that value matching trims surrounding spaces but is case-sensitive — it previously claimed matching ignores case, which contradicted the shipped behaviour.

  The contending-antigen editor is not offered for now. Only the editor is deferred: a project that already carries contending groups keeps them, the args projection still passes them, and the emitted verdicts and their competitor notes are unchanged.

  It asked the scientist to retype by hand a grouping the panel file is meant to declare, and at the default one-identity-per-tag grouping the identities are the barcodes themselves — so the picker could only offer raw 15-mers. What it should become is contention derived from a declared panel column, alongside the existing grouping choice.

  Hide the Combine-mode column selector. It is not exposed to users for now; the control, its validation, and the workflow's combine-mode logic are kept for later re-enable. With the selector hidden, `combineColumn` stays unset and every antigen uses the default "sum" mode.

  ## Exported columns

  The verdict stage's cell artifact is split, so that no frame leaving the block carries a column keyed on `(sample, cell, tag)`.

  One frame previously bundled two differently-keyed columns and exported the pair: the per-cell, per-tag counts, and the per-cell scalars — the reference reading and whether a declared gate set the cell aside. Only the second belongs outside. The per-cell per-tag states stay inside the block, because labelling and lead selection read verdicts and never cells, so exporting them shipped the run's largest artifact across the boundary to a consumer that does not exist. The per-cell reference readings are the opposite case: the block is required to report the cells carrying a high reference reading whether or not a gate is declared, and which of them a declared gate set aside.

  **The per-cell per-tag counts are no longer imported at all**, not merely un-exported. Nothing read them on either side of the boundary, so importing them built the run's biggest p-frame for no reader on every verdict run — their grain is cell × tag, which on a realistic panel is 11-20× the rows of the sparse reads they derive from. The Python is untouched: it still writes the counts table and the exec template still collects it, so the states are computed and exist within the run. What stops is turning them into p-columns.

  **Renamed output:** `antigenCellTable` → `antigenCellReference`. What remains in the frame is per-cell reference readings and gate outcomes, so the old name described a shape the frame no longer has.

  Renaming an output is breaking for any downstream consumer, which is why this is worth checking rather than taking on faith — but there are none, so this ships as a patch. The claim is verifiable in one command: `git grep -n -E "antigenCellTable|cellCounts|cellScalars"` over `model/src`, `ui/src` and `workflow/src` returns hits only inside the workflow that builds them. Neither the model nor the UI reads the frame; the model's `perCellTable` output resolves a different, identically-named workflow output and is unaffected. The punchcard, verdicts, QC and panel-mismatch frames are unchanged.

  Align the exported columns' reader-facing labels to the spec glossary.

  Labels and descriptions only. No p-column name, domain or axis spec changes, so nothing downstream re-binds and no column identity moves.

  - **"Reference count" → "Baseline reading"** on the per-cell comparator. The glossary defines _baseline_ as the reading a count is measured against, and _reference_ as the tag rather than the reading. Every other reader-facing surface moved to "baseline" already; this one lives in the workflow and was missed.
  - **"Antigens bound / offered / settled / unsettled" → "Identities …"** on the clonotype counts. They count identities, never tags — the module's own comment said so two lines above the labels — and the glossary separates an identity, "a group of tags read as one thing", from the antigen a tag carries.
  - **The cell-grain sibling becomes "Identities this cell bound"**, because the clonotype-grain count now carries the plain name. The two are different numbers over different populations, and a reader meeting both under one name would take the smaller for a subset of the larger, which it is not.
  - **"Panel" → "Panel used"** on the per-sample column. The panel axis's own label column is the other "Panel", and it names the panel itself.
  - **"Measured thing" → "Measurement subject"**, and two Title Case stragglers to sentence case.

  Checked while doing this and deliberately left alone: "Feature" and "Tag" are not two names for one layer. The feature axis keys by antigen name and the tag axis carries barcode sequences, they are separate axes on purpose, and the module already guards against a new column keying on the legacy feature axis while holding barcodes.

  No two exported columns now share a label.

  ## Build and packaging

  Lower the per-sample mitool memory floor from 64 GiB to 16 GiB. The 64 GiB floor was applied on every run regardless of input size, and mitool's memory-from-limits launcher turns the grant into a JVM with `-Xms` = 50% of it — a ~32 GiB initial heap even for tiny datasets, which swaps on typical desktop RAM and stalls the "parsing reads" step. The `size("reads")*4` term still scales large inputs up (cap 256 GiB), so only small runs are affected. Also lower the per-sample mitool CPU default from 16 to 8 (matching peptide-extraction; 16 exceeded the core count on typical desktop machines) and fix the "mitool CPUs per sample" tooltip, which stated the default was 4.

  Fix the block changelog pointer. `block.meta.changelog` pointed at `file:../CHANGELOG.md` (the repo-root "Initial release" stub), so every published block-pack shipped the 1.0.0 stub and the desktop update view showed no release notes. Point it at `file:./CHANGELOG.md` — the changesets-generated block changelog.

- e406949: The tag-distribution baseline brings its own rule, and it is the one its method was published with.

  `what-plays-the-baseline` fixes that rule: on the raw counts, drop the counts above the 99th percentile, fit a two-component negative binomial mixture, label the higher-median component the signal one, and give each cell the probability that its count belongs to it. A cell reads bound at 0.9 or above. `count-becomes-a-state` is explicit that the score and cutoff of the declared rung do not apply — each baseline brings its own rule, a run selects one baseline, so exactly one rule calls the state.

  **This changes every verdict on a distribution-rung run.** The rung previously fitted a kernel density over log2 counts, split it at the deepest trough, took the median of the background as a comparator count, and then handed that count to the declared rung's score. The published method it followed never defines that number, and no run was reading the rule the rung was validated with.

  The separation test goes with it, and that is also the spec's instruction rather than an omission. The old fit rejected a tag whose two components did not stand far enough apart, on a threshold this block invented. `what-plays-the-baseline` refuses exactly that: the method assumes two components exist, a tag that bound nothing will be split anyway and its upper slice called signal, no published test replaces the eye, and "a test invented here would be this corpus doing the thing it refuses everywhere else". So the run shows the fit instead of judging it. **A tag nothing bound can now report bound cells.**

  `Maximum dip height` is removed from the settings, along with `--distribution-separation`, because a mixture has no trough to measure. The 300-cell condition stays.

  `referenceCount` is now null on every distribution-rung row. That rung's comparator is a fitted distribution rather than a reading, and a number there would read as a comparator that served.

- 799e6ad: Every setting is named from the spec glossary, and the form is grouped by what a setting decides.

  Seven labels change. "Admissibility gate (baseline UMIs)" and "Min UMIs per barcode" say **unique counts**, which is the glossary's word for a count of distinct molecules. "Minimum cell agreement" becomes "Share of voting cells that must agree", since the glossary's term is a **vote**. "Bound cutoff" becomes "Score at which a cell reads bound", naming the **score** it acts on. "Panel columns that define an identity" becomes "Panel columns that group tags into identities", naming the **grouping**. "High baseline reading" becomes "Line where a baseline reading counts as high", which says it is a line rather than a reading. "Cells needed to fit a tag's own distribution" becomes "Cells a sample needs for this baseline", dropping the method jargon.

  Four moves. The sample column joins the other panel-column pickers at the top, where it belongs: it is a panel input, and it was sitting below the baseline among the reading settings. The baseline's own cell condition moves up beside the baseline choice, because it is a condition on that choice. "Optional settings" becomes **The reading** and takes the agreement share, which was alone behind an "Advanced reading settings" accordion that now goes. The "Baseline thresholds" accordion goes too: the gate and the high-reading line both read a cell's own baseline reading, so both exist only under a declared tag, and they now sit in the baseline section with the rung they belong to. A header for two fields that vanish with the rung above them was a section the reader met empty more often than not.

  Both of those fields were gated on "not the fitted rung", which also showed them before any baseline was chosen. They are now gated on the declared rung itself.

  Three tooltips follow the same words, and the sticky-cell one now says what sticky means and that counting such cells is a measurement while setting them aside is the gate's job.

- f0a2513: The minimum count never reaches the baseline tag, and the setting that could make it is gone.

  `minimum-count-before-any-reference` states it as a rule rather than a preference: the minimum asks whether a count is evidence of binding, a tag declared to be bound by nothing is never evidence of binding, so the question does not arise for it. A cell's reference reading enters the comparison as it came back, and a small one is the measurement rather than noise to be cleared away.

  "Apply the minimum count to the baseline tag" is removed, along with `--minimum-applies-to-baseline` and the `minimumAppliesToBaseline` field. The exemption is now unconditional in `apply_floor`.

  **No verdict changes.** The setting was off by default and every rung already built its comparator from raw counts, so the default behaviour is what shipped. What changes is that the other behaviour can no longer be selected. A stored project that had switched it on now runs with the exemption in force, and reports fewer removed readings and fewer emptied cells than it did.

- 7923317: A sample's report is its own measurement set, and the Quality tag is that set's rollup.

  The Quality Checks tab listed **three** hand-written checks against a threshold the UI held itself, while the software computed **nine** sample-level measurements with their own statuses and their own line provenance. A reader opened a sample, met three rows and a green badge, and concluded the sample had been checked. Six measurements were omitted without saying so, and a list that silently drops what it could not check reads exactly like a list that checked everything and found nothing wrong.

  **The tab now lists every sample-level measurement the software declares**, in declaration order, including the ones nothing computed. Each row carries its status where a line stands behind it, its value, and — where there is no value — the reason in place of one. A measurement with no line carries no status and shows an em-dash: there is no fourth status word, and which of the two no-status cases applies is read from the value.

  **A blank and a zero are opposite findings, so nothing is ever blank.** Every valueless row states why: the aggregate-barcode fraction because nothing in this block detects aggregates, the sticky measurement because no cell of the sample carries a comparator reading, the read-level fractions because the refine-tags report supplied no step with input reads. The reasons travel in `result_qc.csv` too, on a `reason` column that previously carried only the deferred ones.

  **The sample's rolled-up status sits at the top of the list, with its coverage beside it** — how many measurements were judged, how many were computed with no line to judge them against, and how many nothing computed. Whether something is wrong and whether anybody looked are different questions and are answered separately.

  **The Main grid's Quality tag is that same rollup and is computed nowhere else.** The UI no longer holds a QC threshold of its own: `PANEL_ASSIGNED_LINE`, `qcChecks`, `qualityStatus` and `QcCheck` are gone, and with them the second copy of a line that could drift from the software's. The tag and the report beside it cannot disagree about one sample.

  A measurement whose finding belongs to a reagent rather than to the sample keeps its own status on its row and stays out of the sample's rollup, and its row says so — otherwise a reader meets a status on the page that the tag does not carry.

  The verdict step emits `result_qc_by_sample.json` alongside `result_qc.csv`, and the model reads it as `sampleQcReport`. The frame remains the artefact every other reader takes; the report is the same measurements keyed by sample, for a view that holds one sample at a time.

### Patch Changes

- Updated dependencies [3ad61a7]
- Updated dependencies [e406949]
- Updated dependencies [f0a2513]
- Updated dependencies [1d0f6ef]
- Updated dependencies [7923317]
  - @platforma-open/milaboratories.feature-integration.model@3.0.0

## 2.2.0

### Minor Changes

- 88d6e9b: Feature Barcode Profiling — pilot finishing (BEAM in-vivo).

  Analysis / functionality:

  - Preview (dry-run) mode with a per-file read cap for fast settings checks.
  - Multi-barcode antigens: `sum` (OR, default) and `all` (AND — called only where every probe barcode fires) combine modes, declared via an optional tag-CSV combine column plus a Min-UMI advanced field (covers the LIBRA-seq dual-probe design).
  - Import per-feature properties from the tag-to-feature CSV's extra columns (A-0026): every column beyond the mapped barcode-sequence and feature-name columns becomes a `pl7.app/feature/property` p-column on the shared feature axis, published as a `featureProperties` export so properties (antigen type, species, pool, ...) ride into VDJ Multiomic Integration and Lead Selection. Generic, no hardcoded schema.
  - Off-target-aware consensus plus a "cross-reactive" label, off by default: the controls are exposed, but with no off-target designation set the dominant call is byte-identical to before and the label never appears.

  Resource allocation:

  - Default per-sample mitool CPU/memory now match the MiXCR blocks (`mixcr-analyze`): parse/refine default to `cpu(16)` and `clamp(gib(64) + size("reads")*4, gib(64), gib(256))`; tag-stat and per-cell-metrics memory floors raised with a 256 GiB cap. Optional per-sample `perProcessCPUs` / `perProcessMemGB` overrides remain in Advanced Settings.
  - The polars steps (per-cell-metrics, qc-report) are bound to the granted CPU via `POLARS_MAX_THREADS` so they no longer size their thread pool to all host cores.

  UX / logging:

  - Live per-sample, per-step logs (mitool parse / refine / tag-stat plus the Python per-cell-metrics step), opened by double-clicking a sample row; a per-step "[N/M]" progress counter that advances through the final Python stage, with the progress label following the live stream.
  - The sample column auto-populates when the tag CSV has a column whose values match the dataset's sample names (replacing the manual suggestion banner); the CSV-metadata staging keys on the CSV alone, so the barcode / feature-name dropdowns stay populated across reloads and dataset changes; tag-mapping dropdowns are gated until a tag CSV is loaded.
  - Tooltips across every setting, the read-layout fields, and the Quality / Read-recovery columns; the analysis-logs drawer leads with a hint pointing to the richer per-sample logs.

  Housekeeping:

  - Rename the block display title to "Feature Barcode Profiling" (display only).
  - Remove middot/bullet separators from labels, the default subtitle, and the breakdown output.
  - Fix the broken mitool reference link in the block description; remove stale planning docs.

### Patch Changes

- Updated dependencies [88d6e9b]
  - @platforma-open/milaboratories.feature-integration.model@2.2.0

## 2.1.2

### Patch Changes

- 55d84ba: SDK Update
- Updated dependencies [55d84ba]
  - @platforma-open/milaboratories.feature-integration.model@2.1.2

## 2.1.1

### Patch Changes

- b878b6b: Remove standard output usage
- Updated dependencies [b878b6b]
  - @platforma-open/milaboratories.feature-integration.model@2.1.1

## 2.1.0

### Minor Changes

- c44afc8: Add progress bar

### Patch Changes

- Updated dependencies [c44afc8]
  - @platforma-open/milaboratories.feature-integration.model@2.1.0

## 2.0.0

### Major Changes

- 632b4bf: Feature Integration v1 — consolidated notes (all prior changesets combined into one; everything
  major-bumped).

  ## Feature-barcode workflow (core)

  Implement the feature-barcode workflow (plan Tasks 3–4).

  - Workflow: per-sample mitool pipeline (`parse → refine-tags → tag-stat -u`) over the feature-barcode
    FASTQs, then the per-cell-metrics Python software, importing the per-cell results as the A-0010
    contract p-columns keyed `[pl7.app/sampleId, pl7.app/sc/cellId, pl7.app/feature/featureId]`
    (`umiCount` / `fraction` / `consensusFeature` / optional `specificityScore`), exported to the result
    pool for VDJ Multiomic Integration.
  - Tag pattern: cell barcode `CELL`, feature barcode `FEATURE` (mitool's first-class feature tag type,
    mitool#86), molecule `UMI`. Read geometry is configurable (cellLen/umiLen/featureLen, 10x 5' v2
    defaults) — DP-1 "parameterize + proceed".
  - Feature-barcode error correction: `refine-tags` corrects `FEATURE` against the panel whitelist (the
    tag column of the user's tag→feature CSV, emitted as `panel.txt` by the per-cell-metrics `emit-panel`
    entrypoint) — within-Hamming-1 reads snap to a panel barcode and off-panel reads are dropped
    (mitool#87 fixed the `-t TAG#file:` whitelist CLI). `CELL` is de-novo corrected; the tag order scopes
    UMI deduplication to `(cell, feature)`.
  - Python `per_cell_metrics._load()` consumes mitool's aggregated `tag-stat -u` output (the pre-computed
    `unique_UMI` distinct-molecule count) instead of counting raw UMI rows — DP-2.

  ## Per-cell metrics (Python)

  - Front-end plan gaps: enforce the dominance 0.5 floor in the UI; user-mapped CSV barcode/feature
    columns (D4); per-sample QC summary table (reads parsed/matched, cells, features, UMIs); specificity
    - consensus hints on the results page; fix a pre-existing raw tag-stat QC output-name panic.
  - Compute `panelAssignedFraction` in the per-sample QC report (was always blank). It now reads the
    FEATURE correction step's `outputCount / inputCount` — the fraction of reads kept after correcting
    the feature barcode against the panel whitelist — falling back to blank when no refine report is
    available, the report has no FEATURE step, or that step has zero input reads. Covered by new
    behavioral tests in `test_qc_report.py`.
  - Fix a crash when no (cell, feature) pair survives the tag→feature join (a wrong read geometry, or a
    sample with no on-panel reads): both empty-input paths now write header-only CSVs instead of failing
    the whole per-sample run (the UMI-count column is coerced to numeric on read so a header-only
    tag-stat, which polars would infer as String, doesn't break the fraction division).
  - Vectorize the consensus and specificity computations. Both previously looped in Python over every
    cell (consensus) and every (cell, feature) row (specificity, via `iter_rows` + a list of dicts),
    which was slow and held a Python-object copy of the data on top of the polars frame — the first thing
    to OOM on large samples. They are now pure-polars/numpy column operations (group-by + window for the
    dominant-category rule; scipy `beta.cdf` applied to whole columns for the score). Output is
    byte-identical, guarded by the golden consensus test plus oracle tests that cross-check both
    vectorized paths against the pure `consensus_category`/`specificity_score` rules. Measured: 1.2M
    (cell, feature) rows with a control process in ~0.9 s at ~0.7 GB. Empty input stays header-only via
    polars schema-preservation.

  ## Model & UI

  - Feature-parity enhancements from a review against recent blocks:
    - Negative-control dropdown now works: staging parses the tag→feature CSV (`emit-features`
      entrypoint) and the model exposes the discovered feature names as `controlOptions` (was an empty
      stub).
    - Robustness: `tag-stat -u` runs with `--use-local-temp` (avoids shared /tmp exhaustion on the
      on-disk sort); mitool `parse`/`refine-tags` memory is sized from the input reads' blob size via
      `memFormula` (clamped, with the metaExtra floor as fallback) instead of a fixed request.
    - Observability: per-sample × per-step mitool/Python logs, an `isRunning` spinner signal, and a raw
      `tag-stat` QC table surfaced on a QC page (`PlLogView` + `PlAgDataTableV2`).
    - UI/model polish: results table is `retentive` + `withStatus` (no flicker on recompute); the
      tag→feature CSV is validated client-side (required columns) with a feature-count preview; a dynamic
      subtitle reflects the chosen control feature. Export column specs moved to `column-specs.lib.tengo`
      with the standard abundance/order/visibility annotations (identity-neutral).
  - Render all three results tables (`perCellTable`, `tagstatQcTable`, `qcSummaryTable`) with
    `createPlDataTableV2` instead of V3: they are the block's own self-contained, non-batch
    `processColumn` frames that V3's discovery cannot render (the scoped-sources form returns undefined;
    the array-columns form hangs on the upstream Samples & Data FASTQ File-dataset). V2 takes the columns
    directly and auto-joins the `pl7.app/label` sample name onto the sample axis (so tables show the real
    sample name, not the internal sample hash).
  - Harden the model: read the prerun feature/column lists with `getDataAsJsonOrUndefined` instead of
    `getDataAsJson` (the latter throws "Resource has no content." while staging is still computing), and
    reject in `args()` when the barcode-sequence and feature-name columns map to the same CSV column
    (previously only caught by the Python after the full mitool chain ran).
  - Standardize the sidebar subtitle to the block-label pattern: the subtitle reads
    `data.defaultBlockLabel` (falling back to a static string), which a UI watchEffect mirrors from a new
    `suggestedBlockLabel` model output — a dynamic `"<dataset> · <barcode> → <feature>"` string derived
    from the selected FASTQ dataset, the barcode-sequence column, and the feature-name column (each part
    dropped until set). The derivation lives in the output because the subtitle context has no result
    pool.
  - Split the QC page into two full-height single-table sections — "Per-sample QC" and "Raw tag-stat" —
    the standard one-table-per-page pattern.
  - UI tidy-up on the Main and QC pages: whitelist help text moved into a `#tooltip` slot; dataset input
    label renamed to "Select dataset" (block convention); removed the "N features detected" hint; the
    no-negative-control info banner made dismissable (persisted via a `controlInfoDismissed` UI-only
    field); pipeline logs moved into a "Logs" slide-over; fixed the stacked QC tables collapsing to their
    footers.
  - Add column-header tooltips (`pl7.app/description`) to the results tables (Main: Feature fraction,
    Consensus feature, Specificity score; Per-sample QC: Panel-assigned fraction; Raw tag-stat: Distinct
    UMIs (raw)). Annotations only — column identity is unchanged, so the A-0010 downstream contract is
    unaffected.
  - Mark the barcode-sequence and feature-name column dropdowns as `required` (red-star indicator),
    matching their `args()` validation — both must be set before Run enables.
  - Rework the logs into a single "Analysis logs" (wide 80% slide-over) that scales to any sample count,
    replacing the per-tool-step log boxes. While the run is in progress it shows a live
    `Processing… N samples complete` count (no fixed denominator — the block only processes the samples
    present in its feature-barcode dataset, not every project sample); when the run finishes it shows a run-level
    summary — reads parsed, panel-assigned % (median + range), cells/features, and any samples flagged
    for a panel-assigned fraction below 50% (listed by name). qc_report.py now emits per-sample QC as
    JSON (`qcJson`); the model reads it to build the log (completed-sample count + aggregate), with
    sample names from the upstream label column. Detailed per-sample statistics stay on the QC page.

  ## Cell-barcode whitelist (added, then UI removed for v1)

  - Added an optional, chemistry-selected cell-barcode whitelist for CELL correction: a "Cell barcode
    whitelist (10x)" setting pointing `refine-tags` at a 10x built-in (`#builtin:<name>`, e.g.
    `737K-august-2016`) so the emitted `pl7.app/sc/cellId` strings match the VDJ producer by
    construction; default `""` = de-novo.
  - Then removed the UI selector for v1 (Feature Integration is de-novo only): it was not spec-required,
    de-novo already yields the ~99% cross-block join empirically, only the 5' v2 option was verified, and
    the others carried footguns (whitelist/UMI-length could disagree; non-5'v2 VDJ-side alignment
    unconfirmed). Aligning cell barcodes across producers is a chain-level concern to revisit once the
    downstream join is verified end to end. The `#builtin:` workflow/model plumbing is kept dormant
    (`cellWhitelist` stays `""`) as a documented seam. See
    `docs/dormant-features/cell-whitelist-correction-plan.md`.

  ## Robustness / performance / fixes

  - Fix a pre-Run deadlock introduced with the D4 column-mapping UI: the barcode/feature dropdowns
    (`csvColumnOptions` / `controlOptions`) are populated by the prerun reading the uploaded CSV, and
    their values are required by `args()`, but the CSV upload was driven only from the main render (which
    is unreachable until `args()` passes) — a circular dependency. The prerun now exposes the CSV import
    handle and the model adds a second `getImportProgress` driver resolved from `ctx.prerun`
    (`isActive: true`), mirroring `samples-and-data`, so the CSV uploads during staging, the dropdowns
    populate, and Run enables.
  - Fix `perCellTable` failing to render with `partitionKeyLength (0) must be strictly less than the
number of axes (0)`: the per-sample QC summary was an `Xsv` output with empty axes emitted in the
    same `processColumn` as the contract columns, tripping `xsv.importFile`'s assertion and crashing the
    shared render. It is now collected as a `[sampleId]` file map and concatenated + imported once by a
    child template `qc-summary.tpl.tengo` (injecting the real `sampleId` per row), keeping the 8 typed
    metric columns. Also fixes a latent output-name mismatch (`qcSummary` vs the body's `qc` key).
  - Size the `tag-stat` and `per-cell-metrics` steps' memory from input volume (memFormula, base 8 GiB +
    input-blob × multiplier, clamped to 128 GiB) instead of a fixed 8 GiB, mirroring parse/refine
    (tag-stat by the refined.mic blob, per-cell-metrics by the tag-stat TSV). Prevents the two
    input-sized steps from OOMing on large samples. The Advanced-Settings per-process override still
    applies to parse/refine only.

  ## Per-cell results table, QC labels, graph tab

  - The Main results table is now ONE ROW PER CELL `[sampleId, cellId]` instead of the per-(cell ×
    feature) matrix. `per_cell_metrics.py` emits a new `result_per_cell_summary.csv` (imported as the
    table-only `perCellSummary` columns): `Max Feature UMI count`, `Max Feature Fraction`, and (with a
    negative control) `Max Specificity score` — the per-cell max across features — plus a `Features`
    summary string that lists every feature the cell has signal for as `feature : umiCount : fraction`,
    sorted by descending fraction (dominant first), mirroring antibody-sequence-liabilities'
    `pl7.app/isSummary` column. The Main table now shows consensus + these per-cell columns. The
    A-0010 export contract is UNCHANGED — the full per-(cell × feature) `abundance`/`fractions`/
    `consensusFeature`/`specificityScore` matrix still flows to the result pool for VDJ Multiomic
    Integration.
  - Per-sample QC table now shows the real sample name: it moved from `createPlDataTableV3`
    (`selector { mode: "enrichment", maxHops: 0 }`, which never joins the `pl7.app/label` column) to
    `createPlDataTableV2`, which auto-joins it — previously the sample axis rendered the internal hash.
  - All tables render `Sample` first, then `Cell ID` (sampleId is axis 0, cellId axis 1).
  - New "Graph" tab (last in the tab list): an embedded `@milaboratories/graph-maker` violin plot
    (chart-type discrete) defaulting to y = Feature Fraction, grouped by Sample, faceted by Feature. It
    plots the full per-(cell × feature) matrix (a new `graphPf` workflow output) via a `pf` model output
    built from `getRelatedColumns` (this block's columns + axis-compatible pool columns, so the sample
    grouping shows real sample names) with File-valued columns filtered out of the picker.

  ## Assets

  - Update the block and organization logos.

  ## Loading screen — live per-sample progress

  Replace the Main page's static "run the block" placeholder with a live per-sample progress list while a
  run is in progress. Each sample appears as soon as the roster is enumerated ("Queued"), then shows the
  current mitool step by name with its percent + ETA ("Parsing reads" → "Refining barcodes" → "Counting
  UMIs"), and flips to "Done" when its per-sample pipeline finishes. Modeled on blocks/peptide-extraction.

  - Workflow: each mitool step (parse / refine-tags / tag-stat) runs with a shared `[==PROGRESS==]`
    `MI_PROGRESS_PREFIX` sentinel and its stdout is surfaced two ways — a flat per-sample `parseLogStream`
    Log column (roster + early gate) and a nested per-sample × per-step `stepLogs` Log ResourceMap.
  - Model: new outputs — `started`, `sampleProgress` (roster, gated on `getInputsLocked`), `stepProgress`
    (per-sample × per-step latest progress line), `completedSamples` (from the per-sample qcJson), and
    `sampleLabels`.
  - UI: `sampleResults` picks each sample's latest active step; the Main page renders a stack of
    `PlProgressCell`s (no new UI dependencies), gated on `isRunning` so it shows while running and the
    results table shows once done.

  ## Per-cell "Feature breakdown" column

  Rename the per-cell summary column "Features" → **"Feature breakdown"** and reformat its per-feature
  list from `feature : umiCount : fraction` to `feature (fraction%, umiCount UMI)` (percent instead of a
  raw decimal, "<1%" for a nonzero feature that rounds below 1%), bullet-separated with non-breaking
  padding and sorted by descending fraction. Display-only — the exported per-feature matrix (A-0010
  contract) is unchanged.

  ## Main page — progress-only view (in-UI data display hidden for now)

  Operator feedback: the block should not surface result data in its own UI — only per-sample progress,
  matching MiXCR Clonotyping (Main + a QC tab). The Main page now always shows the per-sample progress
  grid; when the run finishes every row settles into its "Done" state instead of swapping to the results
  table. The "Raw tag-stat" and "Feature Fraction Distribution" (Graph) tabs are commented out in
  `sections()`. This is display-only and reversible — the results-table branch, both tabs' pages, their
  model outputs (`perCellTable` / `tagstatQcTable` / `pf` / `pfPcols`), and the routes are all left intact,
  so a tab is re-enabled by uncommenting one line. The "Per-sample QC" tab stays. The A-0010 export
  contract to VDJ Multiomic Integration is unaffected.

  ## CIDConflictError fix — isolate mitool stages in render.create sub-templates

  The block hit a `CIDConflictError` on every run once the per-sample pipeline was exercised. Root cause:
  each mitool step (`parse` / `refine-tags` / `tag-stat`) captures rate-dependent progress via
  `saveStdoutStream()`, and the SDK's `exec.tpl` is `hash_override`-pinned — so any `getFile(...)` off a
  streaming exec has a stable resource identity but a run-to-run drifting content hash. Feeding such a file
  into a downstream exec's `addFile` and then flattening that exec's per-sample output into a p-frame puts a
  stable-id / drifting-hash node on the deduplication path, which conflicts on re-render.

  Fix: every exec stage now runs inside its own `render.create` sub-template (`fb-parse`, `fb-refine`,
  `fb-tagstat`, `fb-downstream`), and `fb-pipeline` is a thin orchestrator that returns only sub-template
  render outputs — never an inline `exec.getFile(...)`. A `render.create` resource has a content-derived
  identity, so a consumer inside a boundary absorbs the drift and its per-sample outputs flatten cleanly
  (the same structure the peptide-extraction block uses). The A-0010 export contract is untouched. NOTE:
  this render.create split was an intermediate step that only _relocated_ the conflict — the actual fix is
  in the next section (removing `saveStdoutStream`), which also removes the live per-step progress grid this
  section originally described. The unconsumed `parse_report.txt` / `refine_report.txt` reports were dropped
  along the way.

  ## CIDConflictError fix — remove `saveStdoutStream` from the mitool execs

  The `render.create` split above was necessary but NOT sufficient: the block still hit `CIDConflictError`
  (non-deterministically on a clean first render, reliably on re-render). Root cause, confirmed against the
  SDK: `saveStdoutStream()` writes the rate-dependent stdout as a saved file INTO the exec's `files` map
  (`exec/index.lib.tengo`), so every `getFile` off a mitool exec resolved to a run-to-run drifting content
  hash while its resource identity stayed pinned (the SDK `exec.tpl` is `hash_override`-pinned) — a
  stable-id / drifting-hash pair that conflicts on any dedup-relevant flatten. `render.create` only
  relocated the conflict; it could not remove it.

  Fix: drop `saveStdoutStream` (and the now-dead progress plumbing) from the parse / refine-tags / tag-stat
  execs, so their outputs are deterministic and dedup cleanly. This trades away the live per-step progress
  bars: the Main grid now shows each sample as `Processing…` → `Done` (driven by the per-sample `qcJson`
  completion plus the input sample-label roster), and the Quality / Read-recovery columns still fill in at
  completion. The A-0010 export contract is unchanged. Two unused `saveStdoutStream` calls in `prerun.tpl`
  were also removed (dead trap surface). Keeping the live grid would require `render.createEphemeral` (loses
  per-sample caching) or an SDK stdout-capture mode that keeps the stream out of the dedup-relevant files map.

### Patch Changes

- Updated dependencies [632b4bf]
  - @platforma-open/milaboratories.feature-integration.model@2.0.0
