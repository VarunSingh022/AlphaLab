# ADR-0019: Currency Roles and Settlement Semantics

## Status

Accepted (v2.8.0). Written to disk in v2.9.0.

This decision was taken and implemented during v2.8 and was not recorded at the
time. `alphalab.portfolio.valuation` and
`tests/regression/test_venue_concepts_stay_distinct.py` both cite "ADR-0019" in
prose, so the number was already spoken for. This file records the decision as
it was accepted and as v2.8.0 shipped it. Its substance is not reopened here.

Extends ADR-0016, which made the listing exchange one of the four fields
`asset_id` is derived from, and ADR-0012, which introduced the execution venue
on the broker boundary. ADR-0020 records the valuation consequence of the
currency half of this decision.

---

# Context

Two words in AlphaLab each name more than one thing, and in both cases the
collision was invisible because nothing checked it.

**"Currency" named three roles.** `Account.base_currency` is what risk and NAV
read. `ExecutionPipelineConfig.currency` funds the cash ledger and denominates
every `OrderInstruction` and `ExecutionReport`, and therefore every `Position`.
`InstrumentRecord.currency` is what an instrument trades in and is one of the
four fields `asset_id` is derived from. The first two describe one thing — the
account's settlement currency — and until v2.8 nothing verified that they
agreed.

When they disagreed the run did not fail; it went quiet. Cash is deposited under
`config.currency`, so `cash.balance(account.base_currency)` is zero, so
`risk.cash`, `buying_power`, `current_nav` and `peak_nav` are all zero.
`check_buying_power` then refuses every order, `check_leverage` returns early on
a non-positive NAV, and `check_margin` passes vacuously against zero available
margin. The run produces no fills, reports its full starting equity, and two
risk limits silently stop checking.

**"Venue" named three concepts.** The v2.8 archaeology proposed deriving a
canonical record's `venue` from `InstrumentRecord.exchange`, on the evidence
that a registry holding `SAP/XETR/EUR` produced quotes stamped `venue="XNAS"`.
Reading the code refuted the proposal: there are three concepts, not two, and
merging them would make `ExecutionReport.venue` read `"XNAS"` for an order that
executed against the simulator — which is false.

---

# Decision drivers

- **D1.** A silent failure is worse than a loud one. A configuration that
  disables two risk limits and produces no fills must not be accepted.
- **D2.** Do not narrow what the model already supports. `CashLedger` is keyed
  by currency and `Position` declares its own, so foreign-currency holding is
  supported and must stay supported.
- **D3.** Identity is frozen. ADR-0016 derives `asset_id` from
  `(asset_type, exchange, symbol, currency)`; nothing in this decision may
  change any of those four inputs or their meaning.
- **D4.** Refuse rather than normalize. `CashLedger` keys balances by the string
  it is given, so `"usd"` and `"USD"` are already two separate balances.
- **D5.** Name concepts by their role, not by a shared word.

---

# Decision

## 1. The account has exactly one settlement currency, and the configuration must say so once

`ExecutionPipelineConfig.currency` and `Account.base_currency` name one thing.
`ExecutionPipeline.initialize` refuses a configuration where the two differ,
before the portfolio exists, so nothing is funded against a refused
configuration.

Comparison is **exact**: no `strip`, no `upper`. Normalizing here would let the
check pass while the cash ledger still split the balance across two keys, which
is the failure the check exists to prevent.

The refusal states what would have gone wrong — that buying power and NAV would
be zero, every order refused, and the leverage and margin checks silently
stopped — rather than only reporting a mismatch.

## 2. The three currency roles are distinct and stay distinct

| Role | Field | Read by |
| --- | --- | --- |
| **Account currency** | `Account.base_currency` | risk resync, `NAVCalculator` |
| **Settlement currency** | `ExecutionPipelineConfig.currency` | cash ledger, `OrderInstruction`, `ExecutionReport`, `Position` |
| **Trading currency** | `InstrumentRecord.currency` | instrument identity (ADR-0016) |

The first two must agree and are checked. The third is a different fact about a
different object and **does not reach the execution path**.

## 3. A non-base-currency instrument may be held and booked

This decision constrains *configuration*, not instruments. `CashLedger` is
multi-currency and `Position` carries its own currency, so holding and booking
in a foreign currency is supported by the model as it stands, and this ADR does
not restrict it. What is not supported is aggregating two currencies into one
figure; that is ADR-0020.

## 4. The three venue concepts are distinct and stay distinct

| Concept | Field | Identity-bearing | Read by the execution path |
| --- | --- | --- | --- |
| **Listing exchange** | `InstrumentRecord.exchange` | **Yes** — an `asset_id` input | No |
| **Market-data attribution** | `Quote.venue`, `Tick.venue` | No | No |
| **Execution venue** | `ExecutionPipelineConfig.venue`, `RoutingConfig.venue` | No | **Yes** — reaches `ExecutionReport.venue` |

`Bar` and `OrderBookSnapshot` carry no venue field at all, and `Bar` is the only
record type `ProviderHistorySource` produces — so the one production path from a
provider to a fill carries no venue whatsoever. `NormalizationPolicy` supplies
market-data attribution because no wire record carries it, and it reads the
policy, never the instrument.

Unifying any two of these is refused. The repository's own tests already use
`venue` for a MIC (`"XNAS"`), for a trading venue (`"BINANCE"`), and
`alphalab.feed.normalization` sets it to the provider's name.

## 5. Honest aggregation requires an FX authority, and none is fabricated

Where a single figure would require converting between currencies, AlphaLab
refuses rather than inventing a rate. See ADR-0020, which records that decision
and its boundary.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Account currency | `alphalab.portfolio.account.Account` |
| Settlement currency | `alphalab.runtime.execution_pipeline.ExecutionPipelineConfig` |
| Agreement of the two | `ExecutionPipeline.initialize`, via `_require_one_account_currency` |
| Trading currency | `alphalab.instrument.record.InstrumentRecord` |
| Listing exchange | `alphalab.instrument.record.InstrumentRecord` — identity input |
| Market-data attribution | `alphalab.market.normalization.NormalizationPolicy` |
| Execution venue | `ExecutionPipelineConfig.venue`, `RoutingConfig.venue` |

---

# Data model changes

```text
ExecutionPipelineConfig     # UNCHANGED shape; currency's role documented
Account                     # UNCHANGED
InstrumentRecord            # UNCHANGED
Quote / Tick / Bar          # UNCHANGED

+ RuntimeValidationError raised by ExecutionPipeline.initialize
  when config.currency != config.account.base_currency
```

No field is added, removed or retyped. The decision is a validation and a set of
named roles.

---

# Boundary behavior

The check runs in `ExecutionPipeline.initialize`, which is the single
construction point every environment passes through — `BacktestEngine.initialize`
and `TradingSession.initialize` both delegate to it. A backtest, a replay, a
paper run and a live session are therefore refused at the same boundary, in the
same way, with the same message.

---

# Persistence semantics

None. No persisted payload changes, and no schema version moves.

---

# Testing invariants

1. A configuration naming two account currencies is refused.
2. The refusal names what would have gone wrong, not merely that two strings
   differ.
3. The mismatch is refused whichever side differs.
4. Case differences are refused rather than normalized.
5. No portfolio is funded before the configuration is refused.
6. A matching configuration still funds exactly once and initializes exactly as
   it did before.
7. Every `ExecutionMode` is refused the same way, and a backtest is refused at
   the same boundary.
8. Each venue concept lives in its own layer; a canonical record never carries a
   listing exchange; `Bar` and `OrderBookSnapshot` carry no venue at all;
   normalization reads the policy and never the instrument.
9. All three venue values can differ in one run while the instrument identity is
   shared.
10. The listing exchange is part of the identity and the venues are not.

Suites: `tests/regression/test_currency_roles.py`,
`tests/regression/test_venue_concepts_stay_distinct.py`.

---

# Migration and compatibility

No data migration. One behavioural break: a configuration that previously
initialized and then traded nothing now raises at `initialize`. That break is
the decision. Any run it refuses was already producing no fills while reporting
its full starting equity.

---

# Explicit non-goals

- Any FX rate source, conversion, or multi-currency aggregation (ADR-0020).
- Any change to `asset_id` derivation, its four inputs, or the instrument
  namespace.
- Extending the currency check to `NAVCalculator`, `portfolio_value`,
  `long_value` or `short_value`.
- Normalizing or case-folding currency codes anywhere.
- Deriving a canonical record's `venue` from an instrument's `exchange`.
- Restricting foreign-currency instruments from being held or booked.

---

# Consequences

Benefits. A misconfiguration that silently disabled two risk limits and produced
no fills is now impossible. Three currency roles and three venue concepts have
names, and the boundaries between them are asserted rather than assumed. The
identity contract in ADR-0016 is untouched.

Costs. A previously accepted configuration is now refused. Exact comparison
means `"usd"` and `"USD"` are a mismatch, which is deliberate but will surprise a
caller who expects normalization. The remaining currency-blind valuation helpers
are left as they were, which is a known and bounded inconsistency recorded in
ADR-0020.

---

# Alternatives Considered

**Normalize currency codes before comparing.** Rejected. `CashLedger` keys
balances by the exact string, so normalizing at the check would let the
configuration pass while the ledger still split the balance across `"usd"` and
`"USD"` — reintroducing the defect one layer down.

**Derive `Account.base_currency` from `config.currency`, or the reverse.**
Rejected. It removes the disagreement by removing a field, but the two are read
by different subsystems for different purposes, and collapsing them would make
the account object depend on pipeline configuration.

**Warn rather than refuse.** Rejected. The failure mode is silence; a warning is
also silence in any non-interactive run.

**Unify the three venue concepts into one field.** Rejected on evidence.
`ExecutionReport.venue` would report the listing exchange for an order that
executed against the simulator. The three are used for a MIC, a trading venue
and a provider name in the repository's own tests.

---

# Release impact

Minor-version feature with one deliberate behavioural break at configuration
time. No persisted format change, no schema movement, and no change to
instrument identity.
