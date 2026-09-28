# ADR-0045: The Pre-v4 Correctness Release — Risk, Money, Analytics, Execution Realism and the Boundary

## Status

**Accepted and implemented in v3.10.0.**

The first of the four releases the pre-v4 audit plans before the v4.0 freeze
(`docs/audit/PRE_V4_MASTER_AUDIT.md`; the item-by-item plan of record is
`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`). Unlike v3.1–v3.9 it adds almost
no capability: it corrects the canonical path, removes code that contradicts
the library's boundary, and makes persisted state upgradeable, so that what is
frozen at v4 is correct rather than merely stable.

It **removes** two packages (`alphalab.feed`, `alphalab.live`) and five vendor
subpackages of `alphalab.marketdata`, and adds **no package and no package
edge**. New modules: `alphalab.common.currency_units`, `alphalab.common.arithmetic`,
`alphalab.persistence.upgrade`, `alphalab.portfolio.book`,
`alphalab.risk.projection`, `alphalab.runtime.assumptions`. Snapshot schemas:
portfolio 3→4, pipeline 3→4, run 1→2, every older payload upgraded on read.

Supersedes the roadmap's **"No migration framework"** boundary (ledger
BDY-007). Amends **ADR-0019** (a currency's minor unit is now a property of the
currency, not a constant), **ADR-0016** (the wire boundary now also owns when a
bar is stamped), **ADR-0028** decision 8 (the routing configuration is no longer
optional), **ADR-0038** (execution assumptions are recorded, not only
modelled) and **ADR-0043** (the book and valuation identities render amounts by
value, under v2 schemes). Depends on **ADR-0008**, **ADR-0009** (the canonical execution
path), **ADR-0012** (the broker mirror), **ADR-0015** (the reservation ledger)
and **ADR-0041** (fingerprints).

---

# Context

The pre-v4 audit re-read every subsystem of v3.9.0 against three questions: is
each number the canonical path produces correct, would a researcher be misled
by any default, and could the design be frozen for years. It found the path
well-structured and wrong in places a user reads first, among them: an account
at its exposure limit could not sell; money in every currency was rounded to a
cent and every price to four decimals before money was computed; a Sharpe
ratio was annualized with 252 periods whatever the data were; an order filled
at the price its strategy had just been shown; a start-stamped bar reported
its close at its opening instant; and the path was quadratic in the universe —
in the audit's probe, 8× the assets took about 35× as long (0.87 s for 10
assets, about 30 s for 80, 50 records each). It also found vendor clients inside the library — four of them stubs —
and silent `"USD"` defaults on configuration the v3.4 defaults sweep had
exempted. The ledger assigns 59 items to v3.10; this ADR records the decisions
behind them.

---

# Decision

## 1. Risk is judged on the projected book, and never refuses a reduction

Every pre-trade check reads one `RiskProjection` of the book **after** the
order: the portfolio's signed position (the position authority, read at
evaluation time), the signed order, and the signed remainder of every working
order (`WorkingExposure`, from an OMS index kept per asset). A limit refuses an
order only if the order makes the limited measure worse than it already is:
buying power is charged only for the part of an order that grows a position; a
trade that reduces gross exposure is never refused by the gross limit; in a
drawdown or daily-loss breach only orders that grow absolute exposure are
refused, and the breach is reported on every decision.

*Why projection rather than per-check arithmetic:* each v3.9 check did its own
arithmetic on the order alone, and three of them added a notional to a quantity
or ignored the side. One projection, built once, is one place to be right.

A daily loss limit names the IANA zone its trading day is kept in
(`DailyLossLimit.zone`, required). No zone is assumed: the day that ends at
midnight UTC is not the day a Tokyo desk or a New York desk trades. A v3.9
payload's daily loss limit named no day and was never enforced; its upgrade
drops it with a `SchemaUpgradeWarning` rather than inventing a zone.

## 2. Allocation sees positions as numbers, and an intent is a delta

Long-only is judged against the committed position — filled plus working — so
a sale that closes a long passes and one that would leave a short does not.
Allocation receives it as a plain `Mapping[str, Decimal]`: `alphalab.allocation`
goes on knowing nothing of the portfolio or the OMS.

`Intent.target` was documented as a target and treated as a delta. The code is
right and the documentation was wrong, so the documentation is corrected and
the kind is stated (`IntentKind.DELTA`); position-aware targets are a v3.11
addition that will be a *new* kind, not a reinterpretation of an old one.

Sizing refuses what it cannot size — a missing or non-positive volatility, a
non-positive price — instead of substituting 1% or returning zero; a refused
intent is recorded and its asset reported unpriced.

## 3. Money: each currency's minor unit, exact prices, one pinned context

The minor unit is a property of the currency. ISO 4217 is the stated standard
(`ISO_4217_MINOR_UNITS`, transcribed with its provenance); anything ISO 4217
does not list — a stablecoin, a crypto asset, a metal — is **declared** by the
book that settles it (`Account.currency_units`), and a currency with neither is
refused. A declaration may not contradict the standard: yen with two decimals
is the defect, not a configuration.

Prices and quantities are kept exactly as the venue or the market gave them.
Money is rounded once, half to even, at the currency's minor unit, by whoever
produces it. Lot sizes are a sizing concern and never an accounting one.

All accounting and execution arithmetic runs in `ACCOUNTING_CONTEXT`
(precision 34, half-even, trapping invalid operations, division by zero and
overflow), never in the ambient context of the caller's thread; a regression
sweep refuses ambient-context arithmetic on those paths.

## 4. Analytics: per instant, periodicity declared or observed, undefined is None

The curve is reduced to one equity point per instant before returns are taken.
Annualization is declared (`RunConfig.periods_per_year`) or observed from the
curve's own span, **never assumed**, and the report says which
(`Periodicity`). A statistic that is undefined — the Sharpe ratio of a constant
series, the CVaR of nothing — is `None`, never 0.0 and never infinity. Trade
statistics count the fills that realized P&L.

The risk-free rate keeps its default of zero, and this is a decision, not an
exemption: the rate a ratio used is written into the report
(`RiskSummary.risk_free_rate`), so no ratio is published without its rate. A
v3.9 report is restated on read as what it was — annualized at 252,
`Periodicity.ASSUMED` — not recomputed.

## 5. Execution realism: stated, selectable, and reported

**Fill timing** (`FillTiming`) is a first-class choice. `NEXT_EVENT` keeps a
simulated order working until its asset's next event and fills it there,
before the strategy is dispatched, once; `SAME_EVENT` is what every run did.
The default stays `SAME_EVENT` so that every existing run reproduces
bit-for-bit — and because the default is optimistic, the run says so:
`ExecutionAssumptions` states the routing, fill timing, fill policy, costs and
latency of every run and result, and lists in sentences what is optimistic
about them. A frictionless simulator is named as such
(`ExecutionSimulator.is_frictionless`).

A strategy that fails is reported, not absorbed: `strategy_failures` on the run
and the result, derived from the lifecycle events the run already persists, and
`RunConfig.halt_on_strategy_failure` to stop at the failing record with
`StrategyFailedError` carrying the run. The default stays "run on", which is
what a research sweep over many strategies wants; a failure is never silent
either way.

*Deviation from the ledger's wording, recorded:* fill timing lives on
`ExecutionPipelineConfig` (where the simulator is) rather than on `RunConfig`,
and the execution assumptions and strategy failures are derived from the
recorded configuration and events rather than added to the persisted
`PerformanceReport`. Both are reachable from every run and result; neither
duplicates a recorded fact.

## 6. The canonical path is linear in the universe

A market event re-marks the asset it priced, plus the positions a fill priced
since their last mark (`pending_marks`, persisted). `PositionBook` keeps exact
per-currency totals — long value, short value, unrealized P&L — updated by
difference with `Inexact` trapped, so valuation, NAV and risk read totals
instead of re-summing; the price map is persistent; the book is valued once
per event. On a single-currency book this is exact, not approximate: the
incremental path gives what re-marking every position gives, figure for
figure.

**One deliberate numeric change**: a mixed-currency book converts each
currency's totals once — one rounding per figure — where v3.9 converted and
rounded each position. The new figure is the more exact one.

## 7. A bar is stamped at the end of its interval

The canonical bar's timestamp is the instant its close became knowable: the
end of its interval. A source's convention is **required** at the boundary —
`BarStamp.INTERVAL_START` bars are moved by their interval and the move is
recorded as a transformation; undeclared, they are refused. A bare-date bar
takes its instant from the date policy, which must be end-of-day for bars.

## 8. Persisted state is upgradeable, honestly

Each snapshot subsystem declares a `SchemaHistory`: explicit, pure, composable
steps from each version to the next, run on primitives before typed decoding,
so a decoder only ever reads its current schema. A step supplies a value only
when it is what the older payload already meant; when no honest value exists
it **refuses** (`SchemaUpgradeRefused` — a v3.9 book holding fractional yen is
refused, not rounded); when a recorded fact cannot be carried it **warns**
(`SchemaUpgradeWarning`). Payloads written by v3.9.0 itself are kept as golden
fixtures. This replaces the roadmap's "No migration framework" boundary, which
would otherwise have made the first post-freeze fix that needs a durable field
unreadable against every earlier payload.

## 9. The boundary: providers and venues are the application's

AlphaLab does not ship vendor clients. `alphalab.feed`, `alphalab.live`, the
five vendor subpackages of `alphalab.marketdata` (four of them
`NotImplementedError` stubs) and the v1 provider engine with its API-key
configs are removed, as are the exchange symbol spellings in `alphalab.crypto`.
What a host application implements is one method —
`BarHistoryProvider.request_history`, returning wire bars — and what it declares
is an instrument's aliases; the canonical market model, the transports and the
normalization boundary stay AlphaLab's.

## 10. No silent defaults: the rule, and the three things that are not defaults

A currency, a rate, a zone, an annualization or a seed is named by the caller.
The six `"USD"` configuration defaults the v3.4 sweep exempted are removed
rather than exempted, and the sweep itself is tightened: a default of zero is a
default (`0.0 == False` had let every zero rate through), and a literal passed
at a call site or read as a mapping fallback decides a value exactly as a
default does. Three things stay, each for a stated reason:

* `ExecutionPipelineConfig.currency` left empty **is** the account's base
  currency — a currency the caller named, once, on the account.
* `NormalizationPolicy.venue` defaults to `"UNKNOWN"`, a label that says a
  quote is unattributed rather than an attribution.
* `risk_free_rate` defaults to zero because the report records it (decision 4).

The v1 research engine's daily assumption (ledger RES-001) is exempted by name
until v3.12 consolidates it into the one research authority, and its
functions say so.

## 11. Determinism is not optional

Every stochastic step takes an explicit seed (non-negative, `bool` refused) and
the identifier stream is pinned by a golden test; concurrent outcomes are
applied in submission order; the optimizer reads no clock; the version is
declared once and read by both the build and the package.

An identity derived from content renders each amount by value
(`canonical_text`: one text per number, whatever its exponent). Once money is
rounded only by its producer, `250000` and `250000.00` both reach a book, and
two books that compare equal must not have two identities; the book and
valuation schemes are v2 for that reason, beside reconciliation's. The other
content identities follow in v3.11, each with one scheme bump (DET-006).

---

# Consequences

* **Breaking, by design.** Each break replaces a silent wrong answer with a
  correct one or a refusal naming what to supply; the CHANGELOG's migration
  table lists every one. A v3.9 snapshot needs no migration by the caller.
* **Numbers move where they were wrong**: risk decisions that refused
  reductions, money in non-two-decimal currencies, anything priced below a
  hundredth of a unit, execution costs and capacity figures that were rounded
  to four decimal places, every annualized statistic of a non-daily or
  multi-asset run, trade statistics, and mixed-currency valuations -- and every
  content identity downstream of one of them. The release checked the rest
  example by example: with run-varying ids and digests masked, 49 of the 65
  examples print what they printed in v3.9, and the CHANGELOG accounts for
  each of the other 16.
* **The v4 freeze inherits the upgrade framework**: a post-freeze fix that
  needs a durable field adds a step instead of breaking every earlier payload.
* **Still open**, each with a ledger ID and a release: position-aware target
  intents and asset-class margin (v3.11); the boundary for `enterprise` and the
  application-shaped packages (v3.11); the research authority consolidation
  (v3.12); the rerun harness and the remaining v3.9 deferrals (v3.13).
