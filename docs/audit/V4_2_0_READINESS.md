# AlphaLab v4.2.0 Readiness Report

**Candidate:** branch `work/v4.1.1-future-readiness`, draft PR #18, created from
`main` at `33e61d3edbd041bcd125d5dea913c00cdc9da613` -- the commit the `v4.1.0`
tag names, which `main` and the tag still name. The package declares `4.2.0`
(`alphalab/common/_version.py`), approved by the maintainer as the development
target; nothing is tagged, released, uploaded or merged, and the published
v4.1.0 artifacts were not touched.
**Companions:** the ledger (`PRE_V4_COMPLETION_LEDGER.yaml`), ROADMAP's *v4.2.0*
and *Future work* sections, [ADR-0051](../ADR/0051-v4-1-1-information-on-every-driver-and-the-strategy-capability-audit.md)
(decisions 13 to 21), [`STRATEGY_CAPABILITY_MATRIX.md`](STRATEGY_CAPABILITY_MATRIX.md),
[`STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md),
and the v4.1.1 program's own report, [`FUTURE_READINESS_V4_1_1.md`](FUTURE_READINESS_V4_1_1.md),
which this one supersedes for v4.2.0. Every figure below was read from a
command's own output in the session that produced it.

---

## 1. Verdict

**The engine is complete for what its ledger, ROADMAP and this report state, and
not globally.** Every item the v4.1.1 program accepted as future work for this
release -- FUT-002, FUT-009, FUT-011 and FUT-013 to FUT-017 -- is delivered, each
in its own commit with its tests and mutation evidence, and the v4.2.0 global gap
audit fixed the one correctness-relevant gap it found (EXE-012). Four engine gaps
remain, each accepted on the ROADMAP with its interim behaviour and completion
criteria (FUT-018 to FUT-021), and the stated limitations of section 5 stand.
AlphaLab ships no market's rules, data or venue: a market is supported when its
calendar, settlement rule, currency, contracts and margin are declared.

**Release readiness is the maintainer's call, and the evidence supports it as a
minor release candidate**: every gate this report lists passes on the final
tree, on three interpreters, in a clean mirror and on hosted CI, with every
earlier run's identity where it was. Publication, tagging and the merge of PR
#18 are not done and need the maintainer's separate authorization.

---

## 2. Starting state of this request

At the start: HEAD `6025f33` (FUT-015) with FUT-017 uncommitted in the tree;
`5aca127`, `2b9ef14`, `1c610c7` and `6025f33` committed locally and unpushed,
because the "protect" ruleset refused every push (GH013); the remote branch and
PR #18 at `b553a4e`, whose hosted CI had passed everything but the two
version-keyed guards that the version commit closes. The ruleset was then found
with its enforcement disabled -- a change the maintainer made, read and not
altered -- and the branch was pushed by fast-forward only.

## 3. The v4.2.0 milestones

| Item | Commit | What it delivers | Evidence |
| --- | --- | --- | --- |
| FUT-011 | `6589095` | a declared queue model for resting limit orders | 32 integration tests; 15 mutants caught |
| FUT-002 | `b553a4e` | a strategy configuration read back as its declared type | 62 regression tests; 13 mutants caught |
| version | `5aca127` | 4.2.0 the development version; the three version-keyed guards pass | metadata and preflight guards |
| FUT-014 | `2b9ef14` | a strategy exercises its own options from its hooks | 18 integration tests; 11 mutants caught |
| FUT-016 | `1c610c7` | baskets, cash in lieu, foreign and quanto consideration, Bermudan exercise, adjusted terms | 24 integration tests; 13 mutants caught |
| FUT-015 | `6025f33` | settlement-date accounting; unsettled proceeds out of buying power | 11 integration tests; 10 mutants caught |
| FUT-017 | `1dcfe99` | margin calls and forced liquidation on declared margin terms; financed deliveries; the enum-pin scanner reading every decoder | 35 + 13 integration tests, 4 milestone-interaction tests; 36 mutants caught |
| EXE-012 | `1d07a88` | a strategy withdraws its own working orders; the gap audit's records | 12 integration tests and an allocation test; 10 mutants caught |

FUT-009 and FUT-013 were delivered by `dc508e9`, the candidate commit before
this program's milestones.

**FUT-017 in particular.** Margin is declared per instrument (`MarginTerms`: a
rate of value or an amount per contract, initial and maintenance, a collateral
haircut, a liquidation priority); the account is checked after every market
step and, below maintenance, called (`MarginCall`) and liquidated -- each
holding strategy's own target through allocation, risk and the OMS, resting to
its asset's next event and filling by the run's fill policy at a price the
market then shows, in part or not at all as the market allows, and worked by an
asset's declared algorithm under an execution plan. A step still in breach calls
again from the book as it is; a working liquidation is never placed twice. Before
an order, growth in a margined position is held to the initial requirement,
working orders counted, and a margined future spends its margin of buying power
rather than its notional. A physical delivery the cash cannot pay is financed
only when every deliverable declares margin terms -- an obligation is booked and
called, an instructed exercise financed only within the initial requirement --
and refused, as before, otherwise. It was tested by breaches from a price move,
an exchange rate, a futures variation loss and a delivery; constrained
liquidity; a missing quote; partial fills; repeated breaches; replay and a pause
at every boundary; duplicate protection; and its meeting points with FUT-009,
FUT-011, FUT-014, FUT-015 and FUT-016.

## 4. The global gap audit

Read against the objective -- a researcher's own strategy over heterogeneous
inputs, in any declared market, from research to production -- rather than the
FUT list, through the code: the capability matrix's every section, the strategy
contract's intent kinds and the drivers' operations, the corporate actions the
portfolio books, the order types and venue rules, and a search of the package for
hard-coded regional assumptions.

| Finding | Class | Disposition |
| --- | --- | --- |
| A strategy could not withdraw its own working order; re-targeting traded against it (a target of zero beside a resting buy sold short) | 1, correctness-critical | **Fixed**: `IntentKind.CANCEL` (EXE-012), additive |
| Brackets, one-cancels-other, trailing stops | 6, resolved | composed from `CANCEL` over the hooks; no order type needed |
| Buying a fully paid instrument on margin; interest on a debit; short borrow availability and fees | 2, material | **FUT-018** (v4.3); a debit's interest and a borrow fee are bookable as declared cash flows meanwhile |
| Portfolio or risk-based margin; the risk gate's `MarginLimit` a notional cap | 2, material | **FUT-019** (v5.0) |
| A venue's price limits, collars and halts | 2, material for limit-locked markets | **FUT-020** (v4.3) |
| A holding exchanged for others: stock merger, spin-off, rights | 2, material | **FUT-021** (v4.3) |
| An insolvent run's analytics refused | 4, accepted limitation | **LIM-008**: correct refusal kept; `compile_analytics=False` keeps the books |
| Seven persisted enums unpinned (decoders not named `from_primitives`) | 1, correctness-critical (format stability) | **Fixed** with FUT-017: the scanner reads every decoder by name, its helpers and tables |
| A short bought back past the cash stopped the run | 1, correctness-critical | **Fixed** with FUT-017 |
| Hard-coded US conventions | 6, none found | the only literals are docstrings and the ISO 4217 table |
| Market depth (L2), real venues and data, holiday and reference data, text-to-sentiment models, hosted isolation of strategy code | 3, external boundaries | unchanged (BDY-003, BDY-013, matrix 2.15, 2.24) |
| Feature store and portfolio construction off the drivers | 4, by design | unchanged (matrix 2.17, 2.20) |

## 5. Material known limitations

Each is in the ledger entry it belongs to and on the ROADMAP.

* **Margin** (FUT-017): per position, summed, no offsets (FUT-019); no grace
  period; checked at market events, not between them; only a delivery is
  financed, and a debit accrues no interest (FUT-018); risk's `MarginLimit` and
  an allocation budget stay notional caps the user configures.
* **Cancellation** (EXE-012): only what the strategy alone has working; no amend
  in place; a new target still counts a resting order as held.
* **Settlement** (FUT-015): the books are trade-date; units bought and unsettled
  may be sold; no settlement fail or interest on unsettled balances.
* **Contract terms** (FUT-016): a package's components share one currency; cost
  is apportioned by observed values; a quanto fixes its consideration, not its
  payoff.
* **Exercise** (FUT-014): effective at the end of the asking step; a cash
  contract settles against the last observed underlying price.
* **Queue model** (FUT-011): the displayed top of the book only; no fill
  probability.
* **Configuration** (FUT-002, LIM-004): an undeclared configuration reads back
  as JSON reads it.
* **Insolvency** (LIM-008): a run below zero compiles no analytics.
* **Venues and markets**: price limits and halts (FUT-020), holding exchanges
  (FUT-021); a backtest simulates one venue; Windows is untested (BDY-027).

## 6. Validation on the final tree

Run on the committed tree `1d07a88` in the checkout's dev venv (CPython 3.12.4)
unless stated; the clean mirror is the tree's tracked files and nothing else.

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `python -m ruff check .` | all checks passed |
| Format | `python -m ruff format --check .` | 1,325 files already formatted |
| Types | `python -m mypy .` | no issues in 1,222 source files |
| Workflows | `actionlint` with `shellcheck` | clean |
| Tests | `python -m pytest -W error` | **10,258 passed, 0 failed**, 0 skipped |
| Examples | every `examples/[0-9]*.py` under `python -W error` | all 75 exit 0 |
| Fresh 3.12 | the suite in a fresh venv from the clean mirror | CPython 3.12.4: **10,258 passed** |
| Fresh 3.13 | the same | CPython 3.13.18: **10,258 passed** |
| Fresh 3.14 | the same | CPython 3.14.10: **10,258 passed** |
| Certification | `python -W error docs/audit/scripts/certify_release.py --check` | 11 checks passed, matching the certificate; across the program's re-certifications only the source digest, the API manifest's digest and the export count moved -- no determinism, parity, reproducibility or numerical figure, and no `result_id` |
| Public API | `python docs/api/generate_public_api.py` and the manifest tests | 2,695 exports, 35 shared names (2,628 at v4.1.0); additions and defaulted keywords or fields only, each in CHANGELOG `[4.2.0]` |
| Historical inventory | `python docs/audit/scripts/historical_inventory_v4.py --check` | current |
| Build | `python -m build` in the clean mirror | `alphalab-4.2.0.tar.gz`, `alphalab-4.2.0-py3-none-any.whl` (kept in the scratchpad; `dist/` untouched) |
| Distributions | `python -m twine check --strict` | both PASSED |
| Installed artifacts | `release_preflight.py distributions <dir> --version 4.2.0` | **PASSED**: each artifact alone in a fresh environment, `pip check` clean, no runtime requirement, 614 modules, 2,695 public names as the manifest records, examples 11 and 70 to 75 and the consumer contract run with the network refused; wheel `4bc714bc...4c16`, sdist `2ae5fc5d...0caf` |
| Compatibility | the v4.1.0 frozen replay payload and the v3.9 to v3.13 schema-upgrade suites | 202 passed: the v4.1.0 payload reads, re-captures byte-identically and resumes to its reference outcome |
| Mutation | each milestone's rules broken in a scratch copy | FUT-011 15/15, FUT-002 13/13, FUT-014 11/11, FUT-016 13/13, FUT-015 10/10, FUT-017 36/36, EXE-012 10/10 caught |
| Whitespace | `git diff --check` | clean |

**Performance**, against `git archive v4.1.0` on one host, one after the other,
the run benchmarks under `-W error` (judged: CPU time, collector paused, fastest
of three): backtest scaling 3.97x against v4.1.0's 4.02x; replay 1.03x the
backtest against 1.03x; pipeline scaling 3.99x against 4.00x; every parity line
identical. The run layer's cost over the bare step, measured four times on each
tree alternately, was +1.64% to +2.51% (median about +2.3%) against v4.1.0's
+1.04% to +2.40% (median about +1.2%): the ranges overlap, and the program's
earlier milestones measured +2.07% to +2.56% on both, so at most about one point
of a two-per-cent overhead is attributable, and it is not separable from the
spread here. A run declaring no margin pays one emptiness check per market step
and a keyed lookup per order for FUT-017; a queue model's cost is the FUT-011
milestone's measurement (7.42 s against 6.84 s at about 150 resting orders over
6,000 events).

## 7. Identity, compatibility and reproducibility

Every v4.2.0 capability is opt-in through state outside a run's record --
instrument records the registry holds, transaction metadata, enum members and
event tags written only when used, driver-held books -- so a run of v4.1.0's
inputs has the identity it had. The evidence: the certificate's determinism,
parity, reproducibility and numerical checks pass unchanged against every
re-certification in the program (only the source digest, the manifest's digest
and the export count moved); the frozen v4.1.0 replay payload reads, re-captures
byte-identically and resumes to its reference outcome; the v3.9 to v3.13 schema
upgrade suites pass, every new record field reading as absent; and the margin
milestone's own identity test runs margin terms that never bind to the same
`result_id` as no terms at all. Two schemas stepped in the candidate (instrument
3 -> 4, replay 1 -> 2), each with an upgrade; none in this program.

## 8. Remote state

Every milestone commit is pushed; `origin/work/v4.1.1-future-readiness` and
local HEAD named `1d07a88` before this report's own commit, and the branch
advances by fast-forward only. PR #18 is open, a draft, based on `main`. Hosted
CI on `1d07a88`: the six Quality Gates jobs (Ubuntu and macOS under CPython
3.12, 3.13 and 3.14) passed, as did CodeQL; on `1dcfe99` (FUT-017) and `6025f33`
(FUT-015) the same. The jobs that gate a release (preflight, distributions,
benchmarks, the release verdict) are skipped on a pull request by design and
run on the release branch. This report's commit changes documentation only; its
hosted run is recorded on the PR.

## 9. Confirmations

* v4.2.0 metadata is consistent: the version, CHANGELOG `[4.2.0]` (unreleased),
  README and docs markers, `docs/api/history/4.2.0.json`, the preflight
  workflow's release version and the certificate's engine string.
* `main` was not modified: it names `33e61d3`, as the `v4.1.0` tag does.
* The published v4.1.0 tag and artifacts were not touched; the benchmark and
  compatibility comparisons read the tag through `git archive`.
* No tag was created or moved, no release published, no artifact uploaded, and
  no merge performed. PR #18 is a draft.
