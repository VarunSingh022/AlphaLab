# AlphaLab v4.0.0 Release Audit

**Candidate:** the uncommitted working tree on the local branch
`work/v4.0.0-qa-hardening`, based on `main` at `a04a3d1` -- the `v3.13.0` tag
(`6e9daea`) plus three later commits that refreshed the v3.13 certificate and
fixed formatting and typing in two tests. Nothing in this audit was committed,
tagged or pushed; hosted CI has **not** run on the candidate and is pending the
maintainer's push.
**Role:** independent QA, historical archaeology, engine hardening and
release-candidate preparation, done as somebody who had not written the code.
**Companions:** [`V4_HISTORICAL_INVENTORY.md`](V4_HISTORICAL_INVENTORY.md) (the
archaeology, generated), [`PRE_V4_COMPLETION_LEDGER.yaml`](PRE_V4_COMPLETION_LEDGER.yaml)
(272 entries), [ADR-0049](../ADR/0049-the-v4-freeze-the-history-classified-the-engine-re-audited-and-the-public-contract-held.md),
`scripts/historical_inventory_v4.py`, `scripts/mutation_v4_0.py`,
`scripts/stress_v4_0.py`, `scripts/generate_v3_13_0_snapshot_fixtures.py`.

This document records what was examined, what was found, what was fixed and
with which test, what was measured, and what was **not** tested. Historical
documents -- the pre-v4 master audit, earlier ADRs, earlier CHANGELOG sections --
are not rewritten; a pointer to this record was added to the master audit's
status block.

---

## 1. Verdict

The canonical path was sound where it matters most: on 1,000 generated runs
its fills, cash, positions, equity and the conservation of money matched an
independently kept ledger exactly once one defect was fixed; backtest, replay
and paper produce identical runs; a seeded run is byte-identical across
processes and hash seeds. Four defects were found at its edges and fixed, each
with a regression test that fails on the v3.13.0 tree (section 5), and the
frozen public API was found to leave out the surfaces the guides teach --
`alphalab.api` and the snapshot modules -- which it now records (API-007). The
archaeology found that 257 historical deferral items had never been
classified; every one now is, with evidence, and a guard holds them (section
4). One item is accepted future work with acceptance criteria (FUT-001); three
limitations are stated rather than hidden (LIM-004 to LIM-006).

The candidate is ready for the maintainer's review **on the evidence of the
local gates in section 12**, which reproduce `ci.yml` and `benchmarks.yml`
locally. It is not "free of defects": section 7 states what was and was not
tested.

---

## 2. Starting state

| | |
| --- | --- |
| Repository | `/Users/varunkumarsingh/developer/AlphaLab`, branch `main` at `a04a3d1`, working tree clean |
| Tags | 75; `v3.13.0` -> `6e9daea`. No tag was created, moved or deleted. |
| Branch | `work/v4.0.0-qa-hardening` created locally from `main`; never pushed |
| Baseline suite | 9,041 passed in 143.89 s on a clean export of `main`, `pytest -W error` |
| Baseline in the checkout | 1 failure: `test_lifecycle_governance.py::test_the_engine_holds_no_identity_system` |

The checkout failure is not in the code. Seven packages removed in v3.10 to
v3.12 (`feed` and `live` in v3.10; `enterprise`, `studio` and `workbench` in
v3.11; `optimizer` and `plugins` in v3.12), five `marketdata` vendor
subpackages and their test directories survive in this checkout as **gitignored directories holding only
`__pycache__`**. Python imports such a directory as a namespace package, so
`import alphalab.enterprise` succeeds and the test that asserts the engine holds
no identity system fails. CI's fresh checkout cannot have them. Moving them was
not authorized during this audit, so every gate ran in a clean mirror of the
candidate (`git ls-files -co --exclude-standard`, copied), never in the
checkout; the maintainer can delete the directories that contain no `.py` file.

---

## 3. Method and progress log

Every probe ran against the candidate and, for each defect, against an export
of the `v3.13.0` tag, so "fails before the fix" is an observation, not a claim.
Probes were throwaway scripts in a scratch directory; what they established is
now a test.

1. Git checks; branch created; baseline suite on a clean export (section 2).
2. **Archaeology.** Read every ADR's non-goal, not-in-scope and deferral
   section, every CHANGELOG gap, limitation, deferral and still-open section
   from v2.0.0 to v3.13.0, `nowandfuture.md`'s open questions and the strategy
   runtime design's open questions; compared with the ledger (section 4).
3. **Strategy-building exercise** from the documentation alone (section 8):
   found DOC-009 (no guide), and DAT-010 and NUM-015 among the refusal cases.
4. **Restore probe** from ADR-0029's non-goal "no live-object parameter
   persistence": found PER-008.
5. **Numerical review** of options against Hull and central differences:
   NUM-015's reach (every entry point, every input), the finite values right.
6. **Differential accounting test**: 1,000 generated runs against an
   independent ledger; found EXE-011; 0 discrepancies after the fix.
7. Fixes, each test first; pre-fix failure confirmed on the `v3.13.0` export.
8. Schema steps (pipeline 7 -> 8, run 4 -> 5), every test that pins a schema
   number updated, upgrades tested on v3.9.0, v3.11.0, v3.12.0 and -- added in
   this release -- v3.13.0 payloads.
9. Inventory script, generated inventory and `HIS-xxx` ledger entries; guards
   extended to accepted future work.
10. Stress program extended and run; mutation harness extended (199
    mutations, 201 after the final pass) and run.
11. Public API regenerated and compared with `history/3.13.0.json`.
12. Documentation inventory and rewrite (section 11); cross-consistency sweep.
13. **Final reading** (Phase H), done after the first full gates: found API-007
    (example 70's imports checked against the manifest), the missing v3.13.0
    payload fixtures, and a guard a freeze falsifies
    (`test_the_comparison_finds_what_it_should`); each fixed. The first
    examples pass found example 47 failing its own invariant -- its "hidden
    change" was a cost, which PER-008 made visible -- and it was rewritten.
14. Certificate regenerated after the last change to `certify_release.py` and
    the manifest; full gates re-run on the final tree (section 12).

---

## 4. Historical archaeology, v2.0.0 to v3.13.0

**Coverage.** The pre-v4 ledger had classified ROADMAP's boundaries and its
optional list (v3.10) and the release ADRs' "Known limitations" and "DEFERRED"
lists, ADR-0042 to ADR-0044 (v3.13, 52 items). It had never read:

| Source | Sections | Items |
| --- | --- | --- |
| ADR-0010 to ADR-0035 (v2.2.0 to v2.17.0) | "Not in scope", "Explicit non-goals", "Consequences of deferring" | 25 sections |
| CHANGELOG 2.0.0 to 3.13.0 | "Known gaps", "Known Limitations", "Not in scope", "Deferred, unchanged", "Recorded", "Still open" | 12 sections |
| `nowandfuture.md` (v3.0.0 to v3.13.0) | 20. Open questions | 1 section |
| `docs/architecture/strategy/STRATEGY_RUNTIME_DESIGN.md` | 11. Open Questions for Future Phases | 1 section |
| **Total** | | **39 sections, 257 items** |

**Classification** (`V4_HISTORICAL_INVENTORY.md`; the counts sum to the 257
items, and a test holds the inventory and every document that states the count
to that one number):

| Status | Items | Meaning |
| --- | ---: | --- |
| DELIVERED | 115 | built by a later release; the evidence names the release and test |
| BOUNDARY | 80 | kept out on purpose; the ledger entry gives the reason |
| EXPIRED | 17 | a fence around one release's scope |
| REMOVED | 14 | the thing it concerned left the library |
| LIMITATION | 10 | kept as a stated limitation |
| EXTERNAL | 9 | the host application's or a vendor's to supply |
| SUPERSEDED | 9 | replaced by a later decision |
| FUTURE | 2 | accepted future work (both are FUT-001, stated by ADR-0029 and ADR-0030) |
| FIXED-v4 | 1 | PER-008 |

**What it found.** Two items concealed live gaps: ADR-0029's "no live-object
parameter persistence" (behind which a restore accepted a simulator charging
another commission: PER-008) and the replay that ADR-0029 and ADR-0030 left
non-resumable (FUT-001). One limitation, a certified identity's dependence on
the host's `libm`, was stated only in the v3.13.0 CHANGELOG (LIM-005). A
strategy's configuration persisted as JSON reads it was stated by no document
(LIM-004, found by probe while classifying ADR-0025). The scale questions of
`nowandfuture.md` are LIM-006. `nowandfuture.md`'s "known caveat" -- datasets
ingested with an empty payload sharing a version -- had been fixed in v3.10
(KD-004) and was still listed as open; it is corrected.

**ADR-0042 to ADR-0044 revisited.** Their 52 items were re-read against the
code: every classifying entry exists and is closed, and
`test_every_adr_deferral_is_classified.py` still holds each; ADR-0045 to
ADR-0049's lists are held the same way (ADR-0049's to LIM-004 to LIM-006 and
FUT-001).

**Regression protection.** `test_every_historical_item_is_classified_once`
re-reads the ADRs and the CHANGELOG and requires each stated item to match
exactly one classification; `test_the_inventory_and_its_ledger_entries_are_current`
requires the generated inventory and ledger block to be what the script writes
(TST-017).

---

## 5. Defects found and fixed

Each fix was made test first; every test named here fails on the `v3.13.0`
export and passes on the candidate.

| ID | Severity | Defect | Found by |
| --- | --- | --- | --- |
| PER-008 | high | A restore accepted a sizing model, simulator or fill policy configured otherwise than the captured run's; `configuration_id` could not tell two costs apart | archaeology (ADR-0029), then a restore probe |
| DAT-010 | high | Ingestion dropped rows a refusing policy should have refused; `MissingValuePolicy` was read by nothing | strategy-building refusal cases |
| NUM-015 | medium | Option pricing returned `NaN` for a non-finite input | strategy-building refusal cases; numerical review |
| EXE-011 | medium | A whole-unit run filled in fractions under `LiquidityCappedFill` | 1,000-run differential accounting test |

**PER-008.** *Root cause:* a snapshot recorded each live object by type name,
and `_require_object` compared only types (since v2.9 for the pipeline, v2.14
for the fill policy); the reproducibility record's configuration had the same
blind spot (since v3.6). *Reproducer:* capture under
`PerShareCommission(0.01)`, restore with `PerShareCommission(5.00)`: accepted;
the second fill paid 50.00 and the result described the whole run at 5.00.
*Fix:* `ConfigRecord.sizing_model_description`, `simulator_description` and
`RunSnapshot.fill_policy_description`, written by
`alphalab.runtime.assumptions.describe`; `restore` refuses a differing
description, naming both; the descriptions enter the recorded configuration.
Pipeline 7 -> 8, run 4 -> 5; an older payload reads as "not recorded" and is
checked by type, as before. *Tests:*
`tests/regression/test_restore_requires_the_captured_configuration.py` (8),
`tests/regression/test_schema_upgrades_v3_13.py` (16, on payloads the v3.13.0
tag wrote; added in this release, section 11).

**DAT-010.** *Root cause:* `ingestion` collected unreadable rows as rejections
for the quality report and never consulted the cleaning policy for them.
*Reproducer:* 12 daily bars, one close `NaN` (or `inf`, empty, `abc`), under
`REFUSE_EVERYTHING`: an 11-record dataset, no error, no transformation in the
provenance. *Fix:* `_govern_rejections` -- a missing value is governed by
`missing_values`, anything else by `invalid_records`; `REFUSE` raises
`DataQualityError` naming the policy, the count and the first row; a dropping
policy records `drop_row_missing_value` / `drop_unreadable_row` in the
provenance, so the dataset version changes. *Tests:* four in
`tests/regression/test_data_quality_contracts.py`; `test_trade_prints.py`,
which had asserted the old drop, corrected.

**NUM-015.** *Root cause:* validation compared `volatility <= 0`, which is
false for `NaN`, and checked no rate. *Reproducer:*
`black_scholes_price(vol=nan)` -> `Decimal('NaN')`; `black_scholes_greeks(rate=inf)`
-> `NaN` Greeks; a `NaN` spot -> `decimal.InvalidOperation`. *Fix:*
`_require_market_inputs` at every entry point (closed form, lattice, implied
volatility). *Tests:* `tests/unit/options/test_non_finite_inputs.py`. The finite
values were confirmed against Hull (S = K = 100, r = 5%, q = 2%, sigma = 20%,
T = 1: call 9.2270, put 6.3301) and every Greek against central differences;
none moved.

**EXE-011.** *Root cause:* `LiquidityCappedFill`'s share of the displayed size
reached the book unrounded even when `enforce_integer_quantities` was set.
*Reproducer:* buy 10 against 5 shown at a 50% cap -> 2.5 shares. *Fix:* in a
whole-unit run a partial fill is floored to whole units, and a cap below one
unit is no fill. *Tests:* three targeted tests and a 60-seed differential test
in `tests/regression/test_accounting_matches_an_independent_ledger.py`; 29 of
the 60 seeds fail on the v3.13.0 tree.

---

## 6. Other findings

| ID | Finding | Resolution |
| --- | --- | --- |
| TST-017 (high) | 257 historical deferral items never classified | inventory, `HIS-001`..`HIS-039`, two guards (section 4) |
| DOC-009 (medium) | No document said how to write a strategy; every example hand-wrote a clock, a logger and four supervisor transitions, and two replaced the runtime's whole strategy mapping | `start_strategy`, `context_factory`, `FixedClock`, `DiscardingLogger`; `GETTING_STARTED.md` "Build your first strategy"; example 70; README excerpts held to it by a test |
| Guard | `test_the_comparison_finds_what_it_should` required the API diff against the previous release to be non-empty -- false by design for a release that removes and rebinds nothing | rewritten to inject a removal, a rebinding and an enum change and require each found, and a module move not to be (not weakened: it now tests the comparison directly) |
| Gap | v4.0 moved two schemas without freezing what v3.13.0 wrote, which every earlier schema-moving release had done | `tests/fixtures/snapshots/v3.13.0` generated from the tag; `test_schema_upgrades_v3_13.py`; certificate REP-3 reads every frozen release |
| Docs | README's version badge read 3.11.0 and its test badge 8,215, held by no test | rewritten; the badge is a version marker |
| Docs | `docs/ARCHITECTURE.md`'s "Allowed Dependencies" named `feed` (removed v3.10) and three packages `research` never imported | a measured table, held to the AST import graph by `test_documented_dependencies_are_measured.py` |
| Docs | `docs/architecture/strategy/README.md` said `on_start`, `on_stop`, `on_fill`, `on_order` and `on_timer` were never delivered, two releases after v3.11 delivered them | corrected from the code and EXE-005 |
| Docs | ROADMAP invited vendor adapters into the library; `SECURITY.md` named `alphalab.enterprise`; `CONTRIBUTING.md` used a removed package in its commit examples | corrected |
| API-007 (medium) | The API manifest walked packages only, so `alphalab.api` -- "the surface a host platform calls" -- and each durable state's snapshot module (`capture`, `from_primitives`, `restore`, `RunObjects`, `RuntimeObjects`), which GETTING_STARTED and examples 13 and 70 use, were outside the frozen contract: 134 names any rename of which passed every guard | `DOCUMENTED_MODULES` recorded beside the packages and held to STATE_MODEL's durability table; four newly visible shared names given reasons; certificate API-1 counts packages and modules apart. Compared with v3.13.0's own modules: none removed, two changed by PER-008 (`RunSnapshot`, `PipelineSnapshot` schema defaults and one optional field) |
| Docs | example 68's "DIVERGED" case was a cost change, which PER-008 now identifies as INPUTS_DIFFER | the example shows both honestly |
| Tooling | the v3.12 stress program reported peak RSS in kilobytes on macOS, where `ru_maxrss` is bytes (it printed "293488 MB" for about 287 MB) | `_peak_mb` reads the unit per platform |

---

## 7. What was tested, and what was not

**Tested and found right.** Fills, commissions, cash, positions, realized and
unrealized P&L and equity on the canonical path, against an independent ledger,
across 1,000 generated runs with three commissions, two fill policies, two fill
timings, long and short positions and partial fills (0 discrepancies after
EXE-011; money conserved exactly on every run). Backtest/replay/paper parity
and determinism across processes and hash seeds (certificate DET, PAR). Options
against Hull's published values and central differences. Ingestion under every
cleaning policy with every kind of unreadable row. Restore with every live
object configured otherwise. Schema upgrades from every payload v3.9.0,
v3.11.0, v3.12.0 and v3.13.0 wrote. The strategy-building path end to end.

**By design, not a defect.** Realized P&L on a partial close relieves cost
basis proportionally in money, so it can differ by cents from a naive
average-cost figure; money is conserved exactly (`Position.cost_basis` is the
authoritative money figure since v2.1, `alphalab/portfolio/position.py`).

**Not tested in this release.** Any real venue, vendor or network service (the
library ships none, by boundary). Platforms other than macOS on Apple silicon
with CPython 3.12.4 locally; Linux is what hosted CI will exercise, pending.
Concurrency: the engine is single-threaded by design. Workloads beyond the
stress program's (section 10). Formal verification of the solvers beyond their
certified optimality checks. The mutation harness tests the suite's power to
catch the 201 injected defects it lists, not every possible defect.

---

## 8. Strategy-building acceptance (Phase D)

Done as an outside developer would, from `README.md` and `docs/` alone, through
public names only, then kept as `examples/70_build_a_strategy.py` and
`tests/integration/test_strategy_building_end_to_end.py`.

**Public API used** -- every name from a package's `__all__` or a documented
module (section 11), checked against the manifest: `alphalab.api`
(`ingest_rows`, `to_market_dataset`, `validate_dataset`, `backtest`);
`alphalab.data` (`IngestionRequest`, `REFUSE_EVERYTHING`, `raw_source_from_bytes`,
`DataQualityError`, ...); `alphalab.instrument` (`InstrumentRecord`,
`InstrumentRegistry`, `register_instruments`); `alphalab.market`
(`NormalizationPolicy`, `TimeFrame`); `alphalab.strategy` (`BaseStrategy`,
`Intent`, `IntentKind`, `StrategyContext`, `create_runtime`, `start_strategy`,
`context_factory`, `FixedClock`, `DiscardingLogger`); `alphalab.backtesting`
(`BacktestEngine`, `RunConfig`, `ExecutionMode`, `BacktestResult`);
`alphalab.runtime` (`ExecutionPipelineConfig`); `alphalab.execution`
(`ExecutionSimulator`, `PerShareCommission`, `FillTiming`); `alphalab.risk`
(the seven limits); `alphalab.portfolio` (`Account`); `alphalab.allocation`
(`CapitalBudget`, `AllocationConstraints`); `alphalab.common` (`id_scope`);
`alphalab.runtime.run_snapshot` and `alphalab.runtime.snapshot` (`capture`,
`from_primitives`, `restore`, `RunObjects`, `RuntimeObjects`);
`alphalab.persistence` (`serialize`, `deserialize`, `StateDecodeError`);
`alphalab.lifecycle` (`digest_run`). Before API-007 the last two modules'
names, and `alphalab.api`'s, were not in the frozen manifest.

**Observed output** (example 70 under `-W error`, `PYTHONHASHSEED=0`):

| Step | Observed | Expected by hand |
| --- | --- | --- |
| Data | 12 bars, dataset `EX70-SYNTHETIC@e0250c3f...` | 12 |
| Run | 12 records, 2 orders, 0 strategy failures | 2 crossings |
| Fills | buy 10 @ 101.00, commission 0.10; sell 10 @ 100.00, commission 0.10 | the same |
| Cash | 99,989.80 | 100,000 - 1,010.00 - 0.10 + 1,000.00 - 0.10 = 99,989.80 |
| Realized P&L | -10.00 | (100 - 101) x 10 = -10.00 |
| Flat series | 0 orders, 0 fills, cash 100,000.00 | no crossing |
| Rerun | same `result_id` | identical |
| Another seed | same fills, another `result_id` | identifiers differ |
| Stopped after 6 records, serialized, restored, continued | same `result_id` as the uninterrupted run | byte-identical |
| Refusals | 5 of 5: window `fast=4, slow=4` (`ValueError`); a `NaN` close (`InvalidRecordPolicy.REFUSE`); an empty close (`MissingValuePolicy.REFUSE`); a negative close (OHLC consistency); a continuation under another commission (`StateDecodeError`, PER-008) | each refused |

The example also runs from the clean wheel and sdist installs
(`tests/installed_smoke.py`, section 12).

---

## 9. What remains, and why it is safe

**Accepted future work.**

FUT-001 -- *a resumable replay* (target v4.1). *Limitation:* `ReplayBacktest`'s
cursor and its second identifier stream have no snapshot. *Impact:* a replay
interrupted midway must restart. *Reason for deferral:* a new envelope in the
release that freezes the formats is the wrong place to add one; it is additive.
*Interim safe behaviour:* a replay is a pure function of its dataset and seed
and restarts deterministically; a backtest of the same dataset produces the
same orders, fills and P&L (`test_backtest_and_replay_produce_an_identical_run`)
and resumes byte for byte. *Horizon:* v4.1. *Dependencies:* none outside
`alphalab.replay` and `alphalab.backtesting.replay`. *Completion criteria:* a
`ReplayState` snapshot and the cursor stream's `IdStreamPosition` captured with
the run, `ReplayBacktest.resume`, and a cross-process test that an interrupted
replay continued equals the uninterrupted one byte for byte (ROADMAP, Future
work).

**Stated limitations** (each a ledger entry with its test):

| ID | Limitation | Why it is safe to retain |
| --- | --- | --- |
| LIM-004 | A strategy's `configure` value is persisted as JSON reads it: a `Decimal` returns as a string, a tuple as a list | Nothing on the execution path reads it; the continued run's fills and identities equal the uninterrupted run's (tested); durable strategy state goes through `StrategyStateProtocol` |
| LIM-005 | Certified identities are per host class: another `libm` may round an analytics float differently in the last bit | The certificate records the host; `--check` names what moved; money is `Decimal` and unaffected |
| LIM-006 | The operating envelope is measured, not unbounded; the engine is single-threaded | Section 10 states the envelope; the host shards runs |
| PRF-011, PRF-012 | Per-order memory; the one-asset path's per-record cost | Measured in section 10 |
| DAT-007, DAT-008 | Time-zone database dependence; float-second instants (about 0.24 us resolution at current epochs) | Manifests and the certificate record the tz database version; same-instant events are ordered by sequence, not by sub-microsecond time |
| PER-004 | Persisted enum class names are part of the format | `test_persisted_enum_names.py` fails a rename without a schema step |
| LIM-001 to LIM-003 | The release ADRs' limitations (v3.13) | Unchanged; re-read in this audit |

Boundaries (`BDY-xxx`) and external dependencies are unchanged and listed in
`ROADMAP.md`. No vendor, credential or network service is inside the library:
the wheel declares no runtime dependency, and examples 02, 11, 16 and 70 ran
with every socket operation refused by an audit hook (section 12).

---

## 10. Hardening and scale (Phase E)

`docs/audit/scripts/stress_v4_0.py` re-runs v3.13's and v3.12's scenarios as
written and adds three for what v4.0 touched. CPU seconds by
`time.process_time`, collector running; one run on the machine in section 17;
each scenario asserts its outcome as well as printing its cost.

| Scenario | Result |
| --- | --- |
| 10,000 assets | 30,000 records at 198 us/record vs 155 at 400 assets: 1.28x per record at 25x the universe |
| 1,000 strategies | every strategy on every bar 3.8 ms/record; each on its own asset 0.5 ms/record, the same 1,665 fills |
| 100 venues | 12,000 day-order expiries 0.58 s; 10,000-order book across 100 accounts reconciled, 0 mismatches |
| Construction | 10,000 assets, 5 factors, 20,001 constraint rows: OPTIMAL in 1.09 s |
| Checkpoints | 20,000 records, 20 checkpoints: last segment 1.61 MB vs base 1.57 MB (flat) |
| Per order | 2,658 bytes held per additional order (PRF-011) |
| Lattice | American put at 5,000 steps 4.2841 in 1.84 s |
| Optimal split | 20,000 units across 20 venues 12.07 s |
| Factor covariance | stated by structure: 10,000 assets 6.0 MB traced vs 1,317 MB dense at 4,000 |
| Restore (new) | 2,000 / 8,000 / 32,000 records round-tripped with every object described: per-record cost 0.20x at 16x the run; restored == captured |
| Ingestion (new) | 10,000 / 40,000 / 160,000 rows, 1% unreadable, dropped and recorded: 7.3 / 7.8 / 8.3 us a row (1.13x at 16x); refusal no slower than ingestion |
| Strategy start-up (new) | 250 / 1,000 / 4,000 strategies through `start_strategy`: 0.01 / 0.03 / 0.21 s -- quadratic (33x at 16x), as `register_strategy` always was; 4,000 start in a fifth of a second |

Whole run: 204.7 s wall; peak RSS of the process 2.96 GB (the dense
4,000-asset covariance, a deliberate comparison). That run printed the
10,000-asset and 1,000-strategy peaks in the wrong unit ("293488 MB"); re-run
alone after the unit was corrected, the two scenarios peak at **288 MB** (the
10,000-asset book; the 1,000 strategies add nothing above it), the per-record
growth again 1.29x. That re-run shared the machine with the mutation harness,
so only its memory figures are quoted. No avoidable algorithmic problem was found on the canonical path:
restore, ingestion and checkpoints are linear or better; start-up is quadratic
in a count that is set once.


**One-asset path cost (PRF-006's budget).** The v3.13 method: five interleaved
rounds of the `v3.9.0` and `v3.13.0` tags and the 4.0 candidate on one machine,
each tree running its own benchmark against its own package (`PYTHONPATH`
verified per tree), each ratio taken within a round; medians, ranges in
brackets.

| Benchmark | 3.9.0 | 3.13.0 | 4.0 | 4.0 / 3.9.0 | 4.0 / 3.13.0 |
| --- | ---: | ---: | ---: | --- | --- |
| OMS, 100k order lifecycles | 6.51 s | 4.51 s | 4.55 s | 0.70x (0.69-0.71) | 1.00x (0.99-1.02) |
| Backtest, 4k records | 1.26 s | 1.38 s | 1.38 s | 1.10x (1.09-1.13) | 1.00x (0.98-1.03) |
| Replay, 4k records | 1.43 s | 1.54 s | 1.52 s | 1.07x (1.06-1.08) | 1.00x (0.99-1.01) |
| Execution pipeline, 4k events | 1.22 s | 1.36 s | 1.35 s | 1.10x (1.00-1.14) | 1.00x (0.97-1.04) |
| Portfolio engine, fills per second | 46.3k | 34.6k | 34.9k | 1.33x slower (1.32-1.35) | 0.99x (0.96-1.02) |

The budget -- the OMS within 1.1x of 3.9, the one-asset paths within 1.25x, the
portfolio micro-benchmark within 2.0x -- holds in every round, and v4.0 adds
nothing measurable to 3.13: every median lies within 0.99x-1.00x and every
range spans 1.0. (This machine is faster than the one v3.13's table was made
on; ratios, not seconds, are comparable.)

---

## 11. Public API, schemas and migration (Phase F)

Against `docs/api/history/3.13.0.json` (packages only, as v3.13 recorded):
**four names added, none removed or rebound**, all in `alphalab.strategy`:
`start_strategy`, `context_factory`, `FixedClock`, `DiscardingLogger`
(additive). Enum names and members unchanged (`test_persisted_enum_names.py`).

**The documented modules (API-007).** The manifest now also records
`alphalab.api` and the ten snapshot modules: 44 packages and 11 modules, 2,617
exports, 35 shared names. Their 134 names were compared with the same modules
at the `v3.13.0` tag, by v3.13.0's own generator: none removed or added; two
changed, both by PER-008's schema step -- `RunSnapshot` gained
`fill_policy_description: str | None = None` before `schema_version` (default 4
-> 5), and `PipelineSnapshot`'s `schema_version` default moved 7 -> 8. A caller
gets these from `capture` and `from_primitives`; one that built them
positionally should pass `schema_version` by keyword (CHANGELOG).
`docs/api/history/4.0.0.json` is kept for v4.1's comparison.

| Change | Class | Migration |
| --- | --- | --- |
| Four names in `alphalab.strategy` | additive | none |
| Restore refuses an object configured otherwise | compatible correction (a wrong run was accepted) | supply the objects the run was captured with |
| Refusing ingestion refuses unreadable rows; dropping records them | compatible correction | fix the source or choose a dropping policy; re-derive pinned versions of datasets that had unreadable rows |
| Non-finite option inputs refused | compatible correction (the answer was `NaN`) | validate inputs |
| Whole-unit partial fills floored | compatible correction | none |
| Pipeline 7 -> 8, run 4 -> 5 | schema step reading every older payload (fixtures of v3.9.0, v3.11.0, v3.12.0 and v3.13.0) | none |
| `RunSnapshot.fill_policy_description`; snapshot `schema_version` defaults | compatible (optional field, defaults) | pass `schema_version` by keyword if building a snapshot by position |
| `alphalab.api` and the snapshot modules recorded in the manifest | scope of the freeze, no code change | none |
| `configuration_id` and `result_id` of every run | identity change, by design | re-record pinned identities |

Every row is in the CHANGELOG's "Migrating from 3.13" table. The stability
policy from v4.0 is in `docs/api/PUBLIC_API.md`.

---

## 12. Release gates (Phase G)

Local, in a clean mirror of the candidate, with an environment holding the
`[dev]` extras (`pip install -e ".[dev]"`, as `ci.yml`). Hosted CI: **pending**.

| Gate (as `ci.yml` / `benchmarks.yml` run it) | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | All checks passed |
| Format | `ruff format --check .` | 1,263 files already formatted |
| Types | `mypy .` (strict, repository-wide) | no issues in 1,170 source files |
| Tests | `pytest -W error` | 9,325 passed (4,858 unit, 658 integration, 3,809 regression), 0 failed, 0 skipped, 0 warnings, on the final source (sections 18, 19) |
| Examples | every `examples/[0-9]*.py` with `-W error`, `PYTHONHASHSEED` 0 and 4242 | 70 of 70 exit 0 under each seed, nothing on stderr; 66 byte-identical across the seeds, the other four (`12`, `13`, `45`, `48`) printing a random run or order id, a process id or CPU time, as in v3.13 |
| Examples against v3.13.0 | the tag's 69 examples, seed 0, diffed with the candidate's | 60 byte-identical; `15` (DAT-010: its drops are now transformations, so its dataset version moves), `46` (engine version), `55` (identities, PER-008), `47` and `68` (rewritten for PER-008), and the four nondeterministic ones |
| Benchmarks | every `benchmarks/benchmark_*.py` with `-W error`, 900 s limit each | 53 of 53 exit 0, nothing on stderr; 279.8 s in all on the final source |
| Stress | `stress_v4_0.py` (all scenarios) | passed; section 10. Re-run on the final source: every scenario passed, 203.3 s; peak RSS 281 MB at 10,000 assets |
| Mutation | `mutation_v4_0.py <scratch> --worktree` | section 13 |
| Inventory | `historical_inventory_v4.py --check` | current |
| Mutation table | `mutation_v4_0.py --check .` | 201 mutations, every one applies exactly once |
| Certificate | `certify_release.py --check` | 11 of 11, matching the committed certificate; refuses tampered source and evidence (section 14) |
| Build, `twine check --strict`, clean installs | `python -m build --outdir dist`; each artifact alone in a fresh venv, `pip check`, `tests/installed_smoke.py 4.0.0` and the manifest checked against the installed package, from outside the checkout | **recorded beside the artifacts**, in `dist/SHA256SUMS-4.0.0` and `dist/VERIFICATION-4.0.0.txt`: this document ships inside the sdist, so it cannot carry that sdist's hash or the result of installing it. Every earlier build of the candidate passed the same checks |
| Metadata | the wheel's `METADATA` | `Version: 4.0.0`, `Requires-Python: >=3.12`, no runtime `Requires-Dist` (only the `dev` extra) |
| No network | examples 02, 11, 16, 70 with an audit hook refusing every socket operation | all ran; the hook's self-test refused a connection |
| Whitespace | `git diff --check`; untracked files checked by script | clean; the two v3.13.0 run-store fixtures end without a newline, as the store writes them and as v3.12.0's do |
| Tags | local tags against `git ls-remote --tags origin` | 75 and 75, every object identical; none created, moved or deleted |
| Hosted CI | GitHub Actions | **not run**: the candidate is uncommitted and unpushed |

Every result above was captured from the command's own output in this session;
the counts are as printed, not rounded.

---

## 13. Mutation testing

`docs/audit/scripts/mutation_v4_0.py <scratch> --worktree --workers 4`: v3.12's
method through v3.13's table -- one mutation at a time on a copy of the
candidate (tracked and new files, `git ls-files -co --exclude-standard`), the
whole suite with `-x`, the tests that read a clock and the certificate's
source-digest test deselected, an unmutated baseline first, and a check that
each copy imports its own `alphalab`.

**Run** (the candidate before the test below): every pattern applied exactly
once. Baseline passed -- 9,125 passed, 89 deselected (121.5 s). 199 mutations,
6,483 s wall on four copies, 25,320 mutation-seconds.

| Series | Mutations | Caught |
| --- | ---: | ---: |
| M, V (v3.10) | 42 | 42 |
| W (v3.11) | 37 | 37 |
| X (v3.12) | 47 | 46 -- X17 survives, equivalent |
| Y (v3.13) | 58 | 58 |
| Z (v4.0) | 15 | 14 -- Z13 survived |
| **All** | **199** | **197** |

**Equivalent mutant.** X17 removes the clause that a share change applies only
once knowable; `ShareCountChange` refuses a change known after it takes
effect, so the clause is implied by the change's own validation and no input
distinguishes the mutant (master audit, W.4). It survives as it has since v3.12.

**Genuine miss, now pinned.** Z13 deletes the inventory's report of an item that
no row -- or more than one row -- classifies. Every real item is classified
exactly once, so no test ever exercised the report. A test now gives the
inventory an unclassified item and a doubly classified one and requires each to
be reported (`test_an_item_classified_by_no_row_or_by_two_is_reported`,
TST-017). Re-run on its own against the candidate with that test: baseline 9,126
passed, 89 deselected; **Z13 caught** by the new test.

**After the final certification pass** (section 18), which changed
`alphalab/runtime/assumptions.py`, `alphalab/strategy/runtime.py`, the inventory
script, `certify_release.py` and `generate_public_api.py`, every mutation on
those files -- X30, Y57, Y58, Z01-Z03, Z09, Z10, Z12-Z14 -- and two new ones for
PER-009 (Z16, a mapping described by its type; Z17, a set) were run again on
the final candidate: baseline 9,137 passed, 89 deselected; **13 of 13 caught**,
each by a failing test. The other 188 target files unchanged since the full
run, against a suite that has since only grown. Final count: **200 of 201
caught, one equivalent (X17).**

**Every catch is a failing test.** 196 of the 197 catches name a failing test
directly. Three -- M02, W36, X45 -- were first reported as a collection error
in `tests/regression/test_ambient_decimal_context.py`, which builds a live and a
backtest reference at import time and asserts them there. Re-run with that
module ignored, each fails an ordinary test:
`test_integrated_runtime.py::test_a_deployment_decision_reaches_a_fill_at_a_venue`
(M02), `test_artifact_lifecycle.py::test_a_run_result_becomes_an_artifact_and_comes_back_intact`
(W36) and `test_backtest_pipeline.py::test_a_backtest_walks_the_whole_path` (X45).
No mutation was counted caught on a crash, a timeout or an unrelated error.

v4.0's fifteen: Z01-Z03 and Z12 (PER-008: a restore accepting another
configuration, a pipeline or run snapshot not describing its simulator or fill
policy, the v7 -> v8 upgrade inventing a configuration), Z04-Z06 (DAT-010: a
missing value or an unreadable row dropped under a refusing policy, a drop left
out of the provenance), Z07 (NUM-015), Z08 (EXE-011), Z09-Z11 (DOC-009:
`start_strategy` replacing a strategy or drawing from the caller's stream, a
context's configuration writable), Z13-Z14 (TST-017: the inventory), Z15
(API-007: a name leaving `alphalab.api`'s `__all__`, caught by the manifest
test), and -- added in the final pass -- Z16-Z17 (PER-009: a mapping or a set
described by its type name).

---

## 14. Certificate

| | |
| --- | --- |
| Verdict | PASSED, 11 of 11 |
| Engine | alphalab 4.0.0 |
| Source digest | `4cd92508fec0ff498a61a9dfd6e5741116722d75fc1d137a53c526dd315b479d` (re-certified after the final pass; `e9686252...6b77` before it, with every check's evidence identical) |
| Time-zone database | 2024a |
| Certified on | Python 3.12.4 (CPython), macOS-26.6.2-arm64-arm-64bit |

Evidence that moved from 3.13.0, and why: DET-1, DET-2 and REP-1's `result_id`
and REP-2's digest (the recorded configuration now carries each object's
description: 55,309 -> 55,660 record bytes); REP-3 reads 11 more payloads
(v3.13.0's); API-1's manifest (44 packages, 2,479 exports -> 44 packages and 11
documented modules, 2,617 exports: four names added, 134 recorded for the first
time, API-007). NUM-1 to NUM-3 and the
parity checks did not move. `--check` was shown to refuse a candidate with one
comment appended to engine source (it names both digests) and a certificate
whose DET-1 evidence was altered by one byte.

---

## 15. Final audit (Phase H)

Done after the gates, as a separate reading: the audit, the certificate, the
CHANGELOG, ROADMAP, README, ADR-0049 and the ledger were read against the exact
candidate tree that the gates in section 12 ran on.

| Question | Answer, and the evidence |
| --- | --- |
| Has the archaeology covered v2.0.0 to v3.13.0? | Yes, for the sections that state what was left out or left open: every ADR from 0010 to 0035 with a non-goal, not-in-scope or deferral section, every CHANGELOG section from 2.0.0 to 3.13.0 of the six kinds that defer, `nowandfuture.md` and the strategy-runtime design's open questions -- 257 items, each classified with evidence; ROADMAP and ADR-0042 to ADR-0049 by the earlier guard. A guard re-reads the sources (section 4). Not read item by item: release-note prose outside those sections, which states what was done rather than what was left. |
| What defects were found and fixed, with which tests? | PER-008, DAT-010, NUM-015, EXE-011 (section 5), API-007 and the v3.13.0 fixtures gap (section 6); each test named there, each defect test failing on the v3.13.0 export. |
| Which historical limitations remain, and why is each safe? | Section 9: FUT-001 with its interim behaviour; LIM-004 to LIM-006, PRF-011/012, DAT-007/008, PER-004, LIM-001 to LIM-003, each with its reason and test. |
| Did the strategy-building exercise succeed? | Yes: example 70 and its end-to-end test; every figure equal to the hand computation, the continuation byte-identical, five refusals (section 8); run again from the clean wheel and sdist installs. |
| Did financial calculations, execution semantics, persistence, public APIs or schemas change? | Financial arithmetic: no -- NUM certificate evidence unchanged, options unchanged for finite inputs. Execution: one correction, whole-unit partial fills floored (EXE-011). Persistence: pipeline 8, run 5, every older payload read. Public API: four names added; 134 existing names brought under the freeze. Identities: every `configuration_id` and `result_id` moves, by design. |
| Are the migration statements complete and accurate? | The CHANGELOG's table has one row per behaviour change above, checked against the code and the API diff, including the two snapshot classes' changed defaults. |
| Do the public examples run against the current interface? | All 70 under `-W error`, under two hash seeds (section 12); 11 and 70 also from each installed distribution and with the network refused. |
| Are code, tests, README, architecture, roadmap and changelog consistent? | Version markers are held by `test_version_markers_agree.py`; the dependency table by `test_documented_dependencies_are_measured.py`; README's code by `test_readme_shows_runnable_code.py`; ROADMAP's future work by `test_accepted_future_work_is_stated_and_on_the_roadmap`; the API sentence and module list by `test_public_api_manifest.py`. The prose was re-read (section 11 of the CHANGELOG lists what was corrected). |
| Does each reported gate have captured evidence? | Yes: each row of section 12 is the summary line of a captured output, kept with the run. Hosted CI is not reported, because it has not run. |
| Does the certificate's source digest match the final engine source? | Yes: `4cd92508...479d` is the digest of the final candidate's `alphalab/`, and `--check` passes on the candidate. |
| Does verification reject mismatched evidence? | Yes: `--check` exits 1 on one comment appended to engine source, naming both digests, and on one byte of DET-1 evidence altered (section 14). |
| Is the recorded environment accurate? | CPython 3.12.4 on macOS 26.6.2 arm64, time-zone database 2024a -- as the certificate records and section 17 lists. |
| Did the built distributions pass clean installation and smoke tests? | Recorded in `dist/VERIFICATION-4.0.0.txt` beside the artifacts (section 12 says why not here): wheel and sdist each installed alone into a fresh environment, `pip check`, the version, the import location, every public name and examples 11 and 70 checked from outside the checkout. |
| What material risks remain? | Section 16. |

Not a claim of this audit: that the candidate has no defect. It states what was
examined (section 7) and what was not.

---

## 16. Remaining risks and recommended follow-up

1. **Hosted CI has not run** on the candidate (Linux runner). Push, and read the
   `Quality Gates`, `CodeQL` and weekly `Benchmarks` results before tagging.
2. **Cross-host identity** (LIM-005): a Linux runner certifies the same
   evidence only if its `libm` rounds as this host's does; `--check` would name
   any moved figure. The v3.13 certificate passed hosted CI on Linux.
3. **The stale gitignored directories** in this checkout (section 2) make a
   local `pytest` fail; delete them before running gates in the checkout.
4. **FUT-001** for v4.1, with its acceptance criteria.
5. **The operating envelope** (LIM-006) is a measurement on one machine.

---

## 17. Environment

| | |
| --- | --- |
| Host | Apple M2, 8 cores, 16 GB; macOS 26.6.2 (25G83) |
| Python | CPython 3.12.4 |
| Time-zone database | 2024a (as the certificate records) |
| Tools | the `[dev]` extras resolved by pip into a fresh virtual environment |

---

## 18. Final certification and distribution pass

A second, separate pass over the candidate before its distributions were built,
taking nothing above as given: the workflows re-read as the source of truth,
the engine diff re-read, the archives unpacked and tested, the performance
budget measured, every gate re-run.

**What CI runs** (`.github/workflows`). `ci.yml`, on a push or pull request to
`main`: `ubuntu-latest`, Python `3.12` (setup-python v7), `pip install -e
".[dev]"`, then `ruff check .`, `ruff format --check .`, `mypy .`, `pytest -W
error`, every `examples/[0-9]*.py` with `-W error`, `certify_release.py
--check`, `python -m build`, `twine check dist/*`, and each distribution
installed alone into a fresh venv and run with `tests/installed_smoke.py` from
outside the checkout. No environment variables are set. `codeql.yml`: CodeQL's
Python analysis on push, pull request and weekly. `benchmarks.yml`: every
benchmark with `-W error` and a 900 s limit, weekly and on demand.
`release.yml`: on a published release, build and `twine check`. The `[dev]`
pins resolve today to ruff 0.16.10, mypy 2.4.0 and pytest 9.1.1 -- the newest
within their bounds, and what this verification used.

**Hosted CI's history** (read-only, the public API). CI passed on `a04a3d1`,
this candidate's base, with a certificate generated on macOS arm64; the v3.13
certificate had first been generated on Linux x86-64 (glibc 2.39, Python
3.12.3, tz database 2025b), and its refresh on macOS changed only the build and
host lines: every certified figure was identical on the two platforms. Of the
two CI failures before that merge, one was the format check and one the pytest
step, fixed by the next commit, which changed only the certificate's source
digest. The runner notes that `ubuntu-latest` moves to Ubuntu 26 from
2026-10-19: a run on or after that date uses a new image, and so a new glibc
and time-zone database, which no earlier run of this code has met.

**Found and fixed in this pass**

| ID | Finding | Regression protection |
| --- | --- | --- |
| PER-009 (medium) | `describe()` wrote a mapping or a set by its type name, so a restore accepted `VolatilityTargetSizing` with other per-asset volatilities, or a `ProportionalTax` on the other side, and two such runs shared a `configuration_id` -- PER-008's fix did nothing for them | `tests/regression/test_describe_states_every_parameter.py` (5 of 6 fail before the fix); mutations Z16, Z17 |
| PKG-001 (low) | The sdist shipped `tests/` without the files they read; its own suite failed 15 tests and could not collect one | `tests/regression/test_sdist_carries_what_the_tests_read.py` (fails against HEAD's include list); the rebuilt sdist's suite run from the unpacked archive |
| Inventory | 258 rows against 257 everywhere else: a bullet v4.0 had added to `nowandfuture.md`'s open questions was read as history | now prose beside the list; `test_every_count_of_the_inventory_agrees` holds the rows, the status counts and seven documents to 257 |
| DOC-009 | `start_strategy(subscriptions="bars")` was refused as "Subscription 'a' names no topic" | refused for what it is; a unit test |
| Docs | ROADMAP cited a v4.0 one-asset-path measurement this audit did not contain | measured (section 10) |
| Tooling | a failing `certify_release.py --check` named the moved check, not the value or the hosts | it prints each moved value and, when they differ, both hosts; a test |
| LIM-007 (low) | `requires-python >=3.12` admits interpreters nothing has tested | stated in `docs/INTEGRATION.md` and ROADMAP; target v4.1 with criteria |

**For iluvtrade.** `docs/INTEGRATION.md` states the dependency contract: the
release wheel by a pinned direct reference -- AlphaLab is not on PyPI, and a
bare `alphalab==4.0.0` against a public index would install whatever owns the
name -- CPython 3.12, no runtime dependency, the public API as the manifest
records it, and what the certificate establishes about determinism and
persistence.

**Ignored files in the checkout.** Every one of the 120 entries Git ignores is
generated: 117 caches (among them the `__pycache__`-only directories of
packages removed in v3.10 to v3.12), the developer's `.venv/`, `dist/` (holding
3.9.0's artifacts beside 4.0.0's) and a `.DS_Store`. No source is hidden by an
ignore rule. Nothing was deleted.

**Gates on the final source** (the clean mirror of the candidate after this
pass; section 12 lists the commands): `ruff check .` and `ruff format --check .`
clean (1,260 files); `mypy .` no issues in 1,167 files; `pytest -W error`
**9,226 passed**, none skipped, no warnings; every example **70 of 70** under
`PYTHONHASHSEED` 0 and 4242, 66 byte-identical across them and the other four
as in v3.13; every benchmark **53 of 53**; the stress program, every scenario;
the mutations on every changed file and the two new ones, **13 of 13** caught
(section 13); `historical_inventory_v4.py --check` current; `mutation_v4_0.py
--check` 201 apply once; the certificate re-generated (only the source digest
moved) and `--check` passing. The built distributions' record is in `dist/`
(section 12).

**Not run, and why.** Hosted CI (GitHub Actions on `ubuntu-latest`): the
candidate is uncommitted, and only a push runs it -- it is the remaining gate,
and the first evidence on Linux for this code. CodeQL: a hosted service. Any
interpreter other than CPython 3.12.4: none is installed here, and installing
one was not within this pass (LIM-007).

---

## 19. Pre-publication release validation and the version audit

**The gap (TST-018).** The workflows were read as the source of truth.
`release.yml` -- the only release-specific one -- runs on `release: published`,
after the release exists, and ran a build and a non-strict `twine check`.
`ci.yml` and `codeql.yml` run on a push or pull request into `main` (CodeQL also
weekly); `benchmarks.yml` weekly and on demand. Nothing checked the artifacts'
names, their hashes or the version against the intended release, before
publication or after, and nothing kept what CI built.

**What was added.** `.github/workflows/preflight.yml` runs every release gate on
one exact commit before publication: the release identity
(`release_preflight.py version`), CI's gates (`ci.yml`, given a `workflow_call`
with a `ref`), the benchmarks (`benchmarks.yml`, likewise), a CodeQL analysis
that uploads nothing and fails on an error-level or high-severity result, and a
fresh build checked by `release_preflight.py distributions` and kept as the run's
artifact for 30 days; a verdict job fails unless every gate succeeded.

*Which commit.* GitHub runs a `workflow_dispatch` workflow only from a file on the
default branch, a `pull_request` run checks out the merge commit, and a `push`
run's `GITHUB_SHA` is the pushed tip -- except on a branch deletion, when it
"reverts to the default branch". So: on a push to the candidate branch
`work/v4.0.0-qa-hardening` (the only branch the trigger names), the run
validates the pushed commit, and the guard refuses a deletion, any other ref, a
pushed SHA that is not a commit, and a run whose commit is not the pushed tip;
on demand, the run validates the ref chosen under "Use workflow from", and the
required `candidate` input must repeat that name -- a run left on `main` stops
in its first step having validated nothing; on a pull request into `main`
labelled `release-preflight`, it validates `github.event.pull_request.head.sha`,
the candidate branch exactly. Any other event is refused. The guard's own shell
script is extracted from the workflow and run by the suite under nine events'
variables, each refusal held to its reason; of eight mutants of the guard and
its trigger, all are caught, and a ninth (validating the run's SHA rather than
the pushed one, after requiring them equal) is equivalent. The CI and benchmark workflows keep their own
triggers and check out the event's commit when not given one.

*Three corrections before the hosted run.* The reused workflows already
checked out the given `ref`, but only a substring test held them; the tests now
read every checkout step, so a second checkout, a checkout of the event's SHA,
or a preflight job that stops passing the SHA fails (each of these, mutated in,
fails a test). A published tag was compared by stripping one leading `v`, which
also accepted a tag without it; `release_preflight.py version --tag` now
requires exactly `v<MAJOR>.<MINOR>.<PATCH>` (refusing `4.0.0`, `V4.0.0`,
`v4.0.0-rc1`, `vv4.0.0`, `refs/tags/v4.0.0`), and `release.yml` takes the
version only from a tag it has checked. And a push or labelled pull-request
preflight checked only that the version references agreed with one another, so a
tree saying 4.0.1 throughout would have passed; the check now always takes the
release it must be -- `RELEASE_VERSION`, 4.0.0, for those routes and the typed
`expected_version` for a manual one -- and the identity step's own script, run
by the suite in a tree that says 4.0.1 everywhere, fails. Eleven mutants of
these three fixes are each caught.

*What it may not do.* Top-level permission `contents: read`; the CodeQL job alone
adds `security-events: write` for CodeQL's status, with `upload: never`. No tag,
release, commit, comment or package upload; typed inputs reach shell only
through environment variables. `release.yml` keeps running after publication,
now with `--strict`, a check that the tag names the package's version, and the
same installed-artifact checks.

*Shared, not repeated.* `tests/installed_smoke.py` -- which CI, the preflight and
`release.yml` all run on each installed artifact -- now also runs `pip check`,
refuses a runtime requirement, holds every public name to the manifest, and runs
the examples with the network refused.

*Verified here, not on GitHub.* actionlint 1.7.12 with shellcheck 0.11.0 reports
nothing on any workflow (after one shellcheck style finding in the verdict step
was fixed); the commit guard and the verdict were executed with the variables
GitHub would set -- a dispatch left on `main` exits 1 having validated nothing,
a dispatch on the candidate takes its commit, a labelled pull request takes the
head and not the merge commit, and the verdict fails naming any gate not
`success`; five mutants of the workflow (the guard removed, `--strict` dropped,
`contents: write`, an input in shell, the merge commit validated) are each
caught by `tests/regression/test_release_preflight.py`, whose own tests of the
script found a crash on a SARIF result without locations, fixed. **The workflow
has not run on GitHub**: that run is the remaining gate.

**The version audit (DOC-010).** Every tracked and new text file was searched for
version strings and each match read in context.

| File | Was | Now | Remaining references |
| --- | --- | --- | --- |
| `SECURITY.md` | supported: `3.x ✅`, no 4.x row | `4.x ✅`, `3.x ❌`, `< 3.0.0 ❌` | none |
| `SECURITY.md` | toolchain "not installed by `pip install alphalab`" | "not installed with the library itself -- an install without the `[dev]` extra" | none |
| `docs/INTEGRATION.md` | release URL with no word on publication | "resolves once the `v4.0.0` release has been published"; hash from its `SHA256SUMS-4.0.0` | v3.9.0-v3.13.0 named as payloads 4.0.0 reads: intentional |
| `benchmarks/benchmark_{adaptive_research,execution_contract,market_data,point_in_time_research,portfolio_risk,strategy_certification,strategy_execution}.py` | banner "AlphaLab v3.5" ... "v3.9", "v2.3" | "(added in v3.x)" | none |
| `README.md` | header badge 4.0.0, status 4.0.0, footer without a version | unchanged; checked | milestones v1.0.0-v3.0.0: intentional history |
| `examples/46`-`49`, `54`, `55`, `60`, `68`, `_adaptive_evidence.py` | `EngineIdentity("alphalab", "3.6.0")`, `code_identity_for(..., "3.13.0")` | unchanged | intentional: pinned illustrative identities whose fingerprints the examples print |
| `configs/development.toml` | `version = "3.12"` | unchanged | intentional: the Python version |
| `.pre-commit-config.yaml` | `rev: v0.16.9`, `v2.3.1` | unchanged | intentional: ruff's and mypy's hook versions |
| `CONTRIBUTING.md`, `docs/VISION.md`, `docs/SYSTEM_DESIGN.md`, `docs/ARCHITECTURE.md`, `docs/STATE_MODEL.md`, `docs/api/PUBLIC_API.md`, `ROADMAP.md`, `nowandfuture.md`, `docs/README.md`, `examples/README.md` | "as of v3.0.0 ...", release history, payload versions | unchanged | intentional: dated history and compatibility statements |
| `CHANGELOG.md`, `docs/ADR/`, `docs/audit/`, `docs/work/`, `docs/api/history/`, `tests/fixtures/`, engine docstrings citing a measurement's release | historical versions | unchanged | intentional history; outside the new test by design |

`tests/regression/test_release_facing_versions.py` derives the version from
`alphalab/common/_version.py` and holds to it, naming file and line: every
release URL, tag reference, artifact name and pin in eighteen release-facing
documents; every "current/latest release" claim; the header and footer of
`README.md`, `docs/README.md` and `docs/INTEGRATION.md`; the security table's one
supported line; and every printed "AlphaLab vX" banner. Each rule fails against
the text it was written for. The README's "Current Release: v4.0.0" stays: the
README ships inside the artifacts and must read correctly once they are
published; the candidate's unpublished state is recorded in this audit and in
`dist/VERIFICATION-4.0.0.txt`, which do not claim a publication.

**Gates after this pass** (the final source, in a clean mirror): `ruff check .`
and `ruff format --check .` clean (1,263 files); `mypy .` no issues in 1,170
files; `pytest -W error` **9,325 passed**, none skipped, no warnings; every
example **70 of 70** under `PYTHONHASHSEED` 0 and 4242 (66 byte-identical, the
other four as before); every benchmark **53 of 53**; `certify_release.py
--check` 11 of 11 (no engine source changed in this pass); `historical_inventory_v4.py
--check` current; `mutation_v4_0.py --check` 201 apply once;
`release_preflight.py version --expected 4.0.0` agrees everywhere; actionlint
clean. No engine source changed, so the stress program, the mutation run and
the certificate stand as section 18 records them. The rebuilt distributions'
record is `dist/VERIFICATION-4.0.0.txt`, written by the same
`release_preflight.py distributions` the hosted preflight runs.
