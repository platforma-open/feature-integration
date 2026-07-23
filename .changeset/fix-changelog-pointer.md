---
'@platforma-open/milaboratories.feature-integration': patch
---

Fix the block changelog pointer. `block.meta.changelog` pointed at `file:../CHANGELOG.md` (the repo-root "Initial release" stub), so every published block-pack shipped the 1.0.0 stub and the desktop update view showed no release notes. Point it at `file:./CHANGELOG.md` — the changesets-generated block changelog.
