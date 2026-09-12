# ADR-0028: Currency Authority and the Settlement Boundary

## Status

Proposed (v2.12.0).

Extends ADR-0016, which made currency one of the four fields `asset_id` is
derived from. Amends **ADR-0019 decision 2**, which recorded that the trading
currency "does not reach the execution path" — as of this decision it does, as
an authority over what a run may trade. Discharges **ADR-0020 decision 5**,
which deferred the currency-blind valuation helpers to "the release that
supplies the rate source"; this is not that release, and the deferral is closed
on measurement instead. Closes the **ADR-0027** non-goal "`InstrumentRecord.currency`
reaching `Position.currency`, and everything that follows from it".

FX rates, conversion, triangulation and multi-currency aggregation remain
deferred, and no part of this decision presumes they will arrive.

---

# Context

Three facts, each verified against v2.11.0 rather than inferred.

**The instrument's currency is known and ignored.** ADR-0016 makes currency an
identity input, so the registry holds an authoritative, immutable answer for
every registered instrument: changing it derives a different `asset_id`, and
`classify_instrument` cannot reach it. The execution path never asks.
`_instruction` stamps `ExecutionPipelineConfig.currency` onto every
`OrderInstruction`, `ExecutionReport.currency` copies it, and
`_apply_report_to_portfolio` books `Position.currency` from it. A
EUR-registered instrument traded on a USD pipeline produces
`Position.currency == "USD"` — a position labelled with a currency the
instrument does not trade in, with no refusal and no warning.

ADR-0019 recorded that as intended, and at the time it was: v2.7 had only just
introduced the registry and nothing on the execution path read it. v2.11
changed that. `config.instruments` is now threaded through the pipeline and
read on the fill path for sector. The distance between "this run does not know
the instrument's currency" and "this run knows it and ignores it" is the whole
of this decision.

**There is a fifth currency site, and nothing checks it.** ADR-0019 named three
roles. `NormalizationPolicy.currency` is a fourth, stamped onto canonical
quotes and ticks. `RoutingConfig.currency` is a fifth: it defaults to `"USD"`
and is compared to nothing. `execution_report_from_broker` stamps it onto a
venue fill and `apply_execution_report` books it, so a live sell against a
mismatched `RoutingConfig` creates a genuinely mixed book whose next valuation
raises mid-run. ADR-0019's one-settlement-currency invariant does not reach the
live path.

**A mixed book is already fatal, one event later.** `_process_requests` takes a
portfolio snapshot at the end of every event, and `PortfolioValuation.snapshot`
refuses a book spanning currencies. Funding a second currency onto a running
pipeline raises `MixedCurrencyValuationError` out of `process_quote` on the very
next event. Holding and booking a foreign currency is supported by
`PortfolioEngine` used standalone; it has never been supported by a pipeline
run, and the documentation that said otherwise was describing the engine.

---

# Decision drivers

- **D1.** A figure labelled with the wrong currency is the defect ADR-0019 and
  ADR-0020 exist to remove. One remained, and v2.11 made it indefensible by
  putting the right answer within reach of the code that ignores it.
- **D2.** Do not narrow what the model already supports. A wholly-foreign run
  is supported and stays supported; `PortfolioEngine` is a public engine and is
  not narrowed.
- **D3.** Identity is frozen. Nothing here touches `asset_id` derivation, the
  instrument namespace, the scheme tag, `canonical_key`, aliases, or
  classification.
- **D4.** Do not imply FX. No rate is read, stored, named or inferred, and no
  field is added whose only purpose would be to hold one later.
- **D5.** One question, one seam. A rule implemented twice becomes two rules
  that drift.
- **D6.** A live session must not die because one instrument is misconfigured.
- **D7.** Refuse rather than normalize. Comparison is exact — no `strip`, no
  `upper` — following ADR-0019 D4, because `CashLedger` keys balances by the
  exact string it is given.

---

# Decision

## 1. The instrument's currency is authoritative over what a run may trade

`InstrumentRegistry.record_for(asset_id).currency` decides whether this run may
trade this instrument. It is read through `_currency_of`, the exact sibling of
`_sector_of`: one keyed `PersistentMap` lookup, never a scan, minting no
identifier.

The authority is **opt-in with the registry**, precisely as sector is. A run
configured with `instruments=None` has no authority, books in the settlement
currency, and behaves exactly as it did in v2.11. This is honest rather than a
gap: a run with no registry cannot know an instrument's currency, and the
pipeline configuration is the only declaration it has.

An asset the registry does not hold produces no settlement refusal.
`UnpricedReason.NOT_REGISTERED` already owns "a strategy named an instrument the
registry does not hold", and one fault must not be reported twice under two
names.

## 2. STRICT_MATCH, and no policy object

An instrument may be traded only when its currency equals the pipeline's
settlement currency — which `_require_one_account_currency` already requires to
equal `Account.base_currency`.

There is no alternative mode, no `SettlementPolicy` enum and no configuration
field, because no alternative exists. A permissive mode could only mean one of
two things, and both were tested rather than assumed:

- *Book honestly in the instrument's currency.* The book becomes mixed, and the
  portfolio snapshot at the end of the **same event** raises
  `MixedCurrencyValuationError`. A mode whose only outcome is an unhandled
  exception one event later is not a policy.
- *Convert.* That is FX, and it is deferred.

A field with one legal value names a choice this release does not offer and
implies a rate source that does not exist.

## 3. Two seams, because there are two different questions

|  | **Seam 1 — authority** | **Seam 2 — integrity** |
| --- | --- | --- |
| Question | *May this run trade this instrument?* | *Is this report denominated in what this pipeline settles?* |
| Site | `_process_requests`, before the OMS | `_apply_report_to_portfolio` |
| Needs a registry | yes | no |
| Disposition | drops the request | raises `RuntimeValidationError` |
| Catches | a foreign instrument | a mismatched `RoutingConfig`, or any hand-built report |

**Seam 1.** A request for an instrument whose currency is not the settlement
currency is dropped, both allocation ledgers are retired through the existing
`_retire_dropped_request`, and the refusal is surfaced on
`ExecutionPipelineResult`. It runs **after** the existing unpriced check, so an
instrument that is both foreign and unpriced is still reported as unpriced: the
run genuinely never priced it, and the classification that was already there is
not reinterpreted.

**Seam 2.** A report whose currency is not the settlement currency raises. On
the simulated path it cannot fire — `report.currency` *is* `config.currency`,
placed there by `_instruction` — so it is a structural invariant there and a
live check for venue fills.

Neither seam subsumes the other, and both were shown necessary. With only Seam
2, a foreign instrument yields a report carrying the *settlement* currency, so
nothing mismatches and the mis-booking survives. With only Seam 1, a venue
report reaches `apply_execution_report` without ever passing through
`_process_requests`.

## 4. The dispositions differ because the vocabularies differ

The pipeline may decline to create an order; it may not decline a fill that has
already happened at a venue. This is the distinction `_close_unfilled_order` and
`_terminate_order` already draw between what the simulator reports a fill *did*
and what a venue reports the order *became*.

Raising at Seam 1 would kill a live session over one instrument and tear state
inside `_process_requests`, where an order, a reservation and a contribution
entry may already exist. Dropping at Seam 2 would silently discard a real
execution. Each seam takes the disposition its own position in the lifecycle
allows.

## 5. `_instruction` is not changed, and `Position.currency` follows by equality

Under STRICT_MATCH, `config.currency` and `_currency_of(registry, asset_id)` are
the same string for anything that trades — Seam 1 guarantees it. Re-reading the
registry per order would add a lookup on the hot path to obtain a value already
in hand.

`Position.currency` therefore follows `InstrumentRecord.currency` **by an
equality the refusal enforces, not by a second read**. The property is directly
testable and is tested: in a registry-configured run, every accepted fill
satisfies `Position.currency == registry.record_for(asset_id).currency`.

Nothing is added to `TradeRecord`, `OrderRequest` or `oms.Order`. Sector needed
freezing onto `TradeRecord` because it is mutable and a reclassification would
rewrite history; currency is an identity input and is already frozen by
identity, so a field would move the snapshot schema for provenance that exists.

## 6. One rule for a mixed book, three shapes of caller

`assert_single_currency_book(cash, positions, base_currency)` is *the* rule.
`assert_single_currency(state, base_currency)` — the existing public predicate,
unchanged in signature and behaviour — delegates to it, so there is exactly one
implementation and one message.

## 7. Which helpers refuse, and which do not: the rule already in the code

This was the one open question, and it is settled from structure rather than
invented. Reading `alphalab.portfolio.valuation` and
`alphalab.portfolio.exposure`, the codebase already sorts its helpers three
ways, consistently, and this decision only names the rule:

| Kind | Helpers | Refuses a mixed book |
| --- | --- | --- |
| **Aggregates across positions *and* names a base currency** | `PortfolioValuation.snapshot`, `PortfolioValuation.portfolio_value`, `NAVCalculator.calculate`, `_risk_exposure` (and the `sector_exposure` v2.11 added) | **Yes** |
| **Aggregates across positions and names no currency** | `PortfolioValuation.long_value`, `PortfolioValuation.short_value`, `PortfolioValuation.asset_values`, `ExposureEngine.long_exposure` / `short_exposure` / `gross_exposure` / `net_exposure` | **No** — they make no currency claim |
| **Names a currency but aggregates nothing** | `PortfolioValuation.cash_value`, `CashLedger.balance` | **No** — a keyed lookup returns what it was asked for |

`long_value` and `short_value` are therefore **components of a valuation, not
valuations**. They take no base currency, return no currency label, and cannot
be told what to refuse against: a `Mapping[str, Position]` carries no account.
Their only production caller is `PortfolioValuation.snapshot`, which calls them
*after* `assert_single_currency` and is the thing that attaches a currency
label. Their siblings in `ExposureEngine` have the same shape and nobody
proposes guarding those.

The rejected alternative was an optional `base_currency=None` parameter. It was
rejected twice over: it leaves the meaningless sum reachable by default, so it
buys no safety; and it is not free. Guarding both helpers was measured
end-to-end on the real pipeline at **+1.78%** over a 1,000-event, 50-position
run against a **0.27%** noise floor, because `snapshot` runs 2.01 times per
event and each guard adds a full traversal to a book `assert_single_currency`
has already proven homogeneous. Paying two per cent of every run for a check
that is provably redundant at its only call site is not a trade this release
makes.

What replaces the parameter is documentation and a test. The docstrings state
that these are component sums making no currency claim, and
`tests/regression/test_currency_authority.py` pins the three-way rule above so
that a later change to any helper's classification is deliberate.

## 8. `RoutingConfig.currency` is kept and constrained

The public field and the `execution_report_from_broker` signature are unchanged.
The value is constrained at Seam 2, where the report meets the portfolio, rather
than at `RoutingConfig` construction: a `RoutingConfig` is built standalone and
has no access to the pipeline it will be used with.

`route_order` additionally refuses to send an order when the routing currency
disagrees, through the existing `RoutingRefusal` vocabulary. That is an earlier
and better-located failure for the ordinary path, not a second rule: it reuses
the same comparison and cannot be relied on, because a report can reach
`apply_execution_report` without it.

The default `"USD"` is now a trap for a non-USD pipeline, and Seam 2 turns that
trap from silent book corruption into an immediate refusal.

## 9. `PortfolioEngine` is not narrowed

`PortfolioEngine.apply_fill` still accepts any currency, and a second fill in a
different currency still leaves `Position.currency` at whatever the first fill
declared while moving cash in the second. It is a public standalone engine,
ADR-0019 D2 forbids narrowing it, and
`test_an_entirely_foreign_book_values_in_its_own_currency` requires a
wholly-foreign book to keep valuing.

Once decisions 1–3 hold, no pipeline path can reach that divergence. It is
recorded here as a known, bounded limitation of the engine used directly rather
than repaired by restricting a public API.

## 10. No new exception type

`RuntimeValidationError` for a settlement mismatch — the class
`_require_one_account_currency` already raises, in the same module, for the same
category of fault: a call into the pipeline that names two currencies where one
is required. `MixedCurrencyValuationError` for a mixed valuation, which is its
documented meaning.

The settlement refusal must **not** borrow that error's missing-rate sentence.
It says this pipeline settles in one currency and this instrument trades in
another; it does not say a rate is unavailable, because the availability of a
rate is not what makes it refuse.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Instrument trading currency | `alphalab.instrument.record.InstrumentRecord` — identity input, unchanged |
| Currency authority lookup | `_currency_of`, `alphalab.runtime.execution_pipeline` |
| Settlement currency | `ExecutionPipelineConfig.currency` |
| Base/settlement agreement | `_require_one_account_currency` — unchanged |
| Seam 1, authority | `_process_requests` |
| Seam 2, integrity | `_apply_report_to_portfolio` |
| The mixed-book rule | `alphalab.portfolio.valuation.assert_single_currency_book` |
| Routing currency | `RoutingConfig`, constrained at Seam 2 and refused early by `route_order` |
| An FX rate source | **Nothing. No owner exists, and none is invented here.** |

---

# Data model changes

```text
+ alphalab.portfolio.valuation.assert_single_currency_book(cash, positions, base)
+ alphalab.runtime.execution_pipeline.SettlementRefusal
+ ExecutionPipelineResult.settlement_refusals      # appended last; derived, never persisted
+ RoutingRefusal.SETTLEMENT_CURRENCY               # a new member of an existing enum

ExecutionPipelineConfig          # UNCHANGED
Account / InstrumentRecord       # UNCHANGED
Position / CashLedger            # UNCHANGED
RoutingConfig                    # UNCHANGED shape and signature
TradeRecord / OrderRequest       # UNCHANGED
oms.Order                        # UNCHANGED
PortfolioValuationSnapshot       # UNCHANGED
PortfolioValuation.long_value / short_value       # UNCHANGED signatures
assert_single_currency           # UNCHANGED signature; now delegates
```

No schema constant moves. `ExecutionPipelineResult` is not part of any snapshot,
and `settlement_refusals` is appended after the last existing field so that
positional construction keeps working.

---

# Boundary behavior

Seam 1 sits in `_process_requests`, which every environment reaches through
`process_record`, `process_market_event` or `process_quote`. Seam 2 sits in
`_apply_report_to_portfolio`, the single function a simulated fill and a venue
fill both pass through — the same site ADR-0027 decision 6 chose for sector, and
for the same reason: parity across the four environments becomes structural
rather than a convention four call sites must keep.

A backtest, a replay, a paper run and a live session are therefore held to the
same rule, at the same two points, with the same messages.

---

# Persistence semantics

None. No persisted payload changes and no schema version moves:
`PIPELINE_SNAPSHOT_SCHEMA` stays 2 with `READABLE_PIPELINE_SCHEMAS` `(1, 2)`,
and `SESSION`, `BACKTEST`, `PORTFOLIO`, `OMS` and `ALLOCATION` are untouched.
Every v2.11 payload restores unchanged and re-captures byte-identically.

A durable, aggregated record of settlement refusals — the `UnpricedAsset`
treatment — is deliberately **not** added. It would put a new field on
`ExecutionPipelineState` and move the pipeline schema for observability the
per-event result already carries. It belongs with the FX release, which moves
that schema anyway.

---

# Testing invariants

1. A registry-configured run books `Position.currency == record_for(asset_id).currency`
   for every accepted fill.
2. A foreign instrument is dropped before the OMS: no order, no fill, no
   reservation and no contribution survive, and the run continues.
3. The refusal names the instrument, its currency, the settlement currency and
   the fix, and does not claim a rate is missing.
4. An instrument that is both foreign and unpriced is still reported as
   `REGISTERED_BUT_UNPRICED`.
5. An unregistered asset is still reported as `NOT_REGISTERED`, never as a
   settlement mismatch.
6. `instruments=None` behaves exactly as v2.11, including for a foreign
   instrument.
7. A wholly-foreign run funds, trades, marks and values unchanged.
8. A venue report disagreeing with settlement raises `RuntimeValidationError`,
   with and without a registry, through both `apply_execution_report` and
   `apply_broker_execution`, and leaves the caller's state untouched.
9. The simulated path cannot produce a report whose currency disagrees.
10. Each currency-claiming helper refuses a mixed book; each component helper
    does not, and the three-way classification is pinned.
11. `PortfolioValuation.snapshot` performs exactly one currency check per call.
12. A single-currency valuation is numerically identical to v2.11.
13. `PortfolioEngine.apply_fill` standalone behaviour is unchanged.
14. `id_position.draws` is identical to v2.11.0 for every existing scenario, and
    no settlement path mints an identifier.
15. `asset_id` derivation, `canonical_key`, classification and
    `TradeRecord.sector_id` are unchanged.

Suites: `tests/regression/test_currency_authority.py`,
`tests/regression/test_currency_roles.py`,
`tests/regression/test_valuation_refuses_mixed_currency.py`.

---

# Migration and compatibility

No data migration and no migration framework. Two deliberate behavioural breaks,
both correctness fixes:

1. **A run that silently mis-booked a foreign instrument now drops the request.**
   Every position that break removes was labelled with a currency the instrument
   does not trade in.
2. **A venue report disagreeing with settlement is refused.** Every book that
   break prevents was one whose next valuation would have raised anyway.

Measured against v2.11.0 before implementation: instrumenting
`_apply_report_to_portfolio` to flag any fill where a configured registry's
instrument currency differed from the report currency produced **zero
violations across the full 2,926-test suite**. No existing test trades a foreign
instrument on a registry-configured pipeline.

`test_the_currency_blind_siblings_are_unchanged_in_this_release` is rewritten,
as its own docstring anticipated: "Pinning the current behaviour here makes a
later change deliberate."

---

# Explicit non-goals

- Any FX rate source, rate type, provider, conversion, triangulation, rate
  caching or reference-currency hierarchy.
- Aggregating two currencies into one figure. `MixedCurrencyValuationError`
  keeps its meaning and its message.
- A per-currency breakdown on `PortfolioValuationSnapshot`. Under this decision
  a pipeline book holds one currency, and a mixed book is refused before any
  figure is computed, so the breakdown could only ever restate `equity`.
- Per-currency minor units. `CURRENCY_QUANT` stays `0.01`, and a JPY or KWD book
  still rounds to two places.
- Automatic cash conversion on a foreign buy — that is an FX trade, with an FX
  position and its own P&L attribution.
- Currency on `TradeRecord`, `OrderRequest` or `oms.Order`.
- `NormalizationPolicy.currency` reaching the execution path.
- Collapsing `Account.base_currency` and `ExecutionPipelineConfig.currency`,
  which ADR-0019 rejected for reasons that still hold.
- Making `ExecutionPipelineConfig.instruments` mandatory, or threading it
  automatically from a `NormalizationPolicy`.
- Narrowing `PortfolioEngine`.
- A durable settlement-refusal state field.
- Any change to instrument identity, the namespace, the scheme tag, aliases,
  classification or sector provenance.
- Sector- or currency-based risk **limits**. `RiskLimits` is unchanged and no
  check reads a currency.

---

# Consequences

Benefits. The last silent currency defect is removed: a run can no longer book a
position in a currency its own registry says the instrument does not trade in.
The live path is brought under the invariant ADR-0019 established for
configuration, closing a hole through which a venue report could mix a book.
Three currency-claiming helpers and the `sector_exposure` v2.11 added stop
returning figures that sum across currencies. The three-way helper rule is
stated and pinned rather than left to be rediscovered. FX becomes a genuine
leaf: a source protocol, a configuration reference and conversion behind a
branch that now exists.

Costs. The authority is opt-in with the registry, so a run that does not thread
`instruments` onto its pipeline keeps v2.11 behaviour — and the most realistic
misconfiguration, a crypto instrument quoted in a settlement currency the
account does not hold, is exactly the case where operators are least likely to
have threaded it. `PortfolioEngine` keeps its first-fill-wins divergence when
used directly. `RoutingConfig.currency` remains a separate field that must
simply agree. The guards cost approximately two per cent of the risk resync.

---

# Alternatives Considered

**A `SettlementPolicy` enum with a permissive mode.** Rejected on measurement,
not taste: booking honestly in the instrument's currency makes the book mixed,
and the portfolio snapshot at the end of the same event raises. The permissive
mode's only observable outcome is an unhandled exception one event later.

**Refuse at `initialize` by checking every registered instrument.** Rejected. An
`InstrumentRegistry` is a security master, not a run's universe; a global master
paired with a single-currency pipeline is a legitimate and common setup, and
`register_instruments` is built for exactly that scale.

**Raise at Seam 1 instead of dropping.** Rejected. It kills a live session over
one misconfigured instrument and tears state inside `_process_requests`.

**One seam instead of two.** Rejected on evidence: neither seam can see the
other's case.

**An optional `base_currency` on `long_value` / `short_value`.** Rejected. It
leaves the meaningless sum reachable by default, so it buys no safety, and
guarding both was measured at +1.78% end-to-end against a 0.27% noise floor for
a check that is provably redundant at their only production call site. See
decision 7.

**Record settlement refusals durably, aggregated per asset, like
`UnpricedAsset`.** Deferred. It moves `PIPELINE_SNAPSHOT_SCHEMA` from 2 to 3 for
observability the per-event result already provides, and should ride with the
FX release, which moves that schema anyway.

**Change `_instruction` to read the instrument's currency.** Rejected as
redundant: under STRICT_MATCH it is the same string, obtained by an extra
registry lookup per order on the hot path.

**A new `SettlementCurrencyError`.** Rejected. `RuntimeValidationError` already
covers an invalid call into the pipeline and is what the sibling currency check
raises; a second name would split one category.

---

# Release impact

Minor-version feature with two deliberate behavioural breaks, both correctness
fixes. No persisted format change, no schema movement, no migration, no new
identifier draw, no change to instrument identity or classification, and no FX.
