# ADR-0035: Settlement Currency, the FX Feed, and the Strategy Registry

## Status

**Accepted and implemented in v2.17.0.** ADR-0033's consequences section ended
with a list of exactly three things left open, and named them precisely:

> **What is deliberately still open**, stated so it is not discovered later:
> settlement-level multi-currency trading, with the four blockers in decision
> 13; a strategy-class registry for the lifecycle join; and a rate feed.

This ADR closes all three. Each lands on an authority that already existed, and
each is stated below with the constraint from a previous ADR that shaped it —
because in all three cases the earlier refusal was right at the time and the
thing that changed is a blocker, not a principle.

Depends on **ADR-0019**, **ADR-0020** and **ADR-0028** for the currency rules,
**ADR-0033** for the three gaps and the four blockers, **ADR-0030** for the state
budgets it is not allowed to move, and **ADR-0016** for the layering the registry
had to respect.

---

# Context

v2.16 closed the **valuation** gap: a book holding two currencies values as one
figure, and the figure says which rates produced it. It could not close the
**settlement** gap, and ADR-0033 decision 13 said exactly why:

> `PortfolioState.realized_pnl` and `commission_paid` are single cumulative
> scalars that name no currency, and a run trading in two would sum them across
> both. Allocation sizes against a capital budget in one currency and risk
> limits are stated in one.

Four blockers. Two are fields, two are configuration. None of them is an FX
problem — each is a place where a number failed to say what currency it was in,
which is only harmless while there is exactly one.

---

# Decision

## 1. Settlement truth and reporting truth are different numbers

This is the property the whole design turns on, and every other decision here
follows from it.

**Settlement truth** is what actually happened, in the currency it happened in.
A EUR fill accrues EUR realized P&L on the state, permanently.
`PortfolioState.realized_pnl` and `commission_paid` are
`CurrencyAmounts` — a mapping from currency to an exact amount — so no addition
is ever performed across two.

**Reporting truth** is one figure in one currency, produced on demand by a
valuation, with every conversion it performed recorded on the snapshot.

The smaller change was available and is wrong: translating each fill's P&L into
the reporting currency *at fill time* would have left both fields scalars. It
destroys the only record of what was actually earned, and it bakes one instant's
rate into a cumulative figure that is then wrong at every later instant. A book
can be valued in USD today and EUR tomorrow; what it *realized* does not change
when you change the question.

`CurrencyAmounts` is deliberately not `CashLedger`, which it resembles. Cash has
reservations and refuses an overdraft; an accounting result has neither and is
freely negative, because a losing trade is not an error.

## 2. A pipeline settles the currencies it was told to, and refuses the rest

`ExecutionPipelineConfig.also_settles` is a `frozenset[str]`, **empty by
default**. An empty one is the single-currency pipeline every run had before
v2.17, byte-identical to it: one permitted currency, the same two ADR-0028 seams,
the same fast path, no rate ever read.

Naming a currency there does three things and no more. Seam 1 stops dropping a
request for an instrument that trades in it; seam 2 stops raising on a venue
report denominated in it; and the resulting book becomes genuinely mixed — so
every valuation of it then needs an `FxRates` covering the pair and refuses
without one. It is **not** a licence to convert: it widens what may be settled,
and settlement stays native.

Both seams change from `== config.currency` to `in config.settlement_currencies`.
That is the whole of the boundary change, and it keeps ADR-0028's asymmetry
intact: seam 1 drops a request the pipeline has not yet created, seam 2 raises on
a fill that already happened and cannot be declined.

## 3. An instruction is denominated in what the instrument trades in

`_instruction` stamped `config.currency` onto every `OrderInstruction`, and that
was correct while seam 1 dropped anything that disagreed — the stamp was always
right because nothing else could reach it.

Now that a foreign instrument *can* reach it, stamping the pipeline's currency
would book a EUR trade as USD at the EUR price: a silent relabelling, which is
precisely the v2.11 defect ADR-0028 exists to prevent.
`_settlement_currency_for` returns the instrument's own currency when the
registry names one this pipeline settles, and the reporting currency otherwise.
A single-currency pipeline's `settlement_currencies` holds one member, so the
only string it can return is the one it always returned.

## 4. Settlement conversion is an act, never a consequence

A fill in EUR debits the EUR cash balance. A run that settles EUR without EUR
cash is refused by `InsufficientFundsError`, naming the currency — and **that
refusal is the capability, not a gap in it**. An account cannot spend money it
does not hold, and a fill that quietly financed itself at a rate nobody asked for
would be the "invented figure that looks authoritative" ADR-0020 refuses.

`PortfolioEngine.convert_cash` is how the money gets there: a deliberate call
with a supplied rate, recording both currencies, both amounts, the rate, its
`as_of` and its source on a `CashConverted` event. `ExecutionPipeline.fund` and
`ExecutionPipeline.convert_cash` wrap it and resync risk.

Funding is deliberately **not** a configuration field. How much of each currency
an account holds, and when, changes during a run; a `Mapping[str, Decimal]` on
`ExecutionPipelineConfig` would freeze one answer for the whole of it.

## 5. The budget names its currency when, and only when, that is ambiguous

Blocker 3. `CapitalBudget.currency` defaults to `""`, and `""` means
**unstated**, not `"USD"` — the distinction ADR-0033 decision 8 draws for
`actor_id`.

A single-currency pipeline accepts an unstated budget, because there is exactly
one currency in play and the budget is in it by determination rather than
assumption; requiring the string there would break every existing caller to state
something already known. A **multi-currency** pipeline refuses one, because
sizing compares a notional against a capital figure and with two currencies that
comparison is meaningless unless the budget says which. Either way a budget
naming a currency the pipeline does not settle is refused, which catches a
transposed configuration that a default would have accepted.

## 6. Allocation converts nothing, and never learns what a rate is

`AllocationEngine.allocate` takes a second price map — the same assets priced in
the budget's currency — and does arithmetic. It has no `FxRates`, no currency
strings and no conversion.

**That is a measured decision, not a stylistic one.** Threading an `FxRates`
into `alphalab.allocation` was implemented first, and it pulled **all eighteen
`alphalab.portfolio` modules** into a package that previously imported none of
them: importing `alphalab.portfolio.fx` executes `alphalab/portfolio/__init__.py`,
which imports the accounting engine. Two domain engines that had been
independent would have stopped being so, to move four lines of arithmetic.

The conversion happens in `_budget_prices`, at the pipeline, where the registry
that names an instrument's currency and the rates that price it both already
are. A single-currency pipeline returns an empty map without reading a price, so
the per-event allocation path is unchanged.

The asymmetry is deliberate and worth stating: `OrderRequest.price` stays in the
instrument's **own** currency, because that is what the venue executes at and
what the fill will be denominated in. Only the budget *comparison* moves into
one currency, because a budget is one number.

## 7. Risk reads the whole book, or refuses it

Blocker 4. `_sync_risk_from_portfolio` read cash with
`cash.balance(base_currency)`, which on a mixed book **silently dropped every
other balance** — so a run holding most of its capital abroad would have reported
almost no buying power and refused every order, with nothing saying why. It now
reads `cash_in`, which includes every balance and converts what is not already in
the base currency. `_risk_exposure` converts each foreign position rather than
refusing outright, through the same one rule. A book no rate covers still
refuses. A homogeneous book takes the identical path and touches no rate, which
is what keeps the per-event resync at the cost ADR-0028 decision 7 measured.

## 8. The rate feed is a boundary, not data

ADR-0033: "**No rate feed.** AlphaLab ships no FX data, exactly as it ships no
classification data." **That sentence is still true.** Every rate in
`alphalab.portfolio.fx_feed` is supplied by a caller, carries a source and an
`as_of`, and there is still no default, no fallback of `1.0`, no triangulation
and nothing AlphaLab computes for itself.

What was missing was the *boundary*. Without one, every caller folded quotes into
an `FxRates` itself and had to decide, alone and usually implicitly, three
questions that have one right answer each:

| A quote that is… | …is |
| --- | --- |
| newer than what is held for its pair | `APPLIED` |
| byte-identical to what is held | `DUPLICATE` — a venue redelivers after a reconnect |
| **older** than what is held | `SUPERSEDED`, and **not applied** |

The third is the one that matters. A feed that accepted it would move the book's
view of the market backwards because two packets arrived out of order, and a
valuation taken between the two would be wrong in a way nothing could detect
afterwards. `OrderingGuarantee` exists on `MarketDataSource` because a live venue
can reorder; this rule is what makes reordering *safe* rather than merely
declared.

A fourth case is **refused rather than decided**: the same pair and the same
`as_of` carrying a different rate or a different source. `FxRates.of` already
refuses that shape — "which one is right is not a question this table will answer
by picking" — and a feed that let the later packet win would be picking while
looking authoritative.

`FxRateSource` takes `MarketDataSource`'s shape deliberately: identity,
provenance, and nothing about order. No provider API is modelled, for the reason
that one says — inventing a vendor's API shape is guessing at an interface
AlphaLab cannot test. `SequenceFxSource` is the deterministic source, the
analogue of `SequenceSource`.

**Staleness stays where it was.** A feed does not decide whether a rate is too
old to *use*; that is `FxRates.max_age_seconds`, checked at conversion against
the instant the conversion is for. What a feed knows is different and also worth
having: `FxFeedState.silent_for` answers *has this feed gone quiet?*, which is a
liveness question about the **connection** that a stale-rate refusal cannot
distinguish from a source simply not quoting a pair.

## 9. The strategy registry is not in the lifecycle, and that has not changed

ADR-0033 decision 5 refused to put one there:

> Mapping that to a class is the caller's knowledge, and a registry of strategy
> classes **here** would be a plugin system this package has no business owning.

Still true. `alphalab.lifecycle` constructs nothing, names no runtime type, and
`test_the_join_builds_no_state_and_starts_nothing` still enforces it.

The registry is in `alphalab.strategy`, beside `StrategyProtocol` — the thing
being registered — in the package that owns what a strategy *is* and imports
nothing above itself. Every layer that needs the mapping can reach it and none of
them owns it.

**It is not a second `StrategyDefinition`.** It stores a `strategy_id`, a factory
and a qualified name for provenance, and nothing about what a strategy is. The
identity it keys on is `StrategyDefinition.strategy_id`, which already existed.

**It never resolves a name to code.** No `importlib`, no class-name derivation. A
name is not a type — `alphalab.strategy.dispatcher` carries the scar of that
assumption (ADR-0032 decision 1) — so a caller registers the class it already
has, and "which code is this?" is answerable by reading the registration site.

**It refuses twice.** A duplicate registration is a `DuplicateStrategyError`
naming both the incumbent and the challenger, because silently taking the second
would make which code runs depend on import order. An unknown identity is an
`UnknownStrategyError` listing what *is* registered, because a `None` a caller
forgets to check reaches the runtime as a missing strategy and fails somewhere
that cannot say registration was the problem. A factory returning something that
does not satisfy `StrategyProtocol` is refused at construction rather than at its
first market event — where `Dispatcher` would record the failure against the
*strategy*.

**The declaration is structural, not an import.** `StrategyDeclaration` is a
Protocol with `strategy_id` and `parameters`; `StrategyDefinition` satisfies it
as it stands, so `alphalab.strategy` acquires no dependency on
`alphalab.studio`. That is ADR-0016 decision 3's reasoning applied again.

`RunPlan.definition` was typed `object` and is now typed as itself. The vagueness
was never a boundary — the lifecycle already imports `StrategyDefinition` and
already stores one on every version — and it cost the caller the only thing the
property is for.

## 10. What is durable is the identity

A factory is a live object, exactly as a strategy instance, a sizing model and an
instrument registry are, and ADR-0023's rule for all of them applies unchanged: a
snapshot records what the object *was* and a restore requires the caller to
supply it back. The registry is therefore not serializable, and
`instances_for` is what makes a restore's supply deterministic rather than
hand-assembled at every restore site.

## 11. Two schemas move, and each refuses the version below it for its own reason

`PORTFOLIO_SNAPSHOT_SCHEMA` 2 → 3, carrying the per-currency accumulations.
**A version 2 payload is refused**, not migrated: it records `realized_pnl` as a
bare number in no currency, and reading it as the account's base currency looks
like a migration and is a guess about money — a v2 run that settled one currency
while its account declared another would have its whole P&L history silently
relabelled.

`PIPELINE_SNAPSHOT_SCHEMA` 2 → 3, carrying `also_settles` and
`CapitalBudget.currency`. **Versions 1 and 2 stay readable**, and the asymmetry
with the portfolio is the point rather than an inconsistency. A v2 pipeline
payload has no `also_settles` because its writer could not have had one — a
pipeline before v2.17 settled exactly one currency, by construction — so reading
it as the empty set is what that payload *says*, not a value invented for it.

The rule is neither "old payloads are readable" nor "old payloads are refused":
**a default is allowed only when it is what the payload already meant.** A v2
pipeline payload nests a v2 portfolio payload, so restoring one still fails at
the portfolio decoder — each envelope validates its own version, which is the
churn confinement ADR-0023 decision 1 bought.

`FX_FEED_SNAPSHOT_SCHEMA` is new at 1 and has nothing to be compatible with.

## 12. No capability moved another's boundary

The property that makes these three additions rather than a rewrite, pinned by
`tests/integration/test_v217_capabilities.py::test_no_capability_moved_another_ones_boundary`:

* the registry added **no field** to `RunState` (still eight, ADR-0030 decision
  2) or `ExecutionPipelineState` (still sixteen, ADR-0030's performance budget),
  and no dependency above `alphalab.strategy`;
* FX rates are **still not run configuration and still not run state** — a quote
  is time-varying data, and `ExecutionPipelineState` is reconstructed eleven
  times per record. Rates are a per-call parameter, defaulting to the empty
  table;
* multi-currency moved **one** pipeline schema and one portfolio schema, and no
  other constant;
* the feed took **no dependency** on the execution path.

---

# Ownership

| Concept | Owner |
| --- | --- |
| What a rate is, and what a table of them converts | `portfolio.fx` — **unchanged** |
| Where rates come from, and the three rules | `portfolio.fx_feed.FxFeed` |
| Money accumulated per currency | `portfolio.amounts.CurrencyAmounts` |
| Settlement truth | `portfolio.engine.PortfolioState` — same fields, per currency |
| Reporting truth | `portfolio.valuation.PortfolioValuation` — **unchanged** |
| What a mixed book is | `portfolio.valuation.assert_single_currency_book` — **unchanged**, one implementation |
| Which currencies a run may settle | `ExecutionPipelineConfig.also_settles` |
| What a fill settles in | `_settlement_currency_for`, from the instrument registry |
| Settlement conversion of cash | `PortfolioEngine.convert_cash` |
| What currency a budget is in | `CapitalBudget.currency` |
| Identity → executable strategy | `strategy.registry.StrategyClassRegistry` |
| What a strategy *is* | `studio.strategy.StrategyDefinition` — **unchanged** |
| What should run where | the lifecycle's deployment ledger — **unchanged** |

---

# Testing invariants

* `tests/regression/test_settlement_multi_currency.py` (29) — a foreign fill
  settles in the instrument's currency; P&L and commission accrue per currency;
  nothing is summed across two; a valuation converts and records every rate; the
  accounting identity holds across two currencies; the same book values in either
  currency; a missing or stale rate refuses; both seams still refuse an unsettled
  currency; a conversion records its rate and a refused one moves no money; the
  budget and risk blockers; the schema, the round trip and the replay.
* `tests/regression/test_fx_rate_feed.py` (28) — the source boundary; the three
  rules and the one refusal; a refused quote leaves the state it was offered
  unchanged; provenance, staleness and liveness stay separate; no derivation; the
  canonical table; durability; determinism; and that arrival order does not
  change where the table ends up.
* `tests/regression/test_strategy_class_registry.py` (31) — registration and
  identity; both refusals and the undispatchable-factory refusal; construction
  and parameters; one authority and no name guessing; the runtime join; the
  lifecycle join with the lifecycle still constructing nothing; durable identity
  and deterministic restoration.
* `tests/integration/test_v217_capabilities.py` (9) — flow 1 end to end from a
  misbehaving source to a reported figure; flow 2 from a governed deployment to a
  strategy that read its context; the missing-rate refusal at the earliest point
  it can fire; no capability moved another's boundary; three refusals on their
  own terms.

---

# Explicit non-goals

* **No implicit FX anywhere.** No fill converts cash to cover itself, no
  shortfall is financed, and no rate is triangulated, inverted or defaulted.
* **No per-currency `equity`.** ADR-0020 rejected turning a valuation figure
  into a mapping and that stands; what became per-currency is the *settlement*
  record, which is a different value.
* **No rate table on run configuration or run state.** Decision 12.
* **No registry in the lifecycle.** Decision 9.
* **No class-name resolution.** Decision 9.
* **No FX in `alphalab.allocation`.** Decision 6, with the measurement.
* **No migration of a v2 portfolio payload.** Decision 11.
* **AlphaLab still ships no FX data.** Decision 8.

---

# Consequences

**What is now true.** A run can trade an instrument that settles in another
currency, book the fill in that currency, accrue its P&L and commission there,
size it against a budget that says what *it* is in, check it against risk limits
that read the whole book, and report the result as one figure in one currency
with the rates that produced it — every one of which is supplied, attributable
and refusable.

**Costs.** Two schema bumps, one of which refuses its predecessor. Three
breaking signature changes on the portfolio surface. A multi-currency run must
fund each currency it settles and supply rates covering every pair, or it is
refused. Three state fields on standalone packages.

**What is deliberately still open**, stated so it is not discovered later:
nothing from ADR-0033's list. All three are closed here.

---

# Release impact

Breaking:

| Change | Who sees it |
| --- | --- |
| `PortfolioState.realized_pnl` / `commission_paid` are `CurrencyAmounts` | anyone reading them as a `Decimal`; use `.of(currency)` or `.total_in(currency, rates)` |
| `PortfolioEngine.apply_fill` requires `currency` | anyone relying on the `"USD"` default, which now decides which P&L bucket accrues |
| `PortfolioSnapshotProtocol.realized_pnl` / `commission_paid` are mappings | any strategy reading them; `realized_pnl_in(currency)` is the keyed read |
| `PORTFOLIO_SNAPSHOT_SCHEMA` 2 → 3; a v2 payload is refused | anyone restoring a portfolio snapshot written before v2.17 |
| `PIPELINE_SNAPSHOT_SCHEMA` 2 → 3; v1 and v2 stay readable | nobody, except through the portfolio payload they nest |

Additive: `alphalab.portfolio.amounts`, `alphalab.portfolio.fx_feed`,
`alphalab.strategy.registry`; `ExecutionPipelineConfig.also_settles`,
`settlement_currencies` and `is_multi_currency`; `CapitalBudget.currency` and
`in_currency`; `PortfolioState.settlement_currencies`;
`PortfolioEngine.convert_cash`; `ExecutionPipeline.fund` and
`ExecutionPipeline.convert_cash`; `CashConverted`; `RunPlan.strategy_id`; a
`rates` parameter on `process_record`, `process_quote`, `process_market_event`,
`apply_execution_report`, `RunEngine.advance` and `apply_broker_execution`, each
defaulting to the empty table so behaviour is unchanged without one.
