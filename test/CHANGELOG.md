# @platforma-open/milaboratories.feature-integration.test

## 1.0.12

### Patch Changes

- Updated dependencies [bf43ed8]
  - @platforma-open/milaboratories.feature-integration.model@3.3.0

## 1.0.11

### Patch Changes

- Updated dependencies [ff33d6a]
  - @platforma-open/milaboratories.feature-integration.model@3.2.0

## 1.0.10

### Patch Changes

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

- Updated dependencies [e873489]
- Updated dependencies [e873489]
- Updated dependencies [e873489]
- Updated dependencies [3af02c5]
  - @platforma-open/milaboratories.feature-integration.model@3.1.0

## 1.0.9

### Patch Changes

- Updated dependencies [82a0c86]
- Updated dependencies [1791309]
  - @platforma-open/milaboratories.feature-integration.model@3.0.3

## 1.0.8

### Patch Changes

- Updated dependencies [acfde14]
- Updated dependencies [fd74062]
- Updated dependencies [817a04b]
  - @platforma-open/milaboratories.feature-integration.model@3.0.2

## 1.0.7

### Patch Changes

- Updated dependencies [5ef1665]
- Updated dependencies [46268f1]
- Updated dependencies [bf00e26]
  - @platforma-open/milaboratories.feature-integration.model@3.0.1

## 1.0.6

### Patch Changes

- Updated dependencies [3ad61a7]
- Updated dependencies [e406949]
- Updated dependencies [f0a2513]
- Updated dependencies [1d0f6ef]
- Updated dependencies [7923317]
  - @platforma-open/milaboratories.feature-integration.model@3.0.0

## 1.0.5

### Patch Changes

- Updated dependencies [88d6e9b]
  - @platforma-open/milaboratories.feature-integration.model@2.2.0

## 1.0.4

### Patch Changes

- Updated dependencies [55d84ba]
  - @platforma-open/milaboratories.feature-integration.model@2.1.2

## 1.0.3

### Patch Changes

- Updated dependencies [b878b6b]
  - @platforma-open/milaboratories.feature-integration.model@2.1.1

## 1.0.2

### Patch Changes

- Updated dependencies [c44afc8]
  - @platforma-open/milaboratories.feature-integration.model@2.1.0

## 1.0.1

### Patch Changes

- Updated dependencies [632b4bf]
  - @platforma-open/milaboratories.feature-integration.model@2.0.0
