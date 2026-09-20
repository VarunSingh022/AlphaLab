# ADR-0039: Global Markets — Conventions, Contracts, and the Multi-Asset Boundary

## Status

**Accepted and implemented in v3.4.0.**

The fourth capability release on the architecture frozen at v3.0.0. It adds one
leaf package, deepens five existing ones, and makes six silently-defaulted
market conventions required. No ownership boundary moves and every v3.1, v3.2
and v3.3 invariant holds.

Depends on **ADR-0016** for the instrument identity these conventions sit
outside, **ADR-0019** and **ADR-0020** for the currency roles and the rate rules
the FX work obeys, **ADR-0027** for the classification provenance pattern this
follows, **ADR-0028** for the settlement boundary, **ADR-0033 decision 10** and
**ADR-0037** for the rule against invented defaults, and **ADR-0036** for the
position that a mechanism ships without its data.

---

# Context

AlphaLab could already hold a futures contract, an option and a perpetual, and
had been able to since the v1 engine series. What it could not do is say what
any of their numbers *meant* outside the market whose conventions had been
written into the defaults.

Six of those defaults were US or Binance conventions presented as universals:

| Where | Defaulted to | Right in |
| --- | --- | --- |
| `FutureContract.currency` | `"USD"` | the US |
| `OptionContract.multiplier` | `100` | US single-stock options |
| `OptionContract.style` | `AMERICAN` | US single-stock options |
| `OptionSpec.style` | `AMERICAN` | the same |
| `FundingRate.interval_hours` | `8` | most perpetual venues, not all |
| `CryptoInstrument.contract_size` | `Decimal("1")` | spot only |

`tests/regression/test_no_silent_financial_defaults.py` has swept the package
for exactly this shape since v2.17 and found none of them, because it sweeps
**function parameters** and every one of these is a **dataclass field**. Each
produced a number rather than an error when it was wrong, which is the failure
mode that file exists to prevent.

Beyond the defaults, four things were absent entirely:

* **No settlement-date arithmetic.** `alphalab.portfolio` owns the settlement
  *currency* and has since ADR-0019. Nothing anywhere turned a trade date into a
  settlement date, so T+1, T+2 and same-day settlement were not expressible.
* **No tick or lot grid.** `FutureSpec.tick_size` and
  `FutureContract.tick_size` were numbers nothing read. A tiered tick schedule —
  the normal shape outside the US — could not be stated at all, and neither
  could a lot size.
* **No roll rule.** `build_continuous_series` took segments a caller had already
  chosen, with the roll prices already computed. Which contract was front on a
  given day, when the roll happened and why were decisions the series did not
  record. Two researchers with the same contracts and the same bars could
  produce different series, both correct, differing only in a choice neither
  wrote down.
* **No implied volatility.** `VolatilitySurface` held observations somebody else
  had inverted. The inversion itself, and the cases where it has no answer, were
  not in the package.

And one thing was present and dangerous: `Position.market_value` is
`quantity * market_price`. Every contract bridge in the repository tells its
caller to apply the multiplier themselves, which is correct and has one failure
mode — the caller can apply it twice, or not at all, and nothing notices.
`alphalab/data/assets.py` opens by naming this as how "a backtest silently
computes P&L that is 1,000x wrong".

---

# Decisions

## 1. Market conventions are a leaf package over `alphalab.common`

`alphalab.conventions` imports `alphalab.common` and **nothing else in
`alphalab`**.

This is not a style preference. The package graph already runs
`data → options → portfolio`, so a convention authority that reached into any of
those could not also be used *by* them without closing a package-level import
cycle — the invariant `tests/regression/test_import_graph_stays_acyclic.py`
measures on every run. Being a leaf is what lets `options`, `futures`, `crypto`,
`portfolio`, `data` and `api` all speak one convention vocabulary.

**Rejected: putting conventions in `alphalab.data`, beside `MarketCalendar`.**
It is the natural home by subject matter and it would have made the package
unusable from `alphalab.options`, which `alphalab.data` imports.

## 2. The calendar authority does not move, and is reached structurally

Settlement counts trading days and a roll rule may count trading days, and
neither package may import `alphalab.data`.

Both declare a **one-method structural protocol** —
`conventions.settlement.TradingDayCalendar` and `futures.chain.SessionCalendar`,
each `is_trading_day(date) -> bool` — which
`alphalab.data.calendar.MarketCalendar` already satisfies. The calendar is
passed in. There is still exactly one calendar with holidays, sessions and a
timezone.

The precedent is `alphalab.common.point_in_time.PointInTimeRecord`, which lets
`macro` and `alt_data` share one point-in-time query without either importing
the other. `test_shared_names_stay_distinct.py` section 19 asserts both
protocols stay at one method, because a protocol that grew sessions and holidays
would be a second calendar in all but name.

**Rejected: giving `MarketConvention` a `MarketCalendar` field.** It would have
bundled the declaration with the data, and AlphaLab ships no holiday data. The
convention names its calendar by `calendar_id`; the calendar is supplied.

## 3. Six defaulted conventions become required

Each is a narrow, documented breaking change to a public API where correctness
required it — the class of change `ROADMAP.md`'s versioning section describes
and v2.17.0 last made, when it "required the margin rates and currencies that
had been silently defaulted".

The test that missed them is extended: `test_v34_invariants.py` sweeps
**dataclass fields** as well as function parameters, over a named set of
convention-bearing field names, with each surviving default listed alongside the
reason a wrong value there cannot produce a number.

Five `currency` defaults survive the sweep. Each reaches a seam that **refuses**
rather than converting — ADR-0028's two seams for the pipeline ones,
`assert_single_currency_book` for the valuation helpers — or belongs to a
standalone package with no accounting path. The exemptions are verified rather
than asserted: the test exercises the refusal.

## 4. A multiplier is multiplied in exactly one place

`conventions.market.contract_notional` is the only site in AlphaLab that
multiplies a contract count by a multiplier, and a regression test reads the
source of every module to keep a second from appearing.

`ContractNotional` carries the inputs beside the result, so a reader can verify
it was applied once, and reports **money** and **underlying units** as separate
fields — the two were being confused, which is the whole reason the type exists.

`Position` is unchanged and still has no multiplier.
`alphalab.portfolio.contracts` pairs a position with its convention and is a
different measurement from `ExposureEngine`, not a better one:
`test_shared_names_stay_distinct.py` section 21 holds the split and demonstrates
the 1,000x gap between them.

## 5. A continuous futures series is reproducible from four stated things

`ContractChain`, `RollPolicy`, the observations, and the `AdjustmentMethod`.
Nothing else, and no default anywhere in the chain.

The four stages stay separate: raw observations, the selected active contract
(`active_contract_at`), the roll events (`roll_schedule`, each carrying its
trigger and a readable reason), and the continuous representation
(`continuous_segments` feeding the unchanged `build_continuous_series`).

A roll rule **refuses the input it needs and was not given**: a trading-day
trigger without a calendar, a volume crossover without observations. Neither is
approximated from the other, because approximating one is a different rule
reported under this one's name.

Roll prices are the prints **at the roll instant** on both contracts. A missing
print raises rather than being interpolated: interpolating it would invent the
very print the whole adjustment is computed from.

**Rejected: inferring a roll rule from the data.** Volume, open interest and
days-to-expiry each give a defensible answer and they disagree, and a series
whose rule was inferred cannot be reproduced by anyone who did not run the same
inference.

## 6. An implied volatility that is not identifiable is refused

`implied_volatility` inverts the same expression `black_scholes_price` rounds —
`black_scholes_value`, made public so the identity is checkable rather than
asserted — and raises `ImpliedVolatilityError` in five cases: at or below the
no-arbitrage floor, at or above the ceiling, unreachable at `MAX_VOLATILITY`,
vega below `MIN_IDENTIFIABLE_VEGA`, and non-convergence.

Every one of those is routine in a real chain. Returning a number anyway — a
clamp, a fallback, the last iterate of a solver that never converged — produces
a surface with fabricated points in exactly the corners a trader looks at.

`surface_from_chain` returns the surface **and every refusal with its reason**,
and the two together account for every contract in the chain. A test asserts
that sum, because a surface that silently dropped its wings would still look
like a surface.

## 7. Greeks travel with the model that produced them

`ModelAssumptions` names the four things the model does not do: early exercise,
dividends, a volatility smile, and any year basis other than the 365.25 days the
code actually uses. `BLACK_SCHOLES_MERTON` is the record for this package's
pricer, and `ImpliedVolatility` carries it.

The precedent is `analytics.decomposition.VaRPolicy`, which carries the method
and the confidence together so "a figure cannot travel without the assumptions
that produced it".

## 8. Cross rates are derived deliberately, through a named currency

ADR-0020 refuses triangulation and that refusal stands: `FxRates.convert`
triangulates nothing and still raises `MissingRateError` for a pair it was not
given.

`FxRates.cross_rate(base, quote, via=...)` derives one when a caller asks, with
`via` **required**. EUR/JPY via USD and via GBP are different numbers, and a
result that does not say which route it took cannot be reconciled against
anything. The derived rate carries `derived=True`, a source naming both legs,
and the **older** of the two legs' `as_of`.

The standing is exactly `with_inverses()`: a deliberate act, marked as derived,
never performed implicitly.

## 9. The look-ahead guard on FX rates is now required, not optional

`max_age_seconds` has bounded how *old* a rate may be since v2.16. A rate whose
`as_of` is **after** the conversion instant is now `FutureDatedRateError`.

`ROADMAP.md` listed this under "optional future evolution" through v3.3. v3.4's
point-in-time rule makes it required: a stale rate is visibly old, and a
future-dated one is a look-ahead that produces a confident currency return the
position could not have earned. Applying it found a genuine half-second
look-ahead in `test_settlement_multi_currency.py`'s own fixture, invisible for
four releases because nothing checked.

## 10. Currency attribution is a return decomposition, not a second attribution engine

`AttributionDimension.CURRENCY` buckets realized P&L by settlement currency,
from trades, and deliberately has no total — summing it would need rates
ADR-0020 forbids inventing.

`currency_attribution` decomposes a **reporting-currency return** into a local
component and a currency component, from values and two rate tables. It has a
total because it was given the rates the other was not.

Neither derives the other. `alphalab.portfolio.fx_research` imports nothing from
`alphalab.analytics`, and a test reads the import graph to keep it that way.
The decomposition is an identity with no residual:

```
(V1 - V0) * r0  +  V1 * (r1 - r0)  ==  V1 * r1 - V0 * r0
```

Each component is rounded once by `to_money` — the repository's one rounding
site — and the cost of that rounding is carried on the result, the same way
`FxConversion.rounding` is.

## 11. Fixed income is a foundation, and says so

`alphalab.macro.bond` covers what a fixed-rate bond with known coupon dates
admits **exactly**: cash flows, accrued interest, clean and dirty price, the
yield inversion, duration and convexity. Every one is a closed expression or a
monotone inversion.

Deliberately absent, each because it needs a model whose choice is the
researcher's: credit spreads and default, embedded options, floating and
inflation-linked coupons, the 30E/360 and ACT/ACT ISDA day-count variants, and
the bootstrapping of a discount curve from traded instruments.

`YieldCurve` stays a set of **observed** yields. v3.4 adds `discount_factor_at`,
which turns an observed yield into a discount factor under a **named**
compounding convention, and does not add a bootstrapper: bootstrapping requires
choosing an interpolation scheme over an incomplete set of quotes, and the
choice changes every forward rate read off the result.

**This is not a fixed-income engine, and the module, the example and ROADMAP.md
each say so.**

## 12. Venue differences are input metadata, never hidden constants

`crypto.VenueSpecification` declares a venue's funding interval, fee schedule,
price source, minimum notional and settlement asset, with nothing defaulted.
Funding instants require an **anchor**, because eight-hourly funding at
00:00/08:00/16:00 UTC is one venue's schedule and a venue funding an hour later
on the same interval produces an entirely different set of instants.

Nothing here reaches a venue. AlphaLab holds no exchange adapter, no API client
and no credential, and a test greps this module for the shapes of one.

## 13. A 24/7 calendar does not invent observations

`MarketCalendar.continuous` models a venue that never closes and is the right
model. But "the market was open" and "there is an observation" are different
claims, and on a continuous venue an outage, a delisting and a quiet period all
look identical.

`crypto.availability` reports gaps as a **count of absences** and coverage
against a *theoretical* clock computed from the window and the declared cadence —
never from the data, which would make coverage identically 1.0 and the
measurement vacuous. There is no fill, no forward-carry and no interpolation,
for the reason `MissingValuePolicy` has no `FILL` member (ADR-0036 decision 3).

An unmeasurable coverage is `None`, never `0.0` or `1.0`.

## 14. Margin is carried, never computed

A futures margin requirement is set per contract by a clearing house, revised
without notice, and stated as money rather than as a percentage of notional.
`ContractMarginSpec` is what an exchange published; `position_margin` multiplies
it by a contract count and **refuses a specification published after the
research instant**.

There are now three margin surfaces and they answer three questions from three
inputs: `MarginEngine` reads a book and a stated rate; `compute_liquidation_price`
reads an entry price and a leverage and answers a *price*; `position_margin`
reads published figures and answers money per contract.
`test_shared_names_stay_distinct.py` section 20 holds the split.

## 15. The wire/domain contract join lives in `alphalab.api`

`alphalab/data/assets.py` has said since v3.1 that a `FutureSpec` "says what a
price series is about" while a `FutureContract` "opens a position in it", and
that joining them "is v3.4's work".

The join is `future_contract_from_spec`, `option_contract_from_spec` and
`convention_from_spec`, and it is in `alphalab.api` for the reason that module
exists: `alphalab.data` must import neither `alphalab.futures` nor
`alphalab.portfolio`, because that would close a package cycle and put a
standalone engine on the ingestion path. The v3.1 lesson that moved `api` to the
top of the graph applies unchanged.

`float` becomes `Decimal` through `str`. `Decimal(0.1)` is
`0.1000000000000000055511151231257827`, and a tick size holding that would put
every price off its own grid.

A `FutureSpec` with no `contract_month` is **refused**, not derived from its
expiry: `futures_symbol` is built from the month, and deriving one would mint an
identifier for a month the provider never named.

`convention_from_spec` takes the calendar id, the tick schedule and the lot
specification as **arguments**. A spec carries what a data provider knows; a
venue's calendar, its tick grid (tiered on most venues) and its lot grid are not
in a price series, and reading `FutureSpec.tick_size` here would silently
produce a flat grid for a venue that publishes a tiered one.

## 16. v3.4 adds no durable state

Every type added is a frozen value or a pure function. A `MarketConvention`, a
`ContractChain`, a `RollPolicy` and a `Bond` are declarations a caller holds,
not state a run carries.

So there is no new snapshot owner, no new schema constant, and no migration.
Every v3.3 snapshot payload round-trips unchanged. A test asserts the absence,
so that a later release adding durable state to one of these packages has to do
it deliberately.

---

# Consequences

## What this makes possible

A research path can now state, for an instrument in any market, what its numbers
mean — and be refused when it has not. A continuous futures series is
reproducible from four stated inputs. An implied volatility surface reports what
it could not invert. A multi-currency book's return separates what the assets did
from what the currencies did, with every rate recorded and no rate invented.

## What it costs

Six constructor signatures changed. The call sites are counted and updated; all
were in tests and benchmarks, and no production module constructed any of the
six without the argument.

## What is still external

Unchanged from v3.3 and extended by this release's surface area:

* **No holiday data, no tick table, no lot schedule, no venue registry.** The
  mechanism is AlphaLab's; the data is the application's.
* **No FX rate**, spot or forward. `covered_forward_rate` computes a parity
  forward from rates the caller supplies and is explicitly not a quote.
* **No deposit curve and no discount curve.** Both rate inputs to a forward are
  required arguments.
* **No margin figures.** A clearing house publishes them.
* **No exchange adapter, no API client, no credential.**

## What was deliberately not built

Recorded here and in `ROADMAP.md` so the boundary is a decision rather than an
omission: a curve bootstrapper, a volatility-surface fit (SVI, SABR), an
American pricing model, interpolation across expiries, credit and optionality in
the bond surface, and a second calendar.
