---
'@platforma-open/milaboratories.feature-integration.ui': minor
---

The agreement limit is a percentage, and the form's sections are named for what they hold.

"Share of voting cells that must agree (0–1)" becomes "Voting cells that must agree (%)". A share is a number a reader has to translate, and this one has a floor most readers do not expect: agreement is measured among the cells that answered and the verdict takes the majority, so it can never fall to half or below. The field now runs from 51 to 100 and says why. The data keeps the 0–1 share, so `--min-agreement` and every stored project are unchanged.

Its tooltip now leads with the default, which is **off**: no agreement test runs, a narrow majority stands, and the verdict reports how narrow it was.

The tag-barcode FASTQ dataset is marked required, which `args()` has always enforced. The single-cell V(D)J dataset moves up beside it, so the two dataset inputs sit together above the panel.

Three sections are renamed: a new **Panel Settings** header over the panel file and its columns, **Baseline (background) level** becomes **Baseline (Background) Parameters**, and **The reading** becomes **Threshold Parameters**.
