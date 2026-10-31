# AlphaLab v4.1.1 Future-Readiness Report

**Candidate:** branch `work/v4.1.1-future-readiness`, created from `main` at
`33e61d3edbd041bcd125d5dea913c00cdc9da613` -- the commit the `v4.1.0` tag names.
Nothing was committed, pushed, tagged or published; the tag and its artifacts
were not touched; `alphalab/common/_version.py` still says `4.1.0`.
**Companions:**
[`STRATEGY_CAPABILITY_MATRIX.md`](STRATEGY_CAPABILITY_MATRIX.md),
[`STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md),
[ADR-0051](../ADR/0051-v4-1-1-information-on-every-driver-and-the-strategy-capability-audit.md),
[`../COMPOSING_STRATEGIES.md`](../COMPOSING_STRATEGIES.md), and the ledger entries
KD-005, DOC-012, TST-023, OFE-027, OFE-028 and FUT-003 to FUT-013.

The work ran in four milestones on one branch. **The audit** read the engine,
fixed two defects, connected information to every driver and several datasets
to one run, built the acceptance scenario (example 72) and named twelve gaps
(FUT-003 to FUT-012). **The continuation** implemented eight of them (FUT-003 to
FUT-008, FUT-010, FUT-012) and named one more (FUT-013). **The FUT-009
milestone** put execution algorithms on the historical drivers (FUT-009).
**The FUT-013 milestone** made a derivative's lifecycle -- exercise, assignment,
expiry, delivery -- the contract's declared terms, carried out by the run in any
market a calendar describes, and named the gaps it found (FUT-014 to FUT-017).
**The FUT-011 milestone** made a resting limit order's passive fills depend on a
declared queue model, and **the FUT-002 milestone** let a strategy declare its
configuration's type and read it back as that type. Accepted as future work:
FUT-014 to FUT-017. Every figure below was read from a command's own
output in the session that produced it.

---

## 1. Verdict

**The strategy contract is open, and the engine composes heterogeneous research
inputs, in several currencies and asset classes, without leaking time, on every
driver.** A researcher can write a strategy no template anticipated; trade an
equity and a future from two datasets on prices, social sentiment and macro
releases with revisions; put rates, funding, dividends, coupons, perpetual
funding, splits, timers and contract settlements on one schedule; hold a
cash-settled future or option to expiry and a fixed-rate bond to maturity;
train a model on data before a boundary and trade it after; have an execution
algorithm work each order in children over the historical bars, every child
filled by the run's own liquidity cap and cost model and settled on one parent
order; declare an option's or a future's terms -- American or European, cash or
physical, its deliverable, its exercise rule -- and have the run exercise,
assign and deliver it through the portfolio's own accounting, on instants a
declared market calendar gives; govern resting limit orders by a declared
queue model, so a passive order fills only once what was displayed ahead of it
has traded or left; and reproduce, replay, pause durably, store and
continue every such run to the identical result. The acceptance scenario proves the time semantics against a fixture
built to leak, including a control that does leak and is caught.

It is **not** a claim that every strategy family is production-ready, nor that
any market's rules are supported: AlphaLab ships none, and supports a market when
its calendar and contracts are declared (matrix 2.27). Engine gaps remain, each
on the ROADMAP with acceptance criteria: a
strategy-facing exercise instruction (FUT-014), settlement-date accounting
(FUT-015), contract terms beyond one deliverable and two exercise styles
(FUT-016), and margin and forced liquidation (FUT-017); a backtest also
simulates one venue, so routing between venues is evaluated only on the live
and paper path, and the queue model reads the displayed top of the book, not
depth beyond it, and draws no fill probability. Real venues and
data are, as before, the host's. "Future-ready" is therefore claimed only for
what sections 6 and 7 evidence.

**The release number is the maintainer's decision.** The candidate adds public
API, three persisted enum members and two persisted schema steps with upgrades, so it is a minor release
(v4.2.0) by semantic versioning and by `docs/api/PUBLIC_API.md`'s policy
(section 9). Until the version is set, three guards that read the package
version fail by construction (section 8.2); everything else passes.

---

## 2. Starting state

**At the audit (phase zero).**

| | |
| --- | --- |
| Repository | `/Users/varunkumarsingh/developer/AlphaLab` (root = working directory), branch `main` |
| HEAD | `33e61d3edbd041bcd125d5dea913c00cdc9da613` = `v4.1.0` (annotated tag; `git rev-parse v4.1.0^{commit}`) = baseline named in the brief |
| Working tree | `git status --short --untracked-files=all`: empty; no stash; no staged or unstaged work |
| Stale namespace directories | none (no `alphalab/` directory without Python files) |
| Version metadata | `alphalab/common/_version.py` = `4.1.0` (the single source, read by hatch and at import) |
| Dev venv | CPython 3.12.4; installed metadata reports `4.0.0` -- a stale editable install, so `tests/unit/test_package_metadata.py` fails there; a fresh venv reports `4.1.0` and passes |
| CI | `ci.yml`: CPython 3.12/3.13/3.14 x Ubuntu/macOS -- ruff, format, `mypy .`, `pytest -W error`, every example under `-W error`, certification `--check`, build, `twine check --strict`, release-path verification; plus `benchmarks.yml`, `preflight.yml`, `release.yml`, `codeql.yml` |
| Contracts | `docs/api/public_api.json` (2,628 exports at v4.1.0), `docs/INTEGRATION.md`, `docs/api/PUBLIC_API.md` (stability policy), `docs/audit/release_certification.json` |
| `dist/` | holds v3.9.0 and v4.0.0 artifacts from earlier sessions; left as it was |

**Baseline gates** (before any change): `pytest -W error` **9,671 passed, 1
failed** in the dev venv (the stale metadata above) and **9,672 passed** in a
fresh CPython 3.12 venv built from a clean mirror of the tree; ruff, format and
`mypy .` clean; `certify_release.py --check`: 11 checks passed, matching.

**At the continuation.** The same branch, the audit's work uncommitted and
preserved (25 files modified, 10 added); nothing reset, discarded or recreated.

---

## 3. Method

Archaeology before code. For each of the 43 packages and `alphalab.api`: the
package docstring and exports; the AST import graph (scratch script below, run
on the checkout); the drivers and the run step read in full where a capability
crosses them; and the integration tests and examples that exercise it. Claims
in documentation were checked against code; two were false (section 6). The
continuation re-read the ledger, ADR-0051 and every FUT entry against source
before implementing, and kept the ledger, the matrix, the ADR, the ROADMAP and
the tests synchronized as each item closed.

```bash
python - <<'PY'
import ast, collections, pathlib
pkg = pathlib.Path("alphalab")
names = {p.stem for p in pkg.iterdir() if (p / "__init__.py").exists() or p.suffix == ".py"}
edges = collections.defaultdict(set)
for path in pkg.rglob("*.py"):
    owner = path.relative_to(pkg).parts[0].removesuffix(".py")
    for node in ast.walk(ast.parse(path.read_text())):
        mods = [a.name for a in node.names] if isinstance(node, ast.Import) else (
            [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
        for mod in mods:
            part = mod.split(".")
            if part[0] == "alphalab" and len(part) > 1 and part[1] in names and part[1] != owner:
                edges[owner].add(part[1])
for name in sorted(names):
    users = sorted(o for o, e in edges.items() if name in e)
    print(f"{name:24} imported by {len(users):2}: {', '.join(users)}")
PY
```

---

## 4. The historical v3.1-v3.9 work, and where it is now

Each release's capability is still present, public and tested; where its
package layout changed, the ledger says why. The cross-release integration
tests for each (`tests/integration/test_v32_capabilities.py` to
`test_v39_capabilities.py`) pass on the final tree.

| Release | Delivered | Where it lives now | On which path | Finding |
| --- | --- | --- | --- | --- |
| v3.1 | Universal data engine: provenance, derived dataset versions, the application API | `alphalab.data`, `alphalab.api` | ingestion -> backtest, replay | intact; several datasets per run added (OFE-028) |
| v3.2 | Study path: features, forward returns, signal diagnostics, walk-forward, purged CV, robustness, overfitting | `alphalab.factor_library`, `alphalab.research`, `api.run_study` | research; a study's features evaluated in a strategy (FUT-006) | intact |
| v3.3 | Institutional backtesting: costs, capacity, attribution, risk decomposition, scenarios | `alphalab.execution`, `analytics`, `scenario` | backtest (costs), research | intact; `scenario` standalone by design |
| v3.4 | Global markets: conventions, calendars, futures chains and rolls, options IV and surfaces, FX research, perpetuals, bonds | `alphalab.conventions`, `futures`, `options`, `crypto`, `portfolio.fx_research`, `macro.bond` | booking on the path since v3.11 (ACC-005); expiry, settlement, funding and bonds on the path now (FUT-004, FUT-010, FUT-012) | intact; physical delivery FUT-013 |
| v3.5 | Research-to-live progression, deployment specifications, runtime health, expected/paper/live comparison, broker reconciliation | `alphalab.lifecycle` | lifecycle | intact |
| v3.6 | Fingerprints, reproducibility manifests, certification, portability | `alphalab.lifecycle` | every path | intact; a manifest now names a run's schedule and a combined dataset's parts (FUT-005) |
| v3.7 | Point-in-time events, alternative data, fundamentals, regimes, adaptive strategies | `alphalab.alt_data`, `research`, `factor_library`, `strategy.adaptive` | research; delivery on every driver (OFE-027) | intact |
| v3.8 | Construction, Black-Litterman, risk budgets, multi-strategy books, capital allocation | `alphalab.portfolio_optimizer`, `analytics`, `portfolio.multi_strategy`, `allocation.capital` | research; strategies call it | intact; `portfolio_optimizer` standalone by design |
| v3.9 | Universal execution contract: capabilities, venue lifecycle, algorithms, routing, execution quality | `alphalab.core`, `broker`, `execution` | live and paper | intact; algorithms not on the historical drivers (FUT-009) |

Seven packages present at v3.9.0 are gone at v4.1.0, each by a recorded
decision: `enterprise` (SaaS identity, the host's -- BND-002), `workbench` (UI
state -- BND-003), `studio` (SCF-001; its one canonical type moved to
`alphalab.strategy.definition`), `feed`, `live` and `optimizer` (provider
scaffolds and duplicates of the canonical models -- SCF-002, SCF-003), and
`plugins` (a placeholder -- SCF-003). v3.10-v3.13 (the pre-v4 audit), v4.0 (the
freeze) and v4.1 (replay pause and resume) are recorded in their own audits; the
v4.0 historical inventory classified every v2.0-v3.13 deferral (257 items).

---

## 5. Connected, already connected, isolated

**Already connected** (verified, unchanged): the strategy contract, allocation,
risk, OMS, simulation and costs, accounting with instrument economics, calendars,
point-in-time information on the backtest, the lifecycle governance path, live
routing through the broker boundary, reproducibility.

**Connected by this candidate:**

* information on the replay, the facade and the live driver (OFE-027);
* several datasets in one run, with one derived, checkable identity (OFE-028);
* `context.market.bar` (KD-005), without which a strategy reading bars through
  its context saw none;
* one `RunSchedule` for every timed input on both historical drivers and the
  facade (FUT-004), durable across a replay pause (FUT-003);
* contract expiry and cash settlement (FUT-012) and bonds (FUT-010) on the path;
* the schedule and a combined dataset's parts in the manifest (FUT-005);
* `alphalab.macro` series as observations (FUT-008); `scheduler` timers onto
  the historical drivers; a study's features and a fitted model on the
  execution path, evidenced (FUT-006, FUT-007);
* `alphalab.execution.algorithms` onto both historical drivers (FUT-009): an
  `ExecutionPlan` works every parent in children, each filled by the run's own
  fill policy and cost model and settled on the parent;
* `alphalab.options`' exercise and settlement economics onto the execution path
  (FUT-013): a contract's declared terms, carried out by the run's schedule
  through the portfolio, the cash payoff equal to `resolve_expiration`'s.

**Still isolated, by design or with a gap named:** `portfolio_optimizer` and
`scenario` (libraries a strategy or host calls; by design), `deep_learning` (a
network is held by a strategy as example 73 holds its model; no test trades
one), `feature_store` (consumed only by `ml`),
`distributed`/`cloud_research`/`cluster_scheduler` (process-pool research
infrastructure), `reporting`, `research_assistant`, `brokers`,
`reinforcement_learning` (its own loop over the real step). The matrix's section
1 lists every edge.

---

## 6. What was corrected and delivered

**Defects.**

| ID | Finding | Fix | Test |
| --- | --- | --- | --- |
| KD-005 (high) | `context.market.bar(asset_id)` returned `None` for every asset since v2.10: the view looked up a bare asset id in an index keyed `"<asset_id>_<interval>"` | `MarketView.bar` finds the asset's bars by key, latest first, longer interval on a tie | `tests/regression/test_strategy_context_reads_bars.py` (5); four fail against v4.1.0's lookup in a scratch copy |
| DOC-012 (low) | The RL package and module docstrings reported the `apply_fill` double count (fixed v2.0.0, D1) and fill-only marking (fixed v2.1) as live | Docstrings say what is true and keep the history | the package's own `test_environment_round_trip_realizes_correct_pnl`; a direct check that `process_quote` marks the position |

Found while implementing, and fixed with the item: a zero final price was
refused by `PortfolioEngine.apply_fill`, so an option could not expire worthless
(`final_settlement=True`, FUT-012); a run ending with only skipped records after
expiry escaped the held-past-expiry check (it now reads the last record handed,
skipped or not). Two guards failed on the finished tree and were examined, not
loosened: the v3.9 compatibility invariant reached its COMPATIBLE status only by
the luck of its seed (two draws in 600 at v4.1.0, none under three other seeds),
and `AssetType.BOND` moved the stream -- it now checks, on every draw, a
declaration built to offer what the flow needs, and passes on v4.1.0 too
(TST-023); the financial-defaults guard flagged `RunSchedule.of(funding_rates=())`,
an empty sequence of stated rates that charges nothing, now permitted by name
with that reason, as `rates` already was.

**Delivered (additive unless stated).**

| ID | What | Evidence |
| --- | --- | --- |
| OFE-027 | observations on `ReplayBacktest.run`, the facade and `LiveSession.deliver_observation` | `tests/regression/test_heterogeneous_inputs_on_every_driver.py` (82) |
| OFE-028 | `CombinedDataset`, `combine_datasets`, `combined_market_dataset` | `tests/unit/test_api_combined_datasets.py` (8) |
| FUT-004 | `alphalab.backtesting.schedule`: `RunSchedule` and `ScheduleCursor` carrying observations, FX rates, `Funding`, `ScheduledCashFlow`, per-unit `ScheduledDistribution`, `ScheduledFundingRate`, `ScheduledSplit`, timers and `ScheduledSettlement`; inputs before the records of their instant or after; duplicates refused; the rate table rebuilt from the schedule; `RunEngine.fund` | `tests/integration/test_scheduled_inputs.py` (34), among them a driver result equal to a hand loop stating every rate |
| FUT-003 | a paused replay carries its schedule; replay snapshot **schema 2** records its digest (upgrade from 1); restore refuses another schedule or none; what remains is derived from the last record taken | the payload round trip at every boundary, in the regression file above and in example 72's test |
| FUT-012 | `InstrumentRecord.last_trade_at` (instrument snapshot **schema 4**, upgrade from 3; outside the identity key); records after it skipped; requests refused before an order exists; `ContractSettlement` closes every position at the final price, cancels working orders, attributes the result; a run ending with an expired contract refused | `tests/integration/test_contract_expiry.py` (25: a future, an option exercised, assigned, worthless, written and expired, replay parity at every pause) |
| FUT-010 | `AssetType.BOND`; `alphalab.api.bond_schedule` from a `macro.Bond` | `tests/integration/test_bonds_on_the_path.py` (8), every figure against the bond's own cash flows |
| FUT-005 | `manifest_for_run(..., schedule=)` checks the schedule against the run's deliveries and records `schedule_digest`; a combined dataset's parts recorded as `dataset_parts`, checked to derive the combined identity; both listed by `external_requirements`; both in the identity only when present, so every v4.1 manifest keeps its id | `tests/integration/test_multi_asset_composition.py` (29) |
| FUT-008 | `alphalab.api.observations_from_indicator` | `tests/unit/test_api_macro_lift.py` (8, against `macro.known_as_of`) |
| FUT-006, FUT-007 | example 73: features from the study's own definitions, a linear model fitted on purged labels before a boundary, carried as declared parameters and state, traded after | `tests/integration/test_model_on_the_execution_path.py` (12) |
| FUT-009 | `alphalab.backtesting.algorithmic`: `ExecutionPlan` (per-asset `AlgorithmAssignment`s, an optional `VenueCheck`) on `BacktestEngine.run`, `ReplayBacktest.run` and the facade; `ExecutionRouting.ALGORITHMIC` holds every parent in the OMS and refuses stop, auction and immediate terms; `ExecutionPipeline.work_child` fills each child by the run's fill policy and cost model and settles it on the parent; the `AlgorithmBook` on the result, in replay schema 2, and the plan's id in the manifest | `tests/integration/test_algorithms_on_the_historical_path.py` (57), `test_execution_algorithm_example.py` (4), two manifest tests; example 74; `benchmarks/benchmark_algorithmic_execution.py` |
| FUT-013 | `alphalab.instrument.terms`: `OptionTerms` and `FutureTerms` on `InstrumentRecord.terms` (instrument schema 4, outside the identity key); `Exercise`, `Assignment`, `OptionExpiry` on the schedule and as run operations, through the portfolio's fill authority and each strategy's share; physical futures delivered at their final price; `AssetType.INDEX`; `session_close`, `settles_at` | `tests/integration/test_option_lifecycle.py` (58), `test_lifecycle_conventions.py` (9), `test_lifecycle_example.py` (4); example 75 |
| FUT-011 | `alphalab.execution.queue`: `QueueModel`, `PriceTimeQueue` (price-time priority over displayed liquidity), `QueuePosition`, `QueueTurn`, `QueueObservation`, `QueueBook`; `queue=` on `BacktestEngine.run`, `ReplayBacktest.run` and the facade; `QueuedResult` and `ExecutionAssumptions.queue_model` / `passive_fills`; the book threaded through `RunEngine.advance` and the pipeline step as a parameter (no pipeline, run or config schema moves, no `result_id` moves); `alphalab.backtesting.queueing` (`QUEUE_BOOK_SCHEMA = 1`) carried by a paused replay (schema 2) and held to its model on restore; `ReproducibilityManifest.queue_model` | `tests/integration/test_queue_model.py` (32: every rule of the model, fills against the same run without it, partial fills, depletion short of the front, bars, the fill policy within the queue, whole units, expiry, a split, a triggered stop-limit, taking orders untouched, an unknown queue, replay parity, a pause at every boundary resumed, refusals, an unchanged record); `tests/unit/lifecycle/test_reproducibility.py` (the manifest) |
| FUT-002 | `alphalab.strategy.configuration`: a strategy class declares `configuration_type`; `RuntimeSupervisor.configure` refuses a value not of that type (`StrategyConfigurationError`, naming the field) and restore reads the recorded value back as it -- `Decimal`, tuples, enums, `UUID`, typed keys, read-only mappings, `Literal`, optional fields and dataclasses through their constructors (defaults and constraints held); the payload unchanged, so no schema or identity moves; an undeclared configuration reads back as before (LIM-004) | `tests/regression/test_typed_strategy_configuration.py` (62: a nested configuration equal and of every type; every converted LIM-004 row exact when declared; payload, `result_id` and `configuration_id` unchanged; refusals at configure and at restore; a default for a field an older payload lacks; a paused replay); 13 mutants of the checks each caught |

**Guards extended, none weakened.** The LIM-004 contract suite is unchanged and passes; its docstring names the typed path; the ADR-deferral classifier lists ADR-0051's new limitation (an undeclared configuration, LIM-004). The replay snapshot's field pin names
`PausedReplay.schedule`; the schema-upgrade suites pin instrument schema 4 and
replay schema 2 with an upgrade test each, the v4.1.0 payloads frozen in
`tests/fixtures` still restoring through them; the persisted-enum pin adds
`bond` and `ALGORITHMIC`, the replay field pin `PausedReplay.algorithms`; the
schema-constant guard knows `ALGORITHM_BOOK_SCHEMA` and `QUEUE_BOOK_SCHEMA`; the
replay field pin names `PausedReplay.queue` and the frozen v4.1.0 replay payload
upgrades with `queue: null`; the result-field-order pin keeps
`settlement_refusals` where it was, with `queue` appended after it; the
ADR-deferral classifier covers ADR-0051's entries.

**Mutation evidence.** Each guard named here was shown to fail against the
defect it guards, in a scratch copy of the package imported ahead of the
checkout (`-o pythonpath= --import-mode=append`): in the audit, the replay merging observations *after* closing the slice fails the backtest-replay parity test (after the workload was strengthened so a reading's size depends on the slices seen -- the first version did not distinguish the two orders, which this check found), and v4.1.0's `MarketView.bar` fails four bar tests and four acceptance tests; in the continuation, four mutants, each caught by the test written for it -- a cursor that counts the inputs after a record's instant as applied (`test_scheduled_inputs.py::test_a_replay_paused_anywhere_through_a_payload_keeps_every_input`), a restore that ignores the schedule's digest (`test_heterogeneous_inputs_on_every_driver.py::test_a_payload_resumed_with_another_schedule_or_none_is_refused`), a settlement that leaves orders working (`test_contract_expiry.py::test_an_order_still_working_at_settlement_is_cancelled`), and a manifest that trusts a dataset's stated parts (`test_multi_asset_composition.py::test_a_dataset_stating_parts_it_was_not_derived_from_is_refused`); in the FUT-009 milestone, eight mutants, each caught by `test_algorithms_on_the_historical_path.py` -- a child allowed to work more than its parent has left, a child trading at its release instant, a child outliving its slice, a parent ended without its reservation released, a child fill reported as completing the parent, algorithmic orders filled whole, a book trusting an altered payload, and the capability check skipped. Two of the eight survived the first test file -- the release-instant rule needed two records at one instant, and filling whole showed only under `SAME_EVENT` -- and the tests that catch them were added. In the FUT-013 milestone, twelve mutants, each caught by the lifecycle tests -- a European option exercised early, a put paying the call's payoff, a delivery financed or shorted silently, the exercise threshold ignored, a physical option closed at its intrinsic value (booking it twice), the deliverable's multiplier assumed one, partial-exercise ties given to the later strategy, delivered shares left at the strike, an index made tradable, the registry dropping terms, an exercise beyond the position; the delivered-shares mutant survived the first test, which read the mark only after later prices had re-marked the shares, and the test now reads the equity point at the delivery itself. In the FUT-011 milestone, fifteen mutants, each caught by `test_queue_model.py` -- prints that leave the queue unconsumed, prints by the order's own side consuming it, a displayed size adding to the queue, a vacated level keeping it, a touch filling behind the queue, a join at the best price put at the front, the queue's cap ignored, its refusal ignored, no join at placement, the book never pruned during the run, a pause not pruned, a triggered stop-limit never joining, whole units not floored, a pause dropping the book, a restore ignoring the model; three survived the first test file -- the cap made the refusal look redundant, and two pruning layers each hid the other -- and the tests that catch them were added. In the FUT-002 milestone, thirteen mutants, each caught by `test_typed_strategy_configuration.py` -- configure unchecked, restore untyped, an int read back as a float, a Decimal's text not held exact, a missing field and an unknown field not refused, a union admitted, an `Any` value not held to JSON-native, a non-canonical key admitted, an enum name read without its class, a subclass admitted for its base, a non-finite Decimal admitted, a mapping read back mutable; four survived the first test file -- the refusals each needed a case of its own -- and the cases were added.

---

## 7. The acceptance scenario

`examples/72_compose_a_multi_asset_strategy.py`, held by
`tests/integration/test_multi_asset_composition.py` (29 tests) and run from
every built artifact by `tests/installed_smoke.py`. In the continuation its
inputs became one `RunSchedule`, its replay pauses through a JSON payload
restored with freshly built objects, its continuation uses `ScheduleCursor`, and
its manifest names the schedule and both datasets.

**Inputs.** ACME (cash equity, XNYS, bars at 16:00 New York) and IDXM4 (an index
future, XCME, multiplier 50, variation margin, whole contracts, settlements at
17:00), 44 sessions each from two vendor files -- the future missing one
session -- read as one `CombinedDataset`. 46 social-media posts about ACME with a
`collected_at` column, among them a post written on 8 April and collected on
22 April, a post with no collection time, an exact repeat and an unreadable row,
and the file out of order. US CPI year-on-year with its period, release instant
and vintage: February (released before the data), March, March's revision on
24 April, April, and May (released after the data ends).

**Strategy.** `CompositeSignal`, written against the protocol and built by a
class registry from parameters that name both information sets: five-session
momentum plus decayed sentiment, scaled to shares and capped; a short hedge in
the future while the latest-known CPI vintage is at or above 3.5, sized with the
contract's multiplier read from the instrument registry; the future left alone
when its last settlement is stale; orders to the next opening auction. Costs:
a six-role model (half-spread, slippage, commission, per-trade fee).
`FillTiming.NEXT_EVENT` and a 10% participation cap, so the result declares no
optimistic execution assumption. An order-size limit of 1,000 units refuses
twelve orders.

| Property the brief requires | What the test establishes |
| --- | --- |
| 1. All eligible inputs consumed | 87 records (44 equity bars, 43 futures settlements) and 49 of 50 deliveries -- May's CPI is after the data -- each seen by the strategy in knowledge order; both instruments traded |
| 2. Late information only when available | every delivery seen at exactly its knowledge instant and never after a decision; the late post absent from every decision before 22 April 09:00 and present in every one after; every decision used the CPI vintage published by then, recomputed from the release rows alone; with the late post and the revision rewritten, every order and decision before availability is identical and the first difference is after it; **control**: the same schedule stamped at observation time moves decisions two weeks early, and the same check catches it |
| 3. Out-of-order and missing data explicit | out-of-order file delivered in knowledge order; repeat and unreadable rows rejected by line; the undated post counted (`unverified == 1`) and never delivered; the missing settlement leaves the future untouched that day; two datasets pricing one instrument at one instant refused |
| 4. Instrument and currency semantics | whole contracts; multiplier 50 on the position; notional never paid; one settlement currency; `equity == start + realized + unrealized - commission` and NAV agrees; a hedge instrument with no declared multiplier fails the strategy visibly and trades nothing |
| 5. Canonical path | every fill belongs to an OMS order and comes after the decision; no optimistic assumption |
| 6. Failures surfaced | twelve risk refusals in `result.state.risk.history`, each with its violation, none filled; no strategy failure in the clean run |
| 7. Lineage | the manifest's dataset is the combined version the run recorded, with each part recorded, checked and listed for a rerun; the parts reversed are another version and refused; the manifest names the schedule and refuses one missing the run's last delivery; the strategy fingerprint changes with the sentiment set |
| 8. Deterministic rerun | identical `RunDigest` in process, and the same `result_id` in fresh interpreters under two `PYTHONHASHSEED`s |
| 9. Replay agrees | the replay's orders, fills, valuation, deliveries and every decision equal the backtest's; a replay paused at **every** one of the 87 boundaries, written to JSON and restored with fresh objects, resumes to the uninterrupted replay's digest; restoring it with another schedule is refused; a backtest stored at **every** record and continued with freshly built objects reaches the same `result_id` |
| 10. Clean installation | run from the built wheel and sdist by `tests/installed_smoke.py` (section 8.1) |
| 11. v4.1.0 contracts | the full suite, the manifest diff (additive only), the v4.1.0 snapshot payloads upgrading, and the certificate (no figure moved) -- section 8 |

**What it does not prove.** That sentiment or inflation predicts returns (the
prices are synthetic and the result loses money), that the strategy is
well designed, or that any of it behaves the same against a real venue.

---

## 8. Validation

### 8.1 Commands and results on the final tree

Run in the checkout's dev venv (CPython 3.12.4) unless stated; the figures are
the FUT-002 milestone's, on the tree it committed (the FUT-011 commit's own, measured the same way, are in its commit message: 10,053 / 10,054 passed).

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `python -m ruff check .` | All checks passed |
| Format | `python -m ruff format --check .` | 1,315 files already formatted |
| Types | `python -m mypy .` | no issues in 1,213 source files |
| Tests | `python -m pytest -W error` | **10,115 passed, 3 failed** -- exactly the three version-keyed guards of section 8.2 (one of them the dev venv's stale metadata); 0 skipped |
| Examples | every `examples/[0-9]*.py` under `python -W error` | all 75 exit 0 |
| Certification | `python -W error docs/audit/scripts/certify_release.py --check` | 11 checks passed, matching the re-certified certificate (only the source digest and the API manifest's digest and export count moved; no determinism, parity, reproducibility or numerical figure, and no `result_id`) |
| Public API | `python docs/api/generate_public_api.py`, then the manifest tests | 2,683 exports, 35 shared names (2,628 at v4.1.0); additions and defaulted keywords or fields only, none removed or re-signed incompatibly (CHANGELOG `[Unreleased]` lists each) |
| Fresh 3.12 | the suite in a fresh venv from a clean mirror | CPython 3.12.4: **10,116 passed, 2 failed** (the two version-keyed guards; the metadata test passes) |
| CPython 3.13 | the suite on 3.13.17 (python-build-standalone, scratch) | CPython 3.13.17: **10,116 passed, 2 failed** (the same two) |
| CPython 3.14 | the suite on 3.14.9 | CPython 3.14.9: **10,116 passed, 2 failed** (the same two) |
| Build | `python -m build` in the clean mirror | `alphalab-4.1.0.tar.gz` and `alphalab-4.1.0-py3-none-any.whl` (labelled 4.1.0 until the version is set; kept in the scratchpad, `dist/` untouched) |
| Distributions | `python -m twine check --strict dist/*` | both PASSED |
| Installed artifacts | `python docs/audit/scripts/release_preflight.py distributions dist --version 4.1.0 --record dist/VERIFICATION-4.1.0.txt` (each artifact alone in a fresh environment, smoke-tested from outside the tree, examples 11 and 70 to 75 with the network refused) | **PASSED**: each artifact installed alone, `pip check` clean, 612 modules, 2,683 public names as the manifest records, examples 11 and 70 to 75 and the consumer contract run with the network refused; wheel `94d8998c...d117`, sdist `66474307...5d5a` |
| Benchmarks | `benchmarks/benchmark_{backtesting,replay_engine,runtime,execution_pipeline,strategy_runtime}.py` under `-W error`, on this tree and on `git archive v4.1.0`, one after the other on one host | no regression on the final tree: judged backtest scaling 3.98x against v4.1.0's 4.03x, replay 1.02x the backtest against 1.03x, run layer +2.56% against +2.07% over the bare step (within the run-to-run spread the earlier milestones measured, +2.10% to +2.33%), pipeline 3.99x against 4.01x; every parity line identical. A queue model, measured on its own: a run resting about 150 orders at every one of 6,000 quote and print events costs 7.42 s with `PriceTimeQueue` against 6.84 s without (median of five; 149 fills each). Measured on the FUT-011 tree; FUT-002 adds nothing that runs per event (a configuration is checked at configure and decoded at restore), so the run benchmarks were not repeated for it |
| Mutation | the FUT-011 rules and the FUT-002 checks, each broken in a scratch copy | 15 of 15 caught by `tests/integration/test_queue_model.py`; 13 of 13 by `tests/regression/test_typed_strategy_configuration.py` |
| Whitespace | `git diff --check` | clean |

### 8.2 The three guards that waited for the version

Until the version was set they read `alphalab.__version__`, then `4.1.0`, and
failed by construction: `test_every_removed_or_changed_name_is_in_the_changelog`
wanted the changed names in the released `[4.1.0]` entry, and
`test_no_ledger_entry_is_assigned_beyond_this_release` refused the ledger's
`v4.2.0` entries. The maintainer approved v4.2.0 as the development and release
target (section 9); with the version set, both pass on the changes themselves --
the CHANGELOG's `[4.2.0]` section, which the first guard showed lacked three
rows (`BrokerAssetClass`, `InstrumentRegistryRecord`, `InstrumentRegistrySnapshot`),
now added. The third, `tests/unit/test_package_metadata.py::test_package_exposes_version`,
failed in the dev venv only: its editable install had been made at 4.0.0 and
its metadata never refreshed. Reinstalled (`pip install --no-deps -e .`), it
reports 4.2.0 and passes; every fresh environment passed it throughout.

### 8.3 What was not run

Hosted CI ran on the commit before the FUT-011 milestone (`dc508e9`, draft PR
#18): on Ubuntu and macOS under CPython 3.12, 3.13 and 3.14, ruff, format and
mypy passed and pytest reported **10,021 passed, 2 failed** in each of the six
jobs -- the two version-keyed guards of section 8.2 -- so the jobs' later steps
(examples, certification, build, distribution checks) did not run there; CodeQL
passed. The FUT-011 commit's own hosted run is recorded on the PR, not here.
Windows remains untested (BDY-027).

---

## 9. Versioning

**Decision: v4.2.0.** The maintainer approved it as the development and release target (2026-10-30), on the grounds below; the checklist that follows is applied except where it publishes (no tag, release, upload or change to `main` is made before publication is decided).  It adds a public module
(`alphalab.backtesting.schedule`) and modules (`alphalab.backtesting.algorithmic`,
`alphalab.backtesting.queueing`, `alphalab.execution.queue`,
`alphalab.instrument.terms`, `alphalab.strategy.configuration`), names in
`alphalab.api`, `alphalab.portfolio`, `alphalab.instrument`, `alphalab.runtime`,
`alphalab.strategy` and `alphalab.backtesting`, three
persisted enum members (`AssetType.BOND`, `AssetType.INDEX`,
`ExecutionRouting.ALGORITHMIC`), and
defaulted keywords or fields on existing bindings; it moves two persisted
schemas forward (instrument 3 -> 4, replay 1 -> 2), each with an upgrade that
reads every earlier payload, and leaves every v4.1 run `result_id` and manifest
id where it was. `docs/api/PUBLIC_API.md` says "a minor release adds and
corrects and does not break", and semantic versioning puts added functionality
in a minor release. A v4.1.1 label would mislabel it, which the brief forbids.
The alternative that keeps a v4.1.1: release KD-005 and DOC-012 alone as v4.1.1
(a backward-compatible correction), and the rest as v4.2.0.

**Checklist once the number is chosen** (each is mechanical; the guards above
check them):

1. `alphalab/common/_version.py` -> the version.
2. `CHANGELOG.md`: `# [Unreleased] ...` -> `# [X.Y.Z] - date`.
3. `docs/api/history/4.1.0.json` (v4.1.0's manifest, from `git show
   v4.1.0:docs/api/public_api.json`) added, with its row in `docs/api/history/README.md`.
4. `docs/api/PUBLIC_API.md` "At vX.Y.Z the manifest records ..." and the
   release-facing version markers (`tests/regression/test_version_markers_agree.py`
   lists them; `README.md`'s version and test count, `docs/README.md`,
   `docs/INTEGRATION.md`'s pin range, `SECURITY.md`) updated.
5. The ledger's `release: "v4.2.0"` fields kept, or set to the chosen number.
6. `preflight.yml`'s release version, if a preflight is to run.
7. Re-certify (`certify_release.py`), rebuild, re-verify the artifacts.

---

## 10. What remains

| ID | Gap | Target | Class of work |
| --- | --- | --- | --- |
| FUT-014 | a strategy hook that instructs an exercise | v4.3 | a strategy-contract addition |
| FUT-016 | baskets, cash in lieu, quanto, Bermudan exercise | v4.3 | further declared terms |
| FUT-015 | settlement-date (T+n) accounting | v5.0 | a portfolio schema step |
| FUT-017 | margin, collateral and forced liquidation | v5.0 | new capability; depends on FUT-015 |

**Release-blocking for the candidate:** the version decision (section 9) and
the three guards it closes; hosted CI on Linux and macOS for the branch's last
commit, recorded on draft PR #18.
No engine gap above blocks it: each is accepted future work with its interim
behaviour stated.

**Next, in dependency order:** (1) the maintainer chooses the number and the
checklist is applied; (2) hosted CI on the pushed branch is read (draft PR #18);
(3) FUT-014,
which lets a strategy's own hooks take the exercise decisions FUT-013 now carries
out, on the same lifecycle path; (4) FUT-016, the contract terms the lifecycle
refuses today; (5) FUT-015 then FUT-017, which change the
portfolio's payload and belong with a major release or a stated exception.

**External (unchanged):** venue and data-vendor adapters, credentials, market
data, FX rates, holiday and reference data, settlement prices, text-to-sentiment
models, hosted isolation of user-supplied strategy code.

**Remaining risks.**

* *Correctness.* A strategy that reads bars through its context saw `None`
  until this candidate; any host strategy written to treat `None` as "no data"
  will now trade where it did not. A final settlement price is the host's; the
  engine checks when it is applied and that it is not negative where that
  cannot be right, not that it is the exchange's. A bond traded at clean prices
  books no accrual. An exercise's or expiry's settlement value is the host's,
  applied at the instant its event states. A delivery the book cannot cover
  stops the run. A run's resting limit orders fill by the simulator's touch rule
  unless a queue model is given; `PriceTimeQueue` reads the displayed top of the
  book and signed prints, so depth beyond it, hidden liquidity and pro-rata
  books are not modelled, and an execution plan's limit children are not
  governed by it; a child's fill reaches its strategy through the portfolio,
  not `on_fill`.
* *Numerical.* Unchanged: certified identities per host class for the
  standalone engines (LIM-005); the construction solver is cubic (PRF-005).
* *Performance.* `MarketView.bar` is linear in the latest-bar index per call; a
  feature evaluated inside a strategy recomputes its window per call. Neither is
  measured at scale.
* *Reproducibility.* A run's own record names its last delivery, not its
  schedule; the schedule is identified by the manifest when the producer passes
  it. The certificate records this host as macOS 27.0.1, where v4.1.0's
  recorded 26.6.2.
