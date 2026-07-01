---
'@platforma-open/milaboratories.feature-integration.workflow': minor
'@platforma-open/milaboratories.feature-integration.model': minor
'@platforma-open/milaboratories.feature-integration.ui': minor
'@platforma-open/milaboratories.feature-integration': minor
---

Add an optional, chemistry-selected cell-barcode whitelist for CELL correction.

`refine-tags` corrects the cell barcode de-novo by default (unchanged). A new "Cell barcode whitelist
(10x)" setting (Advanced Settings) lets the user point CELL correction at a 10x built-in whitelist
(`#builtin:<name>`, e.g. `737K-august-2016`), so the emitted `pl7.app/sc/cellId` strings match the VDJ
producer (mixcr-clonotyping) by construction — turning the downstream VDJ Multiomic Integration join
from ~99% probabilistic into deterministic. Default `""` = de-novo, so existing behaviour and non-10x /
synthetic inputs are unchanged; the FEATURE panel correction and downstream blocks are untouched. See
`docs/cell-whitelist-correction-plan.md`.
