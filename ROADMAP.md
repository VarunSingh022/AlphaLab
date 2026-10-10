# AlphaLab Roadmap

What AlphaLab has delivered, what it deliberately does not do, what it depends
on from outside, what it states as a limitation, and the one piece of work
accepted for later. The release-by-release record is `CHANGELOG.md`; the
decisions are `docs/ADR/`; the plan of record for every finding since the pre-v4
audit is `docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`.

**v4.0.0** freezes the universal quantitative research and execution engine. It
adds no package and moves no boundary. It is the release in which the v2.0.0 to
v3.13.0 history was read in full -- every deferral, non-goal, limitation and open
question, 257 items the pre-v4 inventory had not read, each classified with
evidence (`docs/audit/V4_HISTORICAL_INVENTORY.md`) -- the canonical path was
re-audited by somebody who had not written it, and four defects it found were
fixed, each with a test that fails without the fix (ADR-0049,
`docs/audit/V4_RELEASE_AUDIT.md`).

| Class | Meaning |
| --- | --- |
| **Delivered** | Built, tested, and described by the documentation |
| **Deliberate boundary** | Not built, on purpose, with a reason and usually a regression test |
| **External dependency** | Not AlphaLab's engineering to do -- data, credentials, a vendor's API |
| **Known limitation** | Built, with a stated limit, its reason and its ledger entry |
| **Future work** | Accepted for a later release, with its interim behaviour and the criteria that close it |

Nothing classed as a deliberate boundary, an external dependency or a known
limitation is a defect, and none blocks a release. A defect is fixed, not
listed: the ledger holds none open.

---

# Delivered

The four major milestones; `CHANGELOG.md` has every release between them.

| Release | What it established |
| --- | --- |
| **v1.0.0** | The foundation: immutable domain models, deterministic engine APIs, the strategy runtime, the universal data engine, the replay engine and the portfolio optimizer |
| **v2.0.0** | The canonical execution domain: one `Side`, one `OrderRequest`, one lifecycle `Order`, float timestamps, and `ExecutionPipeline` as the one spine (ADR-0008, ADR-0009); the v2 line then unified backtest and replay, market data, the broker boundary, the lifecycle, durable state and multi-currency settlement |
| **v3.0.0** | The architecture frozen and the documentation made true; v3.1 to v3.13 then added data ingestion, research methodology, institutional backtesting, global markets, production intelligence, evaluation contracts, point-in-time research, portfolio and risk construction and the universal execution contract, and the four pre-v4 releases made the canonical path correct, complete and hardened (ADR-0036 to ADR-0048) |
| **v4.0.0** | The freeze of the universal engine contract: the history read and classified, the canonical path re-audited and four defects fixed, a strategy built end to end through the public API by an outsider's route, and the public API held stable from here (ADR-0049) |

## The engine series (v1.34.0 – v2.0.0)

Each shipped as its own release, and each package is a standalone, individually
tested engine. The scope notes are kept below as the historical record.

| PR | Package | Shipped in |
|----|---------|------------|
| PR-034 | Feature Store | v1.34.0 |
| PR-035 | Factor Library | v1.35.0 |
| PR-036 | Options Engine | v1.36.0 |
| PR-037 | Futures Engine | v1.37.0 |
| PR-038 | Crypto Engine | v1.38.0 |
| PR-039 | Macro Engine | v1.39.0 |
| PR-040 | Alternative Data | v1.40.0 |
| PR-041 | Machine Learning | v1.41.0 |
| PR-042 | Deep Learning | v1.42.0 |
| PR-043 | Reinforcement Learning | v1.43.0 |
| PR-044 | Cloud Research | v1.44.0 |
| PR-045 | Cluster Scheduler | v1.45.0 |
| PR-046 | Experiment Tracking | v1.46.0 |
| PR-047 | Model Registry | v2.0.0 |
| PR-048 | AI Research Assistant | v2.0.0 |
| PR-049 | Deployment Manager | v2.0.0 |
| PR-050 | AlphaLab Enterprise | v2.0.0 |

## The v2 line

Full detail is in `CHANGELOG.md`; the reasoning is in `docs/ADR/`. Each entry
names what the release established and the ADR that records the decision.

| Release | Established | ADR |
| --- | --- | --- |
| **v2.0.0** | Canonical execution domain models: one `Side`, one `OrderRequest`, one lifecycle `Order`, float timestamps. Four new standalone packages | ADR-0008, ADR-0009 |
| **v2.1.0** | Mark-to-market; the **exact** accounting identity and one rounding policy; O(1) amortized engine histories | — |
| **v2.2.0** | Backtest and replay on **one** step, so parity is structural; persistent containers; per-order reservation ledger; seeded identifiers | ADR-0010 |
| **v2.3.0** | One canonical market-data model over one wire record with an explicit normalization boundary; one broker boundary that `brokers` routes; four environments, one path | ADR-0011, ADR-0012 |
| **v2.4.0** | The model and strategy lifecycle: numbered `StrategyVersion`, typed refs, evidence with content-derived ids, a gated promotion, the deployment ledger as the one source of truth | ADR-0013 |
| **v2.5.0** | Typed `capture` / `restore` over typed decoders; `market.provider` connecting a provider's history to a session; explicit unordered-source semantics; partial fills terminate | ADR-0014 |
| **v2.6.0** | Allocation authority: outstanding capital enforced, terminal orders release what they hold, real strategy attribution replacing `"ALLOC-NETTED"` | ADR-0015 |
| **v2.7.0** | Instrument identity derived (`uuid5` over a canonical key) rather than asserted; dataset provenance derived from the run rather than claimed | ADR-0016, ADR-0017 |
| **v2.8.0** | One meaning per currency field; a valuation that refuses to span two currencies; a run that records why it produced no fills | ADR-0019 – ADR-0021 |
| **v2.9.0** | Deterministic identifier continuation; the execution path round-trips; a duplicate venue fill applies once; a working external order can be ended | ADR-0022 – ADR-0024 |
| **v2.10.0** | `StrategyStateProtocol` and a two-sided codec; `StrategyContext` populated from the **marked** portfolio, with contribution-based order shares | ADR-0025, ADR-0026 |
| **v2.11.0** | The security master's mechanism: `classify_instrument` writes a sector without touching identity, and the pipeline freezes it onto each fill | ADR-0027 |
| **v2.12.0** | The instrument registry becomes the **currency authority**. Two seams: `_process_requests` drops a foreign instrument, `_apply_report_to_portfolio` refuses a mismatched report. `STRICT_MATCH` and no policy object | ADR-0028 |
| **v2.13.0** | `RunStateStore` as the one owner of durable run state — payload-agnostic, one real file backend, one named double, no fallback. Cross-process continuation proven byte-identical | ADR-0029 |
| **v2.14.0** | Runtime unification: `RunEngine` over `RunState` owns the run; `ExecutionPipeline` keeps the step; the drivers hold no state | ADR-0030 |
| **v2.15.0** | Five capabilities that had a contract and nothing behind it: venue transport, WebSocket streaming, artifact bytes, classification provenance, the completed strategy context | ADR-0031 |
| **v2.16.0** | Three joins: the live driver, governance/RBAC/audit, and FX valuation — plus the refactor audit that classified twenty-one structural findings | ADR-0032, ADR-0033 |
| **v2.17.0** | Settlement-level multi-currency, the FX rate feed, the strategy-class registry; seven deprecated surfaces removed with no aliases; zero skips and zero warnings | ADR-0034, ADR-0035 |

## The v3 line

| Release | Established | ADR |
| --- | --- | --- |
| **v3.0.0** | The architecture frozen; the documentation truth freeze. No capability added | ADR-0034 |
| **v3.1.0** | Universal data ingestion: CSV, schema detection, structured validation, cleaning policies, calendars, corporate actions, provenance and the derived dataset version | ADR-0036 |
| **v3.2.0** | Strategy research and validation: features with derived identity, factor research, walk-forward, purged and embargoed cross-validation, robustness and overfitting diagnostics | ADR-0037 |
| **v3.3.0** | Institutional backtesting: itemized execution costs, capacity, attribution, risk decomposition and a scenario contract | ADR-0038 |
| **v3.4.0** | Global markets: one convention authority, continuous futures, implied volatility, FX crosses, crypto venues, a fixed-income foundation | ADR-0039 |
| **v3.5.0** | Production intelligence: the research-to-live progression, deployment specifications, runtime health, expected/paper/live comparison, reconciliation | ADR-0040 |
| **v3.6.0** | Evaluation contracts: strategy fingerprints, reproducibility manifests, certification properties, portability | ADR-0041 |
| **v3.7.0** | Point-in-time research: events, alternative data, fundamentals, regimes and adaptive strategies that replay exactly | ADR-0042 |
| **v3.8.0** | Portfolio and risk: one risk model, constrained construction by a certified solver, risk budgets, multi-strategy books, capital allocation | ADR-0043 |
| **v3.9.0** | The universal execution contract: capabilities, one transition table, algorithms, routing, execution analytics | ADR-0044 |
| **v3.10.0** | Pre-v4 correctness: risk on the projected book, money exact at each minor unit, honest analytics, next-event fills, a linear canonical path, upgradeable snapshots | ADR-0045 |
| **v3.11.0** | Pre-v4 capability: instrument economics, order terms, target positions, slices, leak-proof research, the application's packages moved out | ADR-0046 |
| **v3.12.0** | Pre-v4 hardening: numerics at their edges, exact restores, calendars in simulation, ceilings, classification limits, retention and checkpoints, a stress program | ADR-0047 |
| **v3.13.0** | The last pre-v4 release: American options, the optimal split, estimated urgency, a rerun harness, cron timers, one name per contract, the API as data, a release certificate | ADR-0048 |

## v4.0.0

- **The history, read and classified** (TST-017): the v2-era ADRs' non-goal
  sections, the CHANGELOG's gap, limitation, deferral and still-open sections and
  `nowandfuture.md`'s open questions -- 257 items in 39 sections, each a row of
  `docs/audit/V4_HISTORICAL_INVENTORY.md` and a `HIS-xxx` ledger entry, held to the
  ADRs and the CHANGELOG by a test.
- **Defects found and fixed**: a restore that accepted a live object configured
  otherwise (PER-008); an ingestion that dropped unreadable rows under a refusing
  policy, with `MissingValuePolicy` read by nothing (DAT-010); option pricing that
  returned `NaN` for a non-finite input (NUM-015); and fractional fills in a
  whole-unit run (EXE-011).
- **A strategy, built by an outsider's route** (DOC-009): a getting-started guide,
  `examples/70_build_a_strategy.py` checked against arithmetic done by hand, and
  `start_strategy` / `context_factory` in `alphalab.strategy`.
- **Accepted future work**, tracked: replay resumability (FUT-001), below.

# Deliberate boundaries

Not built, on purpose. Each has a reason, and most have a regression test that a
future "simplification" would have to break first —
`tests/regression/test_shared_names_stay_distinct.py` and
`test_venue_concepts_stay_distinct.py` hold the reasons.

- **No single runtime spanning all engines.** The run layer has one owner
  (`RunEngine` over `ExecutionPipeline`, four drivers), and `alphalab.lifecycle`
  is joined to it by a query with a refusal. Reporting, the feature store, the
  factor library and the rest stay standalone: ADR-0009 is a description of the
  codebase that became a rule, and chaining them would create a runtime nobody
  asked for.
- **No allocation visibility inside `StrategyContext`.** Reservations and
  contributions are *post*-intent facts. Showing a strategy the capital its own
  intent will later reserve invites it to pre-size, duplicating the allocation
  engine's authority (ADR-0015). The contribution ledger is read for attribution
  only.
- **No depth hook on `StrategyProtocol`.** `BookUpdated` and `SnapshotCreated`
  reach no hook. Delivering an `OrderBookSnapshot` to `on_quote` would hand
  existing strategies a payload with no `quote`, and the canonical record path
  cannot produce either event in any case. A stated boundary, pinned by the
  routing test (ADR-0032).
- **No per-environment promotion policy.** A strategy version has one stage
  across all environments and `PRODUCTION` means "live somewhere". A policy that
  differs between `paper` and `live-eu` is not expressible, and making it so
  would put a second source of truth beside the deployment ledger. v3.5's
  `StrategyLifecycleStage` does not change this: it is finer-grained on the
  *maturity* axis and still one stage per strategy version, naming no
  environment at all.
- **No remediation in health or reconciliation.** Both detect and report.
  Nothing cancels an order, reconnects an adapter, pauses a progression or edits
  either side of a reconciliation, and neither side is declared authoritative —
  re-sending an order that actually exists would duplicate it, so that decision
  stays with the caller.
- **No default tolerance anywhere.** A metric with none stated is reported
  not-comparable rather than compared for equality. A hidden `== 0` calls a
  one-cent difference a break and a hidden `0.01` says a cent is fine for a
  position count; both are policies nobody chose.
- **No exposure computed by the comparison layer.** What a book means by
  exposure depends on whether it holds shares or contracts, so it is supplied by
  the caller from whichever authority their book calls for. A third exposure
  site would be the one that forgot the multiplier.
- **Durable state for the v3.5 values — since v3.12.** The reason this was a
  boundary was that a new field would have made every earlier payload
  unreadable; v3.10's schema upgrades removed that reason, and v3.12's
  evidence store files manifests, fingerprints and reports under their own
  identities (BDY-008, OFE-016). `LifecycleState` itself is unchanged.
- **No `SettlementPolicy` object.** `STRICT_MATCH` is the only settlement rule
  because no alternative exists: a permissive mode could only book honestly —
  making the book mixed, which the next valuation refuses without rates — or
  convert, which is FX and is a deliberate act with a recorded rate (ADR-0028,
  ADR-0035).
- **No triangulation, no implicit inversion, no default rate.** A configured rate
  is an invented one. `with_inverses()` will mint the opposite direction, and
  marks what it mints as `derived` (ADR-0020, ADR-0035).
- ~~**No migration framework.**~~ **Replaced in v3.10** (BDY-007, PER-001): every
  snapshot subsystem declares its schema history and upgrades an older payload
  through explicit, pure steps that refuse rather than invent (ADR-0045).
- **No authentication, IAM or federation.** Out of scope per ADR-0018. Until
  v3.11 the library held venue credentials — an `api_key` and signing secret in
  `broker.transport.VenueCredentials` for the HMAC-signed venue transport — and
  `alphalab.enterprise` kept secret *references* with rotation metadata. Both
  are application concerns and left the library in v3.11 (BRK-007, BND-002;
  ADR-0046); it now holds no credential of any kind.
- **No supervised live *process*.** Restart policy, alerting and scheduling are
  an operator's concern. `live_health` answers "should a human look at this?"
  for a run this process is driving, and v3.5's `evaluate_health` answers the
  same question in structured form from observations somebody supplied; acting
  on either answer is still the caller's.
- **No CLI, no server, no daemon, no event bus.** AlphaLab is a library with no
  composition root and no declared entry point, and a test asserts it.
- **No way to fill a missing price.** `MissingValuePolicy` has `REFUSE` and
  `DROP_ROW` and deliberately no `FILL`. Forward-fill, interpolation and
  last-known-value each invent a print that never happened, and the invention is
  invisible by the time it reaches an equity curve. The absence is structural
  rather than a member that raises, so the option is not discoverable and then
  refused (ADR-0036 decision 3).
- **No repair of an impossible bar.** A bar with `high < low` is a row whose
  meaning is unknown, not one with a small error in it; clamping it produces a
  plausible bar the source never reported.
- **No holiday data, and no corporate-action feed.** `MarketCalendar` and
  `apply_adjustments` are the mechanism and the arithmetic; the data is an
  application's to supply. An exchange's holiday list changes annually and
  differs between segments of one venue, so a list baked in here would be wrong
  within a year while looking authoritative — the same position v2.11 took on
  taxonomies and v2.17 on FX rates.
- **No curve bootstrapper.** `YieldCurve` holds *observed* yields and v3.4
  deliberately did not make it a discount curve. Bootstrapping requires choosing
  an interpolation scheme over an incomplete set of quotes, and the choice
  changes every forward rate read off the result — which is the researcher's
  decision, not a library's. `discount_factor_at` turns an observed yield into a
  factor under a **named** compounding convention and stops there (ADR-0039
  decision 11).
- **No fixed-income engine.** `alphalab.macro.bond` covers what a fixed-rate
  bond with known coupon dates admits exactly. Credit spreads and default,
  embedded calls and puts, floating and inflation-linked coupons, and the
  30E/360 and ACT/ACT ISDA day-count variants are each absent because each needs
  a model or an end-of-month rule whose correct form depends on the instrument's
  own terms. The module, the example and this line all say *foundation*.
- **No volatility-surface fit; between expiries, only by name — since v3.13.**
  A `VolatilitySurface` interpolates along strikes at a matching expiry. Between
  two quoted expiries it interpolates only when asked to by name
  (`ExpiryInterpolation.TOTAL_VARIANCE_LINEAR`, FEA-005): variance accumulates
  with time, so total variance is the quantity that interpolates, and an expiry
  outside the quoted range or total variance that falls with expiry is refused
  rather than extrapolated or smoothed. An SVI or SABR fit stays out: a model
  with parameters somebody has to choose and defend (BDY-015).
- ~~**No American option pricing.**~~ **Implemented in v3.13** (NUM-006,
  BDY-016): a Cox–Ross–Rubinstein lattice prices early exercise and discrete
  cash dividends, its step count part of the model's stated assumptions, and an
  implied volatility can be inverted through the lattice an American quote was
  priced on. The Black–Scholes–Merton closed form stays European, and says so in
  `ModelAssumptions.prices_early_exercise`.
- **No inferred roll rule.** Volume, open interest and days-to-expiry each give
  a defensible answer and they disagree. A `RollPolicy` has no default, and a
  trigger refuses the input it needs rather than approximating it from another —
  approximating is a different rule reported under this one's name.
- **No exchange registry, tick table, lot schedule or venue list.** The same
  position `MarketCalendar` takes on holidays, for the same reason: an exchange
  revises them, they differ between segments of one venue, and a table baked in
  here would be wrong within a year while looking authoritative.
- **No depth ingestion from a flat file; trade prints only when declared.** A
  `price`/`size` pair is indistinguishable from a partially populated bar
  without a declaration, so since v3.12 a print is read only from the columns a
  caller names (`TradeColumns`, FEA-004) and never detected from a header; a
  depth book is not a flat table, and stays out (BDY-018).
- **No marketplace logic.** AlphaLab provides fingerprints, manifests,
  certification reports and portability reports; listing, publishing, purchase,
  payment, subscription, ranking, search, seller and buyer accounts, licensing
  and tenancy belong to the application that consumes them. A regression test
  sweeps the v3.6 modules for any of it, and none of them names a consumer.
- **No overall certification score.** A blended figure would decide how many
  failed properties one passing one outweighs — a policy presented as a
  measurement. The report states eight properties and stops.
- **No verdict accepted as evidence.** Certification takes runs, manifests,
  observations and measurements and derives every judgement itself; it has no
  field for a health report, an assessment or a status.
- **No dependency resolver and no environment snapshot.** A dependency closure
  is declared, with its completeness stated. Listing "whatever is installed
  here" and calling it exact is the false reproducibility claim the manifest
  exists to prevent. v3.13's `read_lock_file` reads a lock file's text into that
  declaration and resolves nothing: it opens no file, reads no environment, and
  states a closure exact only when the lock is one (OFE-019, BDY-020).
- **No adaptation for portability.** A strategy that needs a capability an
  environment lacks is not portable there; nothing converts an order type,
  drops a short or retunes a parameter to make it fit.
- **No v3.6 value on `LifecycleState`**, for the reason v3.5 gave: a field there
  would move its schema. A fingerprint, manifest or report is a value the caller
  holds, and since v3.12 the evidence store files each under its own identity
  (OFE-016, the bullet on v3.5 values above); a registered version still stores
  no fingerprint.
- **No default availability.** A record whose source does not say when it was
  knowable is `UNKNOWN` and never read — not assumed available when observed,
  which is how period-end-stamped data looks ahead. There is likewise no
  default calendar, staleness bound or vintage policy (v3.7).
- **No hindsight vintage policy.** Reading today's restatement at a past
  instant is not offered; `restatements` measures hindsight under its own name.
- **No forward fill of a price, and no knowledge frame joined to prices by a
  loosened guard.** `align_prices` is a checked join (v3.7).
- **No regime taxonomy, and no p-value in an event study.** Labels are the
  caller's words; events cluster in calendar time, so a test assuming
  independence overstates significance by an unmeasured amount.
- **No vendor data feed of any kind** for events, alternative data or
  fundamentals, and no LLM, AI-service, Quant-Mind, OpenBB or RedDesk
  integration.
- **No silent scaling of capital.** An account asked for more than it holds
  refuses the whole plan unless `PRO_RATA` is stated, and then the scale is
  recorded; a capital limit is never met by scaling (v3.8).
- **No unconstrained answer behind an infeasible one.** A construction whose
  constraints cannot all hold is `INFEASIBLE`, names the conflict and carries no
  weights; nothing is relaxed and no fallback objective is tried (v3.8).
- **No default risk aversion, `τ`, view confidence, market portfolio,
  tolerance or shrinkage intensity.** Each is a choice with no neutral value
  (v3.8).
- **No enforcement in a risk budget.** A breach is reported with the bucket and
  the limit named; nothing rebalances (v3.8).
- **No broker adapter in capital allocation.** A broker and an account are
  identifiers an application maps to its own adapters and credentials, none of
  which reach AlphaLab (v3.8).
- **No capability discovery, and no silence read as support.** A venue's
  capabilities are declared by the application that has the adapter; an answer
  nobody gave is `UNDECLARED`, and a check that meets one is `UNDETERMINED` —
  never compatible (v3.9).
- **No vendor inside the execution contract.** Adapters translate their venue's
  messages into normalized events; AlphaLab names no venue, holds no credential,
  opens no connection and defines no vendor message structure (v3.9).
- **No venue event in the broker's event log.** Normalized events are judged and
  applied to the mirror; `BrokerState.events` keeps its closed set of types and
  the broker snapshot schema is unchanged (v3.9).
- **No retry of a venue's refusal.** A rejected child fails its algorithm run;
  what to do next is the caller's decision (v3.9).
- **No conversion inside a routing decision.** A venue quoting in another
  currency is ineligible, not converted; routing compares like with like
  (v3.9).
- **No default urgency, quote age or tolerance, and no best-execution claim.**
  Each is a choice with no neutral value; whether an order may be routed away
  from a venue at all is not something AlphaLab knows (v3.9). v3.13 keeps it:
  `estimate_urgency` derives an urgency from risk aversion, volatility and
  impact the caller states, none of them defaulted; iceberg randomization is
  off unless asked for; and the optimal split claims the lowest cost under the
  caller's quotes and cost models, not best execution (BDY-026).

---

---

# External dependencies

Real, and not AlphaLab's engineering to complete.

- **Verification against a commercial venue.** The venue transport and the
  WebSocket client are written to protocol and exercised end to end over real
  sockets against local servers that verify signatures, timestamp windows,
  idempotency keys and accept tokens. They have never been pointed at a
  commercial venue: this environment has no network egress and holds no vendor
  credentials. Both transports say so in their own docstrings.
- **Named vendor request shapes.** Pointing a transport at a named venue needs
  that venue's request shapes, which differ per venue and belong to an adapter.
- **FX data.** v2.17 adds the rate-feed *boundary* — ordering, deduplication,
  conflict refusal, provenance and staleness — and **not a single rate**.
- **Market calendars and corporate actions.** v3.1 adds the calendar
  abstraction and the split/dividend arithmetic, and **not one holiday and not
  one action**. Both are vendor- or exchange-supplied and change on their own
  schedule.
- **Market conventions.** v3.4 adds the convention *contract* — the tick
  schedule, the lot specification, the settlement rule, the multiplier — and
  **not one venue's values**. An exchange publishes them and revises them.
- **Deposit and discount curves.** `covered_forward_rate` requires both deposit
  rates and the day-count basis as arguments; AlphaLab holds no curve and
  bootstraps none. A computed parity forward is arbitrage-free and is not a
  price anyone traded.
- **Futures margin figures.** A clearing house sets initial and maintenance
  margin per contract and revises them without notice. `ContractMarginSpec`
  carries what was published, and `position_margin` refuses a specification
  dated after the research instant.
- **Crypto venue specifications.** Funding interval, fee tier, price source,
  minimum notional and settlement asset differ per venue and per contract.
  `VenueSpecification` declares them; AlphaLab holds no exchange adapter, no API
  client and no credential.
- **A reader for any binary columnar format.** Parquet is expressible in
  provenance through `RawSource.media_type`; reading it needs a third-party
  dependency, and AlphaLab has none.
- **Classification data.** v2.11 supplies the security master's *mechanism* and
  v2.15 its provenance. AlphaLab ships no taxonomy and no reference-data feed, so
  a sector breakdown requires an operator who declares one. Sector is also the
  registry's only dimension: industry, country, issuer and rating are each a
  separate decision with their own consumers. v3.8's risk budgets and
  construction read any classification a caller supplies with a named source —
  a country file, say — and AlphaLab holds none.
- **What a rerun needs.** A reproducibility manifest identifies the dataset
  bytes, the strategy code, the dependency set and the engine; AlphaLab stores
  none of them. The live objects a run records -- by type and, since v4.0, by
  configuration (PER-008) -- are supplied back by the caller, and a restore
  refuses one configured otherwise.
- **What an environment offers.** Every `TargetEnvironment` is an application's
  declaration of a broker's, a market's and a runtime's capabilities; AlphaLab
  discovers none of them.
- **Runtime observations and resource measurements.** Supplied by whoever
  watched the deployment or timed the run. AlphaLab observes and measures
  nothing on its own.
- **Events, alternative data and fundamentals.** v3.7 adds the point-in-time
  contract and **not one record**: the rows, the bytes they were read from, a
  vendor's line-item names and the price a valuation divides by are the
  caller's.
- **Portfolio and risk inputs.** v3.8 estimates a sample covariance and nothing
  else. Expected-return forecasts, market values for an equilibrium prior, views
  and their confidence, factor data, FX rates, account balances and the mapping
  from a broker or account identifier to an adapter and its credentials are the
  caller's.
- **Venues.** v3.9 adds the execution *contract* and **not one venue**: the
  capability declaration for a real venue, its quotes, volume profiles, printed
  volume and benchmark prices, and the adapter that translates its messages into
  `VenueEvent`s are the application's.

---

---

# Known limitations

Built, with a stated limit. Each has a ledger entry that gives its reason.

- **Per-order memory** (PRF-011): the OMS order book, execution reports by
  order, and a live session's routed and settled orders hold an entry for every
  order a run places -- exactly-once handling of a venue's late or repeated
  report needs the order it names. Retention bounds the logs, not these;
  checkpoints carry only what changed.
- **One-asset path cost** (PRF-006, PRF-012): against v3.9 on one machine (five
  interleaved rounds, measured for v3.13.0) the portfolio-engine micro-benchmark
  runs at 1.61x v3.9's time (exact per-currency totals and instrument economics
  on every fill), a one-asset backtest 1.14x, a replay 1.15x, the pipeline 1.07x
  and the OMS 0.81x -- inside PRF-006's budget. The per-record checks of the
  capabilities the one canonical path carries are paid whether or not a run
  configures them; a second path per configuration is what the architecture
  refuses. Measured again for v4.0.0 (five interleaved rounds of 3.9.0, 3.13.0
  and 4.0 on one machine): portfolio engine 1.33x, backtest 1.10x, replay 1.07x,
  pipeline 1.10x and OMS 0.70x of 3.9.0, inside the budget in every round, and
  every median within 0.99x-1.00x of 3.13.0 (`docs/audit/V4_RELEASE_AUDIT.md`).
- **The measured operating envelope** (LIM-006): 10,000 assets, 1,000
  strategies, 100 venues, 20,000-record checkpoint chains and construction at
  10,000 assets are measured (`docs/audit/V4_RELEASE_AUDIT.md`); beyond them is
  extrapolation. A run is single-threaded by design; parallelism is one run per
  process, which the host owns.
- **A strategy's configuration is persisted as JSON reads it** (LIM-004): a
  `Decimal` in `configure`'s value reads back as a string. Nothing on the
  execution path reads it; durable strategy state goes through
  `StrategyStateProtocol`, which round-trips exactly.
- **Certified identities are per host class** (LIM-005): an analytics float
  computed through another `libm` can differ in its last bit, and so can an
  identity over it. The certificate records the host, and `--check` on another
  host names every check whose evidence moved.
- **Time** (DAT-007, DAT-008): local-time computation depends on the host's IANA
  time-zone database, and instants are float Unix seconds (about 0.24
  microseconds of resolution today).
- **Persisted enum names** (PER-004): a plain enum persists by its class and
  member names, which are therefore part of the format, pinned by a test.
- **Tested on CPython 3.12 only** (LIM-007): `requires-python` is `>=3.12`, and
  neither CI nor the 4.0.0 verification has run another interpreter. *Target
  v4.1; done when* CI's matrix runs CPython 3.13 and 3.14, every release gate
  (`ruff`, `mypy .`, `pytest -W error`, every example, the certificate's
  `--check`) passes on each, and their classifiers are added.
- **The release ADRs' limitations** (LIM-001, LIM-002, LIM-003): each re-read
  against the code and kept with its reason -- among them maximum diversification
  without a turnover limit, risk parity's exact long-only budgets, and a VWAP's
  profile resolution.

---

# Future work

Accepted for a later release, each with what it is not yet, what happens in the
meantime and what closes it. `tests/regression/test_nothing_is_left_for_later.py`
holds this list to the ledger: an entry may be left for later only here.

## FUT-001 — a resumable replay (target v4.1)

- **Limitation.** `ReplayBacktest` cannot be snapshotted and resumed: the replay
  cursor (`ReplayState`) and its second identifier stream have no snapshot
  (deferred by ADR-0029 and ADR-0030).
- **Interim behaviour.** A replay is a pure function of its dataset and seed, so
  an interrupted one restarts and produces the same result. A `BacktestEngine`
  run over the same dataset produces the same orders, fills and P&L
  (`tests/integration/test_backtest_replay_parity.py`) and *is* resumable through
  the run snapshot, byte for byte (`examples/70_build_a_strategy.py`).
- **Acceptance criteria.** A `ReplayState` snapshot and the cursor stream's
  `IdStreamPosition` captured with the run; `ReplayBacktest.resume`; a
  cross-process test that an interrupted replay, continued, equals the
  uninterrupted one byte for byte; the ledger entry closed.
- **Dependencies.** None outside `alphalab.replay` and
  `alphalab.backtesting.replay`. Additive: no existing schema moves.

---

# Planned before v4 — the former optional list, re-classified (historical)

Kept as the record of how the pre-v4 audit (v3.10) re-classified every item this
document had listed as optional future evolution through v3.9. Every planned item
was delivered by v3.13.0; the boundaries and the external item stand above.

**Planned** — each an item in `docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`:

| Item | Ledger | Release |
| --- | --- | --- |
| Richer portfolio construction: estimated shrinkage, EWMA and factor-model covariance, costs in the objective, rounding to lots (cardinality and joint lot selection are integer programs: an explicit boundary, ADR-0046) | OFE-002 | v3.11 — delivered |
| Neutralization against several continuous exposures at once, by a rank-revealing solve | OFE-004 | v3.11 — delivered |
| A deflated Sharpe ratio; Holm and false-discovery-rate corrections beside Bonferroni | OFE-005 | v3.11 — delivered |
| A t-statistic on an information coefficient, corrected for overlapping windows (Newey–West) | OFE-006 | v3.11 — delivered |
| Pipeline-driven `on_fill` / `on_order` / `on_timer` | OFE-014 | v3.11 — delivered |
| A venue sequence number; persisted child bindings and request ledger | OFE-021, OFE-022 | v3.11 — delivered |
| Classification dimensions beyond sector; sector-based pre-trade limits | OFE-001 | v3.12 — delivered |
| Per-strategy capital ceilings on the execution path | OFE-003 | v3.12 — delivered |
| Execution-path delivery of external information | OFE-009 | v3.12 — delivered |
| A streaming observation set; split-adjusted and currency-converted fundamentals | OFE-011 | v3.12 — delivered |
| The optimizer's super-linear `pending_trials`, removed with the research consolidation | OFE-013 | v3.12 — delivered: the optimizer was removed (SCF-003) |
| A durable home for progressions, fingerprints, manifests and reports | OFE-016 | v3.12 — delivered |
| Health evaluated over a window | OFE-017 | v3.12 — delivered |
| Book-to-mirror reconciliation across several brokers' accounts | OFE-023 | v3.12 — delivered |
| A lock-file reader | OFE-019 | v3.13 — delivered |
| A rerun harness | OFE-020 | v3.13 — delivered |
| An optimal split; estimated urgency and randomized iceberg tranches | OFE-024, OFE-025 | v3.13 — delivered |

As of v3.13.0 every planned item is delivered; the ledger assigns nothing to a
later release.

**Kept as deliberate boundaries**: statistical regime models, which need an
estimation step with an identity (OFE-010); a half-life fitted to a decay
profile and derived alignment for a comparison, each of which would report a
fit or a pairing as a measurement (OFE-007, OFE-018); per-strategy sub-ledgers
in `PortfolioEngine`, whose question the contribution ledger already answers
(OFE-015); process supervision, hot reload and hook timeouts, which are the
host's (OFE-026) — strategy-API versioning, the part of that item a library can
own, is held since v3.13 by the public API manifest.

**External**: a vendor adapter package (OFE-008) — a venue's request shapes
belong to the application that connects to it.

**Removed**: the two provider-vocabulary `AssetClass` enums, with the packages
that held them (OFE-012, v3.10).

The two reasons the old list gave for not building a deflated Sharpe ratio and
an information-coefficient t-statistic did not hold on re-examination: Holm's
step-down needs no assumption beyond Bonferroni's, and overlapping windows are
what a Newey–West correction is for (DOC-002).

---

---

# Versioning

**Major releases** introduce architectural milestones or breaking public API
changes. v2.0.0 unified the canonical execution domain models; **v3.0.0 freezes
the architecture and is deliberately additive** — the removals that would have
made it breaking were taken in v2.17.

**Minor releases** introduce new capabilities, and have occasionally made small,
documented breaking changes to a narrow public API where correctness required it:
v2.2.0 changed `AllocationEngine.release_reservation` to take no amount because
the reservation ledger owns it; v2.3.0 changed `alphalab.brokers`' field names so
both broker packages speak one vocabulary; v2.4.0 refused two model-registry
stage transitions that made rollback indistinguishable from promoting something
old; v2.5.0 gave a partially filled simulated order a terminal state; v2.16.0
made `governance` a required argument at every governed entry point; and v2.17.0
required the margin rates and currencies that had been silently defaulted.

**Patch releases** focus on stability, bug fixes, and performance.

**v3.1.0** made two narrow documented changes of the kind described above, both
because a v3.1 requirement contradicted a v3.0 behaviour directly:
`UniversalDataEngine.clean` now requires a `CleaningPolicy` rather than applying
an implicit one, and it — like `convert_timeframe` — derives a new dataset
version rather than replacing records in place.

**v3.4.0** made seven, all of the same class: a market convention that had been
defaulted to one market's value became required. `FutureContract.currency`,
`OptionContract.multiplier`, `OptionContract.style`, `OptionSpec.style`,
`FundingRate.interval_hours`, `CryptoInstrument.contract_size` and
`compute_funding_payment`'s `contract_size`. Each produced a number rather than
an error when it was wrong. `FxRates.convert` additionally began refusing a rate
dated after the conversion instant, which is a look-ahead it previously allowed.

**v3.8.0** made six, all in the v1 construction engine and all of the same
class: a figure produced where a refusal or the stated figure belonged.
`PortfolioAdapter.dict_to_covariance_matrix` and `dict_to_expected_returns`
refuse a missing entry rather than reading it as `0.0`; `apply_weight_constraints`
refuses a `max_sector_exposure` it cannot apply and lower bounds that force the
weights above the target, rather than ignoring the one and returning the other;
the manager's cost estimate includes `CostModel.spread_rate`, in a new last field
`TransactionCostEstimate.estimated_spread` defaulting to zero; and
`ConstraintViolated` reports the weight the constraints moved rather than the
constant `1.0`.

**v3.9.0** made five, each a state no venue can report or an input a result
cannot describe, now refused: `oms.order.Order.fill` must complete the order
exactly and `partial_fill` must leave quantity working (a `FULL_FILL` short of
the order, from `fill_status` or `StaticFill`, now raises
`InvalidTransitionError`), a fill must be positive, and `replace` must leave
something to work; a fill during a pending cancel keeps the cancel pending in
the mirror; `validate_cancel_request` refuses `EXPIRED` and `replace_order`
validates an amendment; and `broker.reconcile` refuses duplicated remote records
and a remote account in another currency.

**v3.10.0–v3.13.0**, the pre-v4 releases, made the breaking changes the pre-v4
audit required, each listed in its CHANGELOG entry's migration table. v3.13.0's
are renames and removals that give one name one contract (API-001), taken
without aliases so that v4 freezes one spelling; since v3.13 a test diffs each
release's public API with the previous release's and requires every removed or
rebound name in that release's CHANGELOG section (DOC-006).

After v3.0.0, the bar for a change rises: the invariants listed in
`nowandfuture.md` are frozen, and a change to any of them is a major release with
an ADR.

---

**v4.0.0** is the freeze of the universal engine contract. It is a major release
because it is the point from which the public API (`docs/api/PUBLIC_API.md`,
"Stability from v4.0") stops changing incompatibly until 5.0, not because it
removes anything: against v3.13.0 it adds four names and removes or rebinds none,
and it brings `alphalab.api` and the snapshot modules -- the surfaces the guides
teach -- under the freeze (API-007).
It does change behaviour where v3.13 was wrong, each listed in its CHANGELOG
entry's migration table: a restore refuses a live object configured otherwise
than the captured run's (PER-008, schemas pipeline 7 -> 8 and run 4 -> 5, every
older payload upgraded), an ingestion under a refusing cleaning policy refuses a
row it cannot read rather than dropping it (DAT-010), option pricing refuses a
non-finite input (NUM-015), and a whole-unit run fills in whole units (EXE-011).

From v4.0: a minor release adds and corrects and does not break; a breaking
change waits for 5.0 with an ADR and a migration; a persisted format moves only
by a schema step that reads every older payload.

---

# Feature notes (delivered)

Scope notes for each delivered PR, kept as the historical record of what each
engine was built to do when it shipped. Where a package changed since, its
CHANGELOG entries say how.

## PR-034 — Feature Store

Institutional feature engineering framework: feature registry, versioning,
metadata, validation, caching.

## PR-035 — Factor Library

Reusable quantitative factors: momentum, value, quality, carry, volatility,
liquidity.

## PR-036 — Options Engine

Option chains, Greeks, volatility surfaces, pricing models, strategy simulation.

## PR-037 — Futures Engine

Continuous contracts, rolls, curve analysis, calendar spreads.

## PR-038 — Crypto Engine

Spot, futures, perpetuals, funding rates, exchange normalization.

## PR-039 — Macro Engine

Economic indicators, central bank events, yield curves, inflation, GDP.

## PR-040 — Alternative Data

News, sentiment, satellite, shipping, ESG, credit cards. Since v3.7 also the
point-in-time foundation for any category of external information — events,
observations and fundamentals with source identity and versioned sets (ADR-0042).

## PR-041 — Machine Learning

Feature pipelines, training, cross validation, prediction, evaluation.

## PR-042 — Deep Learning

LSTM, transformers, CNN, sequence models.

## PR-043 — Reinforcement Learning

Trading environments, policy optimization, agent evaluation.

## PR-044 — Cloud Research

Distributed quantitative research: remote execution, worker pools, cluster
management.

## PR-045 — Cluster Scheduler

Job scheduling, queue management, distributed orchestration.

## PR-046 — Experiment Tracking

Experiment history, metrics, parameters, versioning.

## PR-047 — Model Registry

Versioning, promotion, rollback, deployment metadata.

## PR-048 — AI Research Assistant

Deterministic, offline grid-search research driver: candidate generation,
caller-supplied evaluation, best-first ranking, Markdown reporting, and the
bridge that lifts a chosen candidate into a canonical `StrategyDefinition`. No
LLM and no network.

## PR-049 — Deployment Manager

Packaging, release management, rollbacks, production deployment.

## PR-050 — AlphaLab Enterprise

Principals and sessions (no credentials accepted or stored), RBAC, append-only
audit log, multi-user workspaces, secret *references* and rotation metadata, and
a compliance snapshot. Removed from the library in v3.11 (BND-002): identity and
tenancy are the host application's, and the lifecycle takes a
`PermissionAuthority` the host supplies.

---

---

# Long-Term Vision

AlphaLab set out to be a complete quantitative research platform -- market data,
feature engineering, research, portfolio construction, machine learning,
execution and reproducibility -- while preserving determinism, immutability,
modular architecture and production readiness. At v4.0 that engine is complete
and frozen: one execution spine with one run owner and four drivers, a lifecycle
path joined to it, durable state that round-trips across processes, and a public
API held stable from here.

What remains is what this document lists: boundaries that should stay, external
dependencies that are somebody else's to supply, limitations stated with their
reasons, and the future work above.

---

# Community

Contributions are welcome; see `CONTRIBUTING.md`.

A contribution that changes an ownership boundary, a schema contract, a
documented invariant or the public API incompatibly needs an ADR and a major
release (`docs/api/PUBLIC_API.md`). A new standalone engine, a strategy, an
example, a benchmark, a test or a documentation fix follows the ordinary
workflow. A vendor adapter belongs to the application that connects to the
venue, not to this library (BND-001, ADR-0046): AlphaLab defines the contract
it implements.
