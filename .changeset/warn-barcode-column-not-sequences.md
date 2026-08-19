---
'@platforma-open/milaboratories.feature-integration.ui': patch
'@platforma-open/milaboratories.feature-integration.model': patch
---

Warn at config time when the chosen barcode-sequence column holds no nucleotide sequences. A panel CSV often carries an identifier column beside the sequence column, and picking the identifier previously failed several stages into the run, inside barcode correction, with a Java stack trace. The block now names the offending values and the column that would work. The duplicate-barcode warning stays silent while this one shows, so the two never disagree about the fix.
