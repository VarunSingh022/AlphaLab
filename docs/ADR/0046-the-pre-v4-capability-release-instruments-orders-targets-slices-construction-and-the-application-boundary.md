# ADR-0046: The Pre-v4 Capability Release — Instruments, Orders, Targets, Slices, Construction and the Application Boundary

## Status

**Accepted and implemented in v3.11.0.**

The second of the four releases the pre-v4 audit plans before the v4.0 freeze
(`docs/audit/PRE_V4_MASTER_AUDIT.md`; the item-by-item plan of record is
`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`). v3.10.0 corrected the canonical
path; v3.11.0 gives it the capabilities a strategy needs before its API is
frozen — what an instrument *is* when it is not a fully paid share, how an
order is asked for, what position a strategy wants, when an instant is
complete — and moves out of the library the three packages that kept state
about people using software rather than about markets.

It **removes** three packages (`alphalab.studio`, `alphalab.workbench`,
`alphalab.enterprise`) and the reference REST venue transport with its
credentials (`alphalab.broker.transport`, `alphalab.broker.venue`, now
`tests/reference_adapter`). It adds **one package edge**
(`portfolio_optimizer` → `conventions`) and no package. Snapshot schemas:
pipeline 4→5, run 2→3, allocation 1→2, OMS 1→2, portfolio 4→5, live 1→2,
instrument 1→2, broker 1→2; every older payload is upgraded on read, and each
upgrade that must decide something warns (`SchemaUpgradeWarning`) rather than
deciding silently.

Amends **ADR-0018** (the permission check is a `PermissionAuthority` the
application supplies), **ADR-0030** (the slice cursor is continuation state on
`RunState`, which now has nine fields; the pipeline's sixteen are unchanged),
**ADR-0032** and **ADR-0033** (the removed packages), **ADR-0035** (the strategy
definition moved to `alphalab.strategy`) and **ADR-0043** (construction may
read the conventions package). Depends on **ADR-0045** throughout.

---

# Context

The audit's v3.11 allocation was thirty-five ledger items. Most were
capabilities the v3.10 path could not express at all: every instrument was a
fully paid unit of one, so a future's P&L was booked as if its notional had been
paid in cash; every order was a market order; a strategy could only ask for
order deltas and had to count its own position; a multi-asset strategy was
dispatched once per record and never saw a complete instant; prices had to be
positive, so crude oil in April 2020 was unrepresentable; and a construction
could not pay for trading or produce a quantity a venue would accept. Others
were boundary corrections — an RBAC implementation nothing consulted, UI panel
state, a project manager for results computed elsewhere, API keys for a made-up
REST protocol — and research-integrity gaps: forward returns entered at the
close the signal was computed from, delisted names vanished from the returns,
and there was no correction for testing many strategies at once.

Implementing and auditing them found nine defects, seven of which had shipped
(NUM-012, NUM-013, ALC-007, BRK-009, INS-001, LIV-001 and PRF-007); they are
fixed and recorded as findings of this release in the ledger.

---

# Decision

## 1. The library stops at the domain engines

`alphalab.enterprise` (principals, sessions with TTL, RBAC, workspaces,
secrets), `alphalab.workbench` (themes, panels, tabs, layouts) and
`alphalab.studio` (projects, pipelines of caller-supplied metrics) are removed
(ledger BND-002, BND-003, SCF-001; BDY-010 re-examined and kept). Each kept
state about who is signed in, what is on their screen or which project they
are in — the host application's responsibility — and none of it was read by
anything the library computes.

What the removed packages did that *is* the library's stays, in the package it
belongs to:

* a strategy's identity, version and parameters —
  `alphalab.strategy.StrategyDefinition`, beside the class registry, its
  parameters the registry's `ParamValue`s;
* who may promote or deploy — `lifecycle.governance.Governance(authority,
  actor_id, approval_required_in)`, where `authority` is a
  `PermissionAuthority` the application supplies (`StaticPermissions` for a
  research setting). The actor is recorded as a string, as ADR-0018 decided.

The reference REST adapter and its `VenueCredentials` moved to
`tests/reference_adapter` (ledger BRK-007): it documents how a host adapts a
venue to `BrokerProtocol` and is not importable from `alphalab`. Vendor
transports, signing and credentials are the application's.

## 2. An instrument declares its economics

`InstrumentEconomics(multiplier, settlement, lot, minimum_notional,
allows_negative_prices)` rides on the `InstrumentRecord`, outside its identity
key: economics describe an instrument without changing what it is (ledger
ACC-005). Nothing has a default for the multiplier or the settlement.
`economics_for` answers `CASH_EQUITY` for an undeclared equity, crypto, FX or
cash instrument and **refuses** an undeclared future or option: assuming a
multiplier of one for a future produces a number, not a refusal.

`SettlementModel` has four members. `CASH_EQUITY` and `OPTION_PREMIUM` pay
their notional at the trade; `FUTURES_VARIATION` and `PERPETUAL` pay nothing but
costs at the trade and settle their gain or loss in cash at every mark and at
every fill (settle, then resize). A position's *market value* is its notional
exposure — what risk reads — and its *carrying value* is what it adds to
equity; the book keeps `uncarried_value`, zero for every fully paid position,
so every v3.10 valuation is byte-identical. A run that declares no registry
books every instrument as a fully paid unit of one: stated, not assumed.

Cash flows and splits reach a position through the path
(`apply_cash_flow`, `apply_split` on the pipeline, the run and the live
session; ledger ACC-006). A split under simulated routing cancels the asset's
working orders and scales each strategy's attributed position; under external
routing it is refused while orders are working, because the venue owns them.
Spin-offs, mergers and cash in lieu are booked by the caller from those two
primitives — an explicit boundary, not a gap discovered later.

Prices may be zero or negative where the economics allow it; a commission may
be negative — a rebate a venue pays a liquidity provider — through
`ExecutionSimulator.maker_commission_model` (ledger ACC-007). The market layer
accepts any finite price as data; whether a price is tradeable is the
instrument's question, asked at the gate. A wire sentinel such as `-1` is the
provider adapter's to translate.

## 3. An order carries its terms

`OrderTerms(order_type, limit_price, stop_price, time_in_force, expire_at)`
travels from the intent through allocation, risk and the OMS to the venue
(ledger EXE-003). Allocation nets only intents with equal terms: a limit and a
market order for one asset are two orders. Simulation rests what cannot fill
now: a resting limit fills at its limit, or better at a bar's open, as a
*maker* — no spread, slippage or impact, and the maker commission; a stop
becomes a taker at the price that triggered it; a stop-limit records when it
triggered and then works as a limit; IOC and FOK end after one turn; GTD and DAY
expire at `expire_at`. **A simulated DAY order must state its `expire_at`**
(from `MarketCalendar.next_close`): the pipeline holds no calendar, and
inferring a session close is the kind of default v3.4 removed. Reading
exchange calendars inside simulation is planned for v3.12.

## 4. A strategy may state the position it wants

`IntentKind.TARGET_QUANTITY` and `TARGET_WEIGHT` state a position, not an order
(ledger FEA-001). A target is measured against the strategy's **own** position —
its share of every fill of an order it contributed to, which allocation keeps —
plus its share of what is still working, never the account's: two strategies
trading one instrument each reach their own target. The difference is rounded
**toward zero**, onto whole units when the run trades whole units and then onto
the instrument's lot, so a target is approached and never overshot; an order
below the instrument's minimum notional is refused rather than scaled up. A
weight is a fraction of the strategy's budget — a stated figure, not its
equity — at the current price, so the same weight at a new price is a different
quantity.

What an order **commits** against the allocation budget is the exposure it
*adds* to the account's committed position (ledger ALC-007). A sale that
reduces a long commits nothing; reductions in a batch are sent before
additions, so the risk gate judges each purchase with the sales that fund it
already working. Until v3.11 a sale committed its whole notional, and a fully
invested book could not rotate: the budget refused the batch and dropped the
sale with the purchase. A cash account still cannot spend the proceeds of a
sale that has not executed — the buying-power check refuses that, correctly —
so a rotation sells on one close and buys on the next.

## 5. A strategy is told what it subscribed to, when an instant is complete, and what happened

Subscriptions are enforced (ledger EXE-007): `"*"`, a topic (`ticks`, `quotes`,
`trades`, `bars`, `fills`, `orders`, `timers`, `slices`) or a market topic
scoped to one asset (`"bars:<asset>"`); an unsubscribed event builds no
context and dispatches nothing. A v3.10 payload's declared subscriptions were
never enforced, so its upgrade subscribes it to `"*"` and warns.

`on_start` runs before a strategy's first dispatch, `on_stop` and
`on_shutdown` through an explicit `RunEngine.stop`, and after every step each
contributing strategy is told of its fills (its attributed quantity) and of
every order it touched (ledger EXE-005). What a strategy asks for in feedback
rests until the asset's next event, so no step can fill against itself.

A **slice** is the instant complete (ledger EXE-004): after the last record
carrying a timestamp, a strategy subscribed to `slices` that defines `on_slice`
is called once with every asset of the instant. Its orders rest until each
asset's next record. The cursor that says which instant was last closed is
continuation state, so it is `RunState.last_slice_at` — a ninth `RunState`
field — and not a seventeenth `ExecutionPipelineState` field, which ADR-0030's
budget forbids. A run none of whose strategies defines `on_slice` closes its
slices without dispatching anything and is byte-identical to v3.10.

## 6. Every entry point of a run computes in the accounting context

v3.10 pinned the engines to `ACCOUNTING_CONTEXT`; the drivers were not pinned
(ledger NUM-012, shipped). A sale's quantity was negated in the caller's context
— unary minus rounds a `Decimal` — so under a precision of five a sale of
185.295944 booked 185.30, and a `LiveSession` under a precision of five raised
from the venue mirror. Every public entry point of `ExecutionPipeline`,
`RunEngine` and `LiveSession` now runs pinned, strategies dispatched from them
included, so a run is a function of its inputs; a structural test holds the
set and a long-digit oracle runs backtests and live sessions at precisions 3, 5
and 8. Lot arithmetic is exact or refused whatever the context
(`round_down_to_lot` at precision five had returned 12346 for 12345.9 — ledger
NUM-013, shipped since v3.4).

## 7. Live: sequenced, persisted, held, converted

A venue report may carry a sequence number; an older one is stale, an equal one
a duplicate or a conflict, a newer one applied (ledger BRK-002). Cancel and
amend requests persist in the live snapshot; child-order bindings are rebuilt
exactly from the mirror on restore rather than stored twice (BRK-003). An order
a strategy works through an algorithm can be **held** from direct routing
(`LiveSession.hold`, persisted; ledger LIV-001). Live settlement takes the FX
rates a multi-currency fill needs (EXE-009). `PaperBroker` requires a cost
model — `FREE` is a stated choice, not a default — and books shorts and sale
commissions correctly (BRK-008; BRK-009, shipped).

## 8. Research that cannot leak

Forward returns enter a stated number of observations after the signal
(`forward_returns(..., lag=...)`, required; a study that declares no
implementation lag is refused by `run_study`), and a delisted name's final
return is included at its delisting value rather than dropped (DAT-002,
DAT-003). Walk-forward optimization composes the splits and the sweep (FEA-003):
every candidate is fitted on a fold's training instants and scored on its
validation instants — the objective is never handed a test instant while a
selection is made, which a brute-force test checks — and the selection alone is
reported on the test window. The study must name the design it searched, and
the seed is the study's.

Families of tests are corrected by Holm, Benjamini-Hochberg or
Benjamini-Yekutieli, each with its dependence assumption stated; the
probabilistic and deflated Sharpe ratios correct for non-normal returns and
for the number of trials (OFE-005). The information coefficient carries a
Newey-West standard error at lag `horizon - 1` (OFE-006). Neutralization against
several continuous exposures is a least-squares residual by Householder QR that
refuses an ill-conditioned design (OFE-004). Benchmark-relative statistics
report active return, tracking error, information ratio, beta, alpha and
capture ratios over aligned levels (FEA-002). Black-Scholes takes a carry
(dividend yield, foreign rate, or none for a future) — Merton, Black-76 and
Garman-Kohlhagen as one formula (NUM-005).

## 9. Data and identity

`TimeFrame` is a value — a count and a unit — rather than a closed list, so a
30-minute or 2-hour bar is expressible; `alphalab.marketdata.Timeframe` is
removed and a bar's VWAP and trade count are optional where a feed does not
report them (DAT-005). Provider aliases may be dated, so a symbol that meant
one company before a date and another after resolves by the date asked
(DAT-004; restoring an instrument snapshot had also lost aliases registered
after construction — INS-001, fixed). A reproducibility manifest may name the
engine build — a digest of the engine's source, not only its version string —
and the IANA database version local times were computed with (REP-002,
DAT-007). Content identities render a declared `Decimal` by value, so `100`
and `100.00` state one thing, under v2 schemes (DET-006).

## 10. Construction pays for trading and answers in quantities

The covariance can be shrunk (Ledoit and Wolf 2004, intensity estimated),
exponentially weighted (RiskMetrics) or implied by a factor model (`B F B' +
D`); each records its parent and how it was derived (OFE-002). A mean-variance
objective may charge `LinearCosts` for trading away from the current book,
solved exactly: each orthant around the book is a quadratic program, and a
subgradient certificate says when the solver has found the right one; a
degenerate vertex it cannot certify is reported as such, never returned
uncertified. `round_to_lots` turns weights into quantities toward zero and
names every asset whose weight is less than one lot.

**Explicit boundaries**, so they are not discovered later: cardinality and
joint lot selection are integer programs, and no integer solver is part of the
library; CVaR and drawdown objectives need a linear program over scenarios,
which the quadratic solver is not; multi-period construction is a sequence of
single-period problems the caller composes.

## 11. Performance: known, stated, bounded

v3.10 had raised the per-operation constant (ledger PRF-006). v3.11 makes a
state transition build one state rather than a chain of them, caches the field
walk `dataclasses.replace` repeated on every call (`alphalab.common.evolve`,
identical results and errors), gives order ids a cached hash, mints identifier
text without building a `UUID`, keeps `PersistentMap`'s insertion bookkeeping
in flat lists the garbage collector does not walk, and updates the book's exact
totals with one delta per fill.

The release measurement found two more costs, both older than v3.11 and both
fixed in it. A slice of an append-only log copied the whole log, and the
pipeline takes two per fill, so a run was quadratic in its length (PRF-007,
since v2.1). And a persistent map's chain held a pair per write, and a rebase
copied every live key into a list and a pair: the OMS benchmark spent half of
its accept-and-fill stage in the cyclic collector (PRF-008, since v3.10's
compaction). A chain is now one flat list.

Measured side by side on one machine, five interleaved rounds against v3.9.0,
each benchmark's own timing: the OMS benchmark 0.83x (0.72–0.89), a one-asset
backtest and replay 1.05x (0.99–1.06), the execution pipeline 1.01x
(0.92–1.07), the portfolio-engine micro-benchmark 1.69x slower (1.51–1.72; exact
per-currency totals, money at each currency's minor unit and instrument
economics on every fill). Every round is faster than v3.10.0. The budget this
release states — the OMS within 1.1x of v3.9, one-asset paths within 1.25x, the
portfolio micro-benchmark within 2.0x — holds in every round.

---

# Consequences

* A strategy written for v3.10 runs unchanged: a delta intent means what it
  meant, a market order is still the default, an undeclared instrument is still
  a fully paid unit of one, and a run with no `on_slice` is byte-identical.
* Breaking changes are listed in the CHANGELOG's migration table: the three
  removed packages, `marketdata.Timeframe`, `Intent.execution_directive`
  (replaced by `terms`), `forward_returns`' required `lag` and `delistings`,
  `PaperBroker`'s required cost model, `Governance`'s authority, and positive
  prices no longer being required by the market layer.
* The allocation budget changes meaning for batches that reduce positions:
  such a batch commits less and is refused less often. A batch whose positions
  are unknown to the caller commits every order in full, as before.
* A long run costs in proportion to its length: until v3.11 every fill copied
  the execution history and the portfolio's events so far (PRF-007), so a run of
  16,000 events cost half again as much per event as one of 2,000 (1,136 against
  754 microseconds, with the collector paused).
* Deferred, with a release: exchange calendars inside simulation for DAY orders
  (v3.12).

# Rejected alternatives

* **Keeping the removed packages as deprecated shims.** A shim is code the
  library would have to keep correct and test; the packages held no state the
  library reads, and the migration is a rename or a host-side replacement.
* **A target measured against the account's position.** Two strategies trading
  one instrument would each see the other's position and fight over it.
* **Rounding a target to the nearest unit.** Found by rewriting example 08: the
  nearest unit overshoots a target, and a rebalancer that overshoots trades
  back on the next bar.
* **Letting a working sale's proceeds fund a purchase.** A cash account does not
  have that cash until the sale executes; a rotation that assumed it would buy
  on credit it was never given.
* **A seventeenth pipeline field for the slice cursor.** ADR-0030's budget;
  the cursor is continuation state and belongs with the run's.
