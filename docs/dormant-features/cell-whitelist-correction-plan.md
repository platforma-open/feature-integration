# Plan — optional cell-barcode whitelist correction

**DECISION (2026-07-02): the user-facing whitelist selector was REMOVED for v1; FI is de-novo only.**
Rationale (code review): the selector is not spec-required (the spec never mentions a whitelist,
chemistry, or CELL-correction scheme; A-0018 defers only *antigen-tag* correction, A-0003 requires
the cellId link but not a mechanism); de-novo already yields the ~99% join empirically; only the 5' v2
option was verified, while the other four carried real footguns (the `umiLen`/whitelist decoupling —
GEM-X/v3 needs a 12 bp UMI but the field defaults to 10 — plus unverified VDJ-side alignment since the
mixcr `analyze` preset YAMLs aren't in the checkout, and 3'/Multiome options that don't fit a 5'
assay); and the one payoff (a deterministic join) can't be verified until the blocked 3-block e2e
runs. Making cellIds deterministic across producers is really a chain-level concern: the chemistry
(→ whitelist + geometry) is a dataset property that should be set once upstream (Samples & Data) and
inherited by every producer (mixcr-clonotyping AND FI), not re-asked per block. Revisit then.

What was removed: only the `ui/src/pages/MainPage.vue` dropdown + its options list. The `#builtin:`
plumbing (`BlockArgs.cellWhitelist`, the `extra` threading in `main.tpl`, and the conditional CELL tag
in `fb-pipeline.tpl`) is left DORMANT (`cellWhitelist` stays `""` → de-novo always) as the documented
seam for reviving this per the plan below. The sections that follow are the original (superseded) plan.

---

**Status:** implemented on `MILAB-6496_feature-integration-wip` (2026-07-01; `build:dev` green,
lint/format clean). Live e2e + mitool smoke-test still pending (see §8). **Default behaviour
unchanged** (de-novo). This plan adds an *optional*, chemistry-selected cell-barcode whitelist
correction to Feature Integration (FI).

**One-line:** let a user point FI's `refine-tags` at the 10x cell-barcode whitelist that matches their
chemistry, so FI's `cellId` strings are identical-by-construction to the VDJ block's — making the
downstream per-clonotype join deterministic instead of ~99% probabilistic. When unset, FI keeps
today's de-novo correction.

**Relationship to prior docs:** supersedes the local evaluation `cell-barcode-whitelist-correction.md`
("Option D", gitignored). That doc evaluated *whether* to do this and recommended deferring; this plan
adds the empirical BEAM-T verification (2026-07-01) it predates and specifies *how*, as an optional
knob. The evaluation's science/field-review detail is not repeated here.

---

## 1. Problem

FI produces per-cell feature columns keyed on `pl7.app/sc/cellId` — the 16 bp 10x cell barcode
string. The downstream **VDJ Multiomic Integration** block joins those columns to VDJ clonotypes on
`[sampleId, cellId]` (its `aggregate.tpl` materialises the feature column + the cell-linker to CSVs
and joins them in Python). For a row to survive, the *same cell* must carry the *same barcode string*
on both sides.

The two sides currently correct barcodes differently:

- **FI (today):** `refine-tags -t CELL` — **de-novo**: clusters the barcodes observed in the
  feature-barcode library and collapses low-count variants onto high-count neighbours, using that
  library's own frequency distribution.
- **VDJ side (mixcr → mitool):** corrects `CELL` against the fixed 10x whitelist. mitool's 10x preset
  sets `CELL: builtin:737K-august-2016` (`tools/mitool/.../mitool_presets.yaml`), and mixcr's tag
  correction runs through mitool. (The `WhitelistReader` copy in the mixcr repo is unused/legacy.)

Because de-novo depends on this library's data, a cell can come out as string `X` from FI but `X′`
from the VDJ side; when `X ≠ X′` the cell silently drops from the join.

## 2. Empirical evidence (real 10x BEAM-T data, 2026-07-01)

Verified against `5k_BEAM-T_Human_A0201_B0702_PBMC_5pv2` (streamed from the tar; antigen + vdj R1):

- R1 = 26 bp (16 CB + 10 UMI) — 5′ v2, as expected.
- The join **works today**: for real cells (R1 barcode count ≥ 200) **99.1%** of VDJ cells' barcodes
  are present in the antigen library and **98.6%** vice-versa — on *raw, uncorrected* barcodes. Low
  overlap appears only in the count ≥ 5 ambient/error tail (expected noise, not a mismatch).
- Feature-barcode geometry (`featureLen 15`, feature at R2[0]) is **correct** for this data (the R2
  panel is a discrete ~4-antigen set at offset 0); no geometry change needed.

**Reading:** de-novo already yields a ~99% join for real cells, so it is *acceptable for v1*. The
whitelist's value is turning that ~99% (which depends on read distributions) into a deterministic
~100% and dropping off-whitelist junk barcodes — a robustness gain, not a correctness rescue.

## 3. Decision

Add the whitelist as an **optional, chemistry-selected knob, defaulting to de-novo.** This is the
minimal, forward-compatible slice of the deferred **DP-1** ("parameterize + proceed") work:

- **Default de-novo** ⇒ v1 behaviour is unchanged; non-10x assays and the synthetic e2e test keep
  working; nothing is hardcoded to one chemistry (spec-aligned — see §6).
- **When set** to the chemistry's list (5′ v2 → `737K-august-2016`; 5′ v3 → `3M-5pgex-jan-2023`),
  FI's cell-calling matches the VDJ side by construction.

**Recommended timing:** ship the knob now. It is additive (~2 lines of behaviour + plumbing), changes
nothing by default, and gives real 10x runs the deterministic join for free. Deferring it wholesale
(the Option-D recommendation) is also defensible given de-novo's ~99%; the knob is the cheaper middle
path and removes the deferral's only real cost (users stuck on de-novo).

## 4. The plan (FI only)

The whitelist ships inside the mitool jar as built-ins (`SequenceSetCollection.kt`), so **nothing is
bundled**; `#builtin:<name>` is the same `#`-address mechanism the FEATURE panel already uses
(`#file:panel.txt`).

| File | Change |
|---|---|
| `model/src/types.ts` | `BlockArgs`: add `cellWhitelist: string` (`""` = de-novo). `BlockData`: add `cellWhitelist?: string`. |
| `model/src/index.ts` | `init`: `cellWhitelist: ""`. Args lambda: `cellWhitelist: data.cellWhitelist ?? ""`. |
| `workflow/src/main.tpl.tengo` | Add `cellWhitelist: args.cellWhitelist` to the processColumn `extra` map (always a string → no body stall). |
| `workflow/src/fb-pipeline.tpl.tengo` | Read `cellWhitelist := inputs.cellWhitelist`; build the CELL tag arg conditionally; use it in `refine-tags`. |
| `ui/src/pages/MainPage.vue` | Add a "Cell barcode whitelist (10x)" `PlDropdown` in Advanced Settings (options below). |

Core workflow change (`fb-pipeline.tpl.tengo`, replacing `arg("-t").arg(tagPattern.CELL_TAG)`):

```go
cellTag := tagPattern.CELL_TAG
if !is_undefined(cellWhitelist) && cellWhitelist != "" {
    cellTag = tagPattern.CELL_TAG + "#builtin:" + cellWhitelist
}
// ... refine-tags ... arg("-t").arg(cellTag) ...
```

UI options (static — the mitool built-ins for 10x chemistries):

```ts
const cellWhitelistOptions = [
  { value: "", label: "None — de-novo (non-10x / synthetic)" },
  { value: "737K-august-2016", label: "10x 5' v2 / 3' v2 (737K-august-2016)" },
  { value: "3M-5pgex-jan-2023", label: "10x 5' v3 (3M-5pgex-jan-2023)" },
  { value: "3M-february-2018", label: "10x 3' v3 / v3.1 (3M-february-2018)" },
  { value: "737K-arc-v1", label: "10x Multiome ATAC+GEX (737K-arc-v1)" },
];
```

Optional hardening (not required): warn in the args lambda if the whitelist/`umiLen` pair is
inconsistent (e.g. `737K-august-2016` with `umiLen ≠ 10`); validate the name against the known set.

## 5. How it affects downstream blocks

**VDJ Multiomic Integration (block 2): no change required.** It only *matches* barcodes; it never
corrects them. This plan changes only the cell-barcode *values* FI emits, not FI's interface — the
axis spec stays `pl7.app/sc/cellId` (String, no domain), column names/specs/export shape are
unchanged, and block 2's discovery selectors are value-agnostic. Effect on block 2 is strictly
positive: its join gains rows (exact match instead of ~99%).

**mixcr-clonotyping (upstream VDJ producer): no change.** It already corrects cells against the 10x
built-in via its 10x preset; this plan makes FI match it.

**GEX block (cell-ranger / import-sc-rnaseq), if block 2 consumes GEX: no change** — those already
correct against the 10x whitelist, so aligning FI puts all of block 2's cell-keyed inputs on the same
canonical barcode.

Barcode correction belongs at the producer (which holds the reads); block 2 has no reads and must not
correct anything.

## 6. Spec — why it should / shouldn't be added

- **Should (satisfies an invariant):** **A-0003** — "the cell barcode … is the key that ties [the
  libraries] together." The whitelist makes that linking deterministic across FI and the VDJ producer.
- **Permitted, not required (implementation choice):** **A-0018** — "barcode parsing and error
  correction of antigen tags are left to implementation; this spec does not prescribe an
  error-correction scheme." So both de-novo and whitelist are spec-valid; this is an engineering
  call, and the spec explicitly does **not** mandate the whitelist.
- **Why optional / chemistry-driven, not hardcoded:** A-0018 (and the absence of any geometry/
  chemistry atom) means the block must stay general. The whitelist is chemistry-specific, so hardcoding
  one list would over-fit to 10x 5′ v2 — contrary to the spec's deliberate silence. Default de-novo +
  a chemistry selector keeps it general. This is the deferred **DP-1** "parameterize + proceed" seam.
- **Panel is unaffected:** **A-0004** — the tag→feature panel is the user CSV; this plan concerns the
  *cell* barcode only, which is chemistry-determined, not CSV-determined.
- **Tooling:** **A-0016** — antigen-tag processing uses mitool; the built-in whitelists are mitool's,
  so this stays within the sanctioned toolchain.

## 7. Tradeoffs

| | De-novo (today / default) | Whitelist (opt-in) |
|---|---|---|
| Cross-block join | ~99% on real cells; depends on read distribution | Deterministic; canonical barcodes |
| Requires chemistry input | No | **Yes** — user must pick the matching list |
| Non-10x / synthetic data | Works | Would drop all barcodes (not on any 10x list) → must stay de-novo |
| Junk/ambient barcodes | Kept unless out-clustered | Dropped if off-whitelist (cleaner cell set, matches Cell Ranger/mixcr) |
| Failure mode if misconfigured | n/a | Wrong list for the chemistry ⇒ most cells dropped — worse than de-novo |
| Cost | 0 | ~2 lines + a dropdown; a chemistry selector to maintain as 10x adds lists |

The dominant risk is **misconfiguration** (wrong list → mass drop), which is why the default is
de-novo and the option is chemistry-labelled.

## 8. Open questions / verification

- **Confirm which built-in mixcr-clonotyping's 10x preset applies for the target chemistry**, so FI's
  recommended selection points at the identical list. (Traced FI→mitool→737K; the mixcr-preset→mitool
  link is inferred, not fully traced.) [TODO]
- **Smoke-test `refine-tags -t CELL#builtin:737K-august-2016`** on the BEAM-T antigen library —
  **[DONE 2026-07-01]**. 200k antigen reads (5k_BEAM-T 5′ v2, L001) parsed 100% (geometry confirmed:
  CELL 16 @ 0, UMI 10 @ 16, FEATURE 15 @ R2 0). `refine-tags` with `-t CELL#builtin:737K-august-2016`
  resolved the built-in from the jar and ran to completion; it filtered a 9.0% diversity tail of
  off-whitelist (error/ambient) cell barcodes while retaining ~98% of records — confirming the list is
  correct for 5′ v2 and that the whitelist prunes the junk tail. The de-novo control filtered 0% by
  whitelist (unchanged default). Corroborates the 10x→mitool chemistry mapping (`737K-august-2016` =
  3′ v2 / 5′ v1–v2).
- **Live 3-block e2e** (mixcr-clonotyping → FI → block 2) to confirm the deterministic join end to
  end — currently gated by the local-runner blob-staging blocker.

## References

- Spec atoms: `work/projects/beam-seq/work/atoms/` — A-0003 (cell barcode linking key), A-0018 (error
  correction deferred), A-0004 (tag CSV authoritative), A-0016 (mitool).
- mitool built-ins: `tools/mitool/.../pattern/SequenceSetCollection.kt`,
  `tools/mitool/.../resources/mitool_presets.yaml` (`CELL: builtin:737K-august-2016`).
- Prior evaluation: `docs/cell-barcode-whitelist-correction.md` (local, gitignored).
- Empirical verification: 10x public dataset `5k_BEAM-T_Human_A0201_B0702_PBMC_5pv2`.
