---
'@platforma-open/milaboratories.feature-integration.model': patch
---

Punchcard column headers show the identity's full name. They were cut to 20 characters to stop a long label auto-sizing its column off screen, but the cut fell on the barcode suffix that distinguishes two tags sharing a joined label, so distinct columns read as duplicates.
