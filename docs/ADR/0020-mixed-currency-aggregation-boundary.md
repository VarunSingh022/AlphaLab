# ADR-0020: Mixed-Currency Aggregation and the Valuation Boundary

## Status

Accepted (v2.8.0). Written to disk in v2.9.0.

This decision was taken and implemented during v2.8 and was not recorded at the
time. This file records it as it was accepted and as v2.8.0 shipped it. Its
substance is not reopened here.

Depends on ADR-0019, which separated the account, settlement and trading
currency roles. This ADR records what happens when a book nonetheless holds more
than one currency: the valuation refuses rather than inventing a rate.

**Decision 5 is discharged by ADR-0028 (v2.12.0)**, which is not the release
that supplies a rate source. `NAVCalculator.calculate`, `PortfolioValuation.portfolio_value`
and `_risk_exposure` — including the `sector_exposure` v2.11 added — now refuse a
mixed book through the same rule this ADR established. `long_value` and
`short_value` do not, and ADR-0028 decision 7 records why: they name no base
currency, so they make no currency claim and cannot be told what to refuse
against. The refusal itself, its two conditions and its message are unchanged.

---

# Context

`PortfolioValuation.snapshot` returns one number labelled with one currency.
Until v2.8 it produced that number from a book that might hold several, and the
two halves of the calculation disagreed about what to do:

- `cash` was read for the base currency alone, **silently dropping** every other
  balance;
- `long_value` and `short_value` summed **every** position regardless of what it
  traded in.

A book of 1000 USD and 500 EUR cash against 1100 USD and 1100 EUR of positions
reported `equity=3200.00` labelled `"USD"` — a figure in no currency at all. It
was not a rounding error or an approximation; it was a number produced by adding
quantities that cannot be added, and then labelling the sum with one of the
units it was not in.

AlphaLab has no FX rate source. There is therefore no honest single number to
return for such a book, and no amount of care inside the calculation can create
one.

---

# Decision drivers

- **D1.** A wrong number labelled with a currency is worse than no number. A
  reported `equity` is read by risk, by analytics and by a human; a figure in no
  currency at all propagates silently through all three.
- **D2.** Absence of a rate is not invalidity of an instrument. The model
  already supports foreign-currency holdings (ADR-0019 decision 3) and this must
  not narrow it.
- **D3.** Refuse before computing, so a refused valuation returns no partial
  figure a caller might use.
- **D4.** Do not invent infrastructure to close a gap that is genuinely
  external. An FX rate source is a provider dependency, not an internal design
  question.
- **D5.** Bound the change. Extending the rule to every currency-blind helper
  would change the behaviour of the risk resync, which runs on every market
  event.

---

# Decision

## 1. A valuation that would span two currencies is refused

`assert_single_currency(state, base_currency)` runs at the top of
`PortfolioValuation.snapshot`, before anything is computed, and raises
`MixedCurrencyValuationError` when the book cannot be expressed as one figure.

Two conditions, because there were two independent silent errors:

- every `Position` must declare `base_currency`, or its market value would be
  summed into a total it is not denominated in;
- no other currency may hold a **non-zero** cash balance, or that balance would
  be dropped from the total without trace.

## 2. Zero balances are ignored, and reservations are not inspected separately

`CashLedger.withdraw` subtracts in place and leaves the key behind, so a spent
currency is a routine residue rather than a second currency. A zero balance
therefore does not refuse a valuation.

`reserved` is not inspected separately: `reserve` requires `available_cash` to
cover the amount, so a non-zero reservation implies a non-zero balance that the
cash condition already sees.

## 3. The test is on a position's declared currency, never on whether it has value

A flat position still says what it trades in. A rule that ignored a zero-quantity
position would answer differently depending on the order fills arrived in, which
makes the valuation boundary depend on execution history rather than on the
book.

## 4. This is the absence of a rate, not a rule against foreign instruments

The refusal message says so explicitly, and `MixedCurrencyValuationError`'s own
docstring says so. `CashLedger` is keyed by currency and `Position` declares its
own; holding and booking in a foreign currency is supported. Only *aggregating*
two currencies into one number is not.

## 5. The rule is confined to `snapshot`, deliberately

> **Discharged by ADR-0028 (v2.12.0).** The deferral below was to "the release
> that supplies the rate source". That release has not arrived, and the deferral
> was closed earlier on measurement instead: the guard costs approximately two
> per cent of the risk resync, and once ADR-0028's two seams hold, no pipeline
> run can produce the mixed book it refuses. `portfolio_value`, `NAVCalculator`
> and `_risk_exposure` now refuse; `long_value` and `short_value` remain
> unguarded on purpose, as component sums that name no currency.

`portfolio_value`, `long_value`, `short_value` and `NAVCalculator` share the same
currency-blindness and are **left exactly as they were**.

This is a bounded, named inconsistency rather than an oversight. Extending the
rule to them belongs with the release that supplies the rate source, where their
callers can be considered together rather than one at a time — the risk resync
runs one of them on every market event, so refusing there would turn a valuation
question into an execution-path failure.

---

# Ownership

| Concept | Owner |
| --- | --- |
| The refusal | `alphalab.portfolio.valuation.assert_single_currency` |
| The error type | `alphalab.portfolio.exceptions.MixedCurrencyValuationError` |
| Per-currency cash | `alphalab.portfolio.cash.CashLedger` — unchanged |
| Per-position currency | `alphalab.portfolio.position.Position` — unchanged |
| A future rate source | **Nothing.** No owner exists, and none is invented here. |

---

# Data model changes

```text
+ alphalab.portfolio.exceptions.MixedCurrencyValuationError
+ alphalab.portfolio.valuation.assert_single_currency(state, base_currency)

PortfolioValuationSnapshot        # UNCHANGED
PortfolioState / CashLedger       # UNCHANGED
Position                          # UNCHANGED

PortfolioValuation.portfolio_value / long_value / short_value   # UNCHANGED
NAVCalculator                                                    # UNCHANGED
```

---

# Boundary behavior

The check is a precondition of `snapshot`, not a state transition. It runs on a
value, raises before any figure is produced, and leaves the portfolio untouched.
A restored state is therefore checked the first time it is valued rather than at
construction — which is why the run-state envelope in ADR-0023 does not need to
re-assert this particular invariant, though it does need to re-assert
ADR-0019's configuration check.

---

# Persistence semantics

None. No persisted payload changes and no schema version moves. The refusal is
computed from state, never stored.

---

# Testing invariants

1. A book holding two non-zero cash currencies is refused.
2. A book holding a position denominated in something other than the base
   currency is refused.
3. A zero residual balance in a spent currency does not refuse.
4. A flat position still declares its currency and still refuses.
5. The message names the base currency and both sets of offenders.
6. A single-currency book values exactly as it did before.
7. No partial figure is returned by a refused valuation.

Suite: `tests/regression/test_valuation_refuses_mixed_currency.py`.

---

# Migration and compatibility

No data migration. One behavioural break: a mixed-currency book that previously
returned a meaningless number now raises. Any figure that break removes was
wrong.

---

# Explicit non-goals

- An FX rate source, provider, or conversion of any kind.
- Rate caching, triangulation, or a reference-currency hierarchy.
- Extending the refusal to `portfolio_value`, `long_value`, `short_value` or
  `NAVCalculator`. *(Done for the first and last by ADR-0028; declined for the
  middle two, with reasons.)*
- Preventing foreign-currency instruments from being held, booked or traded.
- Per-currency sub-valuations returned as a breakdown.

---

# Consequences

Benefits. A figure in no currency at all can no longer be produced or reported.
The absence of FX is stated where it bites, in a message that distinguishes a
missing rate from an invalid instrument. Single-currency books — every book
AlphaLab is used for today — are unaffected.

Costs. Four valuation helpers remain currency-blind, which is a real and named
inconsistency carried deliberately until a rate source exists. *(Closed by
ADR-0028 for two of them; the other two are reclassified as component sums that
make no currency claim.)* A caller with a
genuinely multi-currency book gets an exception rather than a breakdown, and has
no supported way to obtain one.

---

# Alternatives Considered

**Sum only the base-currency components and label the result.** Rejected: this
is what `cash` already did, and it is precisely the silent dropping the decision
removes.

**Return a per-currency breakdown instead of one figure.** Rejected for v2.8 as
a scope expansion. `PortfolioValuationSnapshot` is a single-figure type read by
analytics and reporting; changing its shape to a mapping is a larger change than
the defect warrants, and it does not answer the question a caller asked.

**Introduce a rate source with a fixed or configurable rate.** Rejected. A
configured rate is an invented one, and a figure derived from it is exactly as
wrong as the figure being removed, with the added cost of looking authoritative.

**Refuse everywhere, including `NAVCalculator`.** Rejected as unbounded for
v2.8. The risk resync calls into these helpers on every market event, so
refusing there converts a reporting question into an execution-path failure.
Deferred deliberately to the release that supplies the rate source.

**Do nothing and document the limitation.** Rejected. The figure was already
being produced and consumed; documentation does not stop a wrong number from
reaching a report.

---

# Release impact

Minor-version feature with one deliberate behavioural break at valuation time.
No persisted format change, no schema movement. Leaves a named, bounded
inconsistency in four helpers, to be closed by the release that supplies an FX
authority.
