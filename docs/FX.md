# FX Deals: an Account Holds Currencies, Not Pairs

Until v4.3 an FX trade was booked as units of a pair (GAP-23). Buying a million
EUR/USD held a million "units" valued in dollars, and moved dollars and no
euros, so the account never held the currency it bought. A record that declares
an FX deal's terms (v4.3, ADR-0067) is booked as the two currencies the deal
exchanges, each pending until a value date counted by the pair's own rule. A
record that declares none is booked as it always was.

None of this says a currency strategy works. It says what each deal moved and
when, in each currency, so a book of them can be read and reconciled.

---

## Three rules that hold everywhere

**Currencies, not pairs.** A deal leaves cash in two currencies and no
position. What it is worth later is what those balances are worth, converted on
demand at rates the run supplies, with their provenance (ADR-0020). Nothing
converts at a fill, and nothing triangulates or inverts a rate (AC-17).

**Declared, never inferred.** A pair's value-date rule is stated: its spot lag,
the calendars a counted day must be open in, and those its value date must be
open in. Nothing is read from a pair's name. Every calendar is the run's, from
a pack (ADR-0059) or the researcher, and a calendar the rule names but the run
does not declare is refused. None is assumed open.

**Funded, not financed.** A deal is paid from the cash of the currency it pays,
by its value date. A deal that cash cannot cover is refused before an order
exists. Margin and borrow terms on a declared pair are refused (ADR-0067's
known limitation 1).

---

## Start here

```python
from dataclasses import replace

from alphalab.conventions import BusinessDayConvention, Tenor, TenorUnit
from alphalab.core.enums import AssetType
from alphalab.instrument import FxKind, FxTerms, FxValueDateRule, InstrumentRecord

# EUR/USD's common rule: two days counted in TARGET, settled where TARGET and
# New York are both open. The ids name calendars the run declares.
spot_rule = FxValueDateRule(2, ("TARGET",), ("TARGET", "USNY"))
three_months = replace(
    spot_rule,
    tenor=Tenor(3, TenorUnit.MONTHS),
    convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    end_of_month=True,
)
spot = InstrumentRecord(
    "EURUSD",
    AssetType.FOREX,
    "XFXV",
    "USD",
    terms=FxTerms("EUR", "USD", FxKind.SPOT, spot_rule),
)
forward = InstrumentRecord(
    "EURUSD3M",
    AssetType.FOREX,
    "XFXV",
    "USD",
    terms=FxTerms("EUR", "USD", FxKind.FORWARD, three_months),
)
```

The run declares the calendars by id in `ExecutionPipelineConfig.calendars`,
beside the listing venue's own: `VenueCalendars({"XFXV": venue, "TARGET":
target, "USNY": new_york})`. It settles both currencies, the base in
`also_settles`, and supplies the rates its book is valued at.

---

## The terms

A pair's record is a `FOREX` record priced in its quote currency. Its
`FxTerms` are outside its identity key, and state:

* `base` and `quote`: distinct ISO 4217 codes;
* `kind`: `SPOT`, `FORWARD` or `NDF`;
* `value_dates`, an `FxValueDateRule`:
  * `spot_lag`: business days from the trade to spot;
  * `counted`: the calendars a counted day must be open in;
  * `settles`: the calendars the value date must be open in;
  * for a forward, either an `outright` date, or a `tenor` with its
    `convention` and `end_of_month` rule;
* `fixing`: for an NDF, its `NdfFixing`.

None has a default, and a malformed deal is refused where it is declared. A
pair may state a lot or a minimum notional in its economics. A settlement rule,
margin terms or borrow terms beside its FX terms are refused.

## Value dates

`alphalab.conventions` counts them, by the `TradingDayCalendar` protocol, so a
`MarketCalendar` is a calendar here:

* **Spot** counts the lag in the `counted` calendars, then rolls forward to the
  first day every `settles` calendar is open.
* **A forward** adds its tenor to spot under its convention (ISDA 2006 section
  4.12's following, modified following or preceding, or `UNADJUSTED`). Under
  the end-of-month rule, a spot on its month's last business day lands on the
  last business day of the month reached.

The common conventions are two choices of lists. A US-dollar pair counts in the
other currency's calendar alone, so a US holiday the day after the trade does
not delay spot. Its value date must be open in both, so the Fourth of July moves
it. A cross counts in both its currencies and settles on a US business day too.
A market whose convention differs states its own lists.

In a run, the trade day is the session of the listing venue's calendar the fill
belongs to, and the legs settle at the value date's first open there.

## What a deal books

`PortfolioEngine.apply_fx_deal` books buying `q` at `p` as `q` of the base
credited and `q x p` of the quote debited, with the commission. Selling
reverses it. Each amount is rounded once, half-even, to its currency's minor
unit.

* Two ledger transactions of type `FX_LEG`, one per currency, each naming the
  deal (`FX_DEAL`) and its value date (`VALUE_DATE`);
* one `FxDealBooked` event;
* no position, and nothing realized.

## Cash: trade-date, settled, available

Both legs move trade-date cash at the deal, as every fill does (FUT-015), and
each is pending until the value date. `PortfolioEngine.cash_position(state,
currency, at)` reports each currency's cash:

* **trade-date**: the cash ledger's balance;
* **unsettled credits**: the currency a deal will receive, not yet arrived;
* **unsettled debits**: what a deal will pay, already out of trade-date cash;
* **settled**: what the bank holds;
* **available**: trade-date less what has not arrived.

A deal is paid from available cash, less working orders that pay in the
currency. The one exception: a credit that arrives by the deal's own value date
counts, because a forward maturing that day delivers what the deal pays.

## Forwards, valued

Before it settles, a forward is valued as its two pending amounts converted at
the supplied rates, undiscounted: euros at the euro's rate, less the dollars
owed. Discounting at a curve is ADR-0065's, declared when a run wants it.

## A rollover, as a swap

A swap is a package of two deals, placed whole or refused whole (ADR-0062): the
near leg sells the currency at spot for the maturing forward's value date, and
the far leg buys it at the new one.

```python
from alphalab.common.order_terms import MARKET
from alphalab.strategy.events import Intent, IntentKind, Leg

roll = Intent(
    strategy_id,
    "ROLL",
    Decimal("1000000"),
    terms=MARKET,
    timestamp=at,
    kind=IntentKind.PACKAGE,
    legs=(Leg(spot.asset_id, Decimal("-1")), Leg(forward.asset_id, Decimal("1"))),
)
```

* Each leg is refused as a single FX order would be, with the other legs
  counted, so a fully invested roll's far leg is paid by its near leg's
  proceeds.
* The legs are booked in settlement order, near first, whatever order the
  strategy names them in.
* An FX deal spends no buying power on either side. Its funding is held by the
  per-currency rule above.

The near leg settles on the day the maturing forward does. The currency arrives
and leaves that day, and the dollars the roll gained arrive with it.

## NDFs

A non-deliverable forward moves no currency at its trade but its commission.
Its `NdfFixing` states the currency its difference is paid in, a fixing lag in
business days before the value date, and the name of its fixing source.

* **The fixing is supplied**, never read from a table that could hold another
  source's rate. `ExecutionPipeline.fix_ndf` takes an `FxRate`, and a
  backtest's schedule takes a `ScheduledFixing`. The rate must be:
  * the pair's own, never inverted;
  * from the source the deal names;
  * true on its fixing date;
  * known by the instant it is booked.
* **The amount** is `notional x (fixing - rate)` in the quote. Paid in the
  base, it is that divided by the fixing. It is rounded once, realized, and
  pending to the value date.
* **A deal that reaches its value date unfixed stops the run**, with the
  reason. Nothing settles at an invented rate.

Before its fixing an NDF is in the book at nothing: its gain or loss enters at
the fixing (ADR-0067's known limitation 2).

## S-05, end to end

`examples/84_carry_a_book_of_fx_forwards.py` holds a million Australian and a
million New Zealand dollars a month forward through 2025. It rolls each forward
as a swap on the day the pair's spot reaches its value date. Its book is
`examples/_fx_carry_book.py`'s, which states the calendars, the pairs and their
rules, the synthetic prices and the interest rates that set the forwards.

* 46 deals, 92 currency legs, and no position.
* Each value date follows both calendars. For example, the New Zealand
  forward struck on 2 January is valued 7 February, because Waitangi Day moved
  it from the 6th.
* Each currency's cash equals a tally of the fills, commission included.
* What the book earned is the forwards' discount and spot's moves, and the two
  add up to the dollars it gained.

`tests/integration/test_fx_carry_end_to_end.py` holds every one of these.

## Not here

* **A financed FX deal.** A deal is paid from cash held: margin on FX exposure
  by its pending legs is not built (ADR-0067's known limitation 1).
* **An NDF's value before its fixing** (known limitation 2).
* **FX options** beyond the `options` engine, and vendor rate feeds.
* **A strategy book in currencies.** The allocation records what each strategy
  dealt in each pair, so a sale in a pair a strategy has not bought is a short
  under its allocation constraints.
