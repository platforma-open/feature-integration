---
'@platforma-open/milaboratories.feature-integration.ui': patch
---

Hide the Combine-mode column selector. It is not exposed to users for now; the control, its validation, and the workflow's combine-mode logic are kept for later re-enable. With the selector hidden, `combineColumn` stays unset and every antigen uses the default "sum" mode.
