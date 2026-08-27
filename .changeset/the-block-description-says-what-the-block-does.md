---
'@platforma-open/milaboratories.feature-integration': patch
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.workflow': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

The block's description says what the block does.

It still described the shipped feature-integration block: which antigen each cell bound "and how
strongly", the dominant (consensus) antigen, and a per-antigen specificity score. The block emits none of
those, and two of them are refused outright — `verdict-at-every-identity` forbids a dominant antigen
standing in for the set, and `binary-narrowing` forbids a magnitude leaving as the answer. The catalogue
entry promised a reader exactly the two things they cannot have, and said nothing about the four-state
verdict that replaced them.

Four smaller corrections. The punch legend said an unsettled reading has *seven* ways to fail and the
tooltip said six; there are five, the sixth reason belonging to *never asked*. The per-sample count
distribution was labelled as a median and described as deciles. The model cited a `resolve_default_source`
in `verdict.py` that deliberately does not exist and that a test pins as absent. And the workflow's
no-dataset branch carried a comment describing a run the model refuses.
