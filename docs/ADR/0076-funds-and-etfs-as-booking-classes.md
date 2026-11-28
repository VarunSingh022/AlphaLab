# ADR-0076: Funds and ETFs as Booking Classes -- an ETF Is a Share, a Fund Deals at Its NAV

## Status

**Accepted** (2026-11-28), under the owner's delegation of the programme's
decisions within the charter's principles (ADR-0056, 2026-11-10). It is the
record GAP-51 asks for its funds and ETFs: "each added class books by declared
terms, with tests; `AssetType` additions by ADR" (workstream WS-13, milestone
M10). It reverses no accepted decision.

---

# Context

**An ETF or a fund has no class of its own.** A researcher books one as an
`EQUITY` or not at all. So nothing can tell an ETF from the shares it holds,
in a limit, a classification or a report.

**An open-ended fund is not dealt like a share.** It is bought and redeemed at
its net asset value, by forward pricing. An order placed before the fund's
dealing cutoff deals at that day's NAV, published after the cutoff. One placed
after it deals at the next day's. A share's order fills at the price of the
instant it is decided, or of the next event. A fund's fills at a price not yet
known when the order is placed, and no rule says which.

**What exists.**
- A venue calendar states a venue's sessions in its own zone (EXE-010). A
  record lists on a venue.
- An order can be held from the market until an instant, and then worked at
  its asset's first event at or after it (ADR-0060's latency hold).
- A venue may have a cost model of its own (ADR-0061).
- A record's lot specification sets its quantity grid.

# Decision

## 1. Two asset types, appended

`AssetType.ETF` (`"etf"`) and `AssetType.FUND` (`"fund"`). Both are fully
paid units unless their record declares otherwise (`economics_for`), with a
lot or a minimum notional declared as for any record. A record's identity key
names its type, so no existing record's `asset_id` moves.

## 2. An ETF is a share

An ETF trades on its listing venue at its prices, intraday, by every rule a
share trades by. Its type classifies it, and nothing else differs. Creation and
redemption by authorized participants is out of scope.

## 3. A fund deals at its NAV, by forward pricing

- **The dealing window is the fund's listing venue's calendar.** The calendar
  states the fund's dealing sessions, so the cutoff is a session's close.
  - An order placed in a session deals at the fund's first price after that
    session's close.
  - An order placed outside a session deals at the first price after the next
    session's close.

  The price series a run is given for a fund is its NAV, stamped when it was
  published. A fund whose venue declares no calendar is refused, since its
  cutoff cannot be assumed.
- **How it is held.** The order is held from the market until the close
  (ADR-0060's hold), then worked at the fund's first price after it. That
  price is its NAV, and the order fills there in full.
- **What a fund's order may be.** A market order, good till cancelled. A day
  order would expire at the very close it is dealt after. A limit, a stop, an
  auction order, an execution algorithm's order and a package's leg are each
  refused, with the reason: a fund deals once, at its NAV.
- **Never sold short.** A redemption of more units than are held, less those
  already being redeemed, is refused.
- **Charges and precision are what exists.** A fund's subscription and
  redemption charges are its dealing venue's cost model (ADR-0061). The
  precision of its units is its record's lot specification.

## 4. What is recorded

Nothing new. A fund's fills, ledger and events are a share's, at its NAV. A
fund's distributions are cash flows, as a share's dividends are. An
accumulation unit's are in its NAV.

# Settled at implementation (2026-11-28)

1. **The cutoff is a day order's expiry.** `MarketCalendar.day_order_expiry`
   is the dealing day's last close: the day the order is placed in, or the
   next trading day's when it is placed while the venue is shut. That instant
   is the cutoff, so a calendar with a lunch break still deals at the day's
   last close.
2. **The hold is ADR-0060's.** A fund's order is annotated with the cutoff as
   the instant it may first meet the market. A latency the run also models
   holds it no less long. `_fill_working_orders` works it at the fund's first
   event at or after that instant, and fills it in full whatever size the
   price shows: a NAV deals every unit asked for.
3. **Refused before an order exists**, with the reason:
   - an order other than a market order good till cancelled;
   - an order under algorithmic routing;
   - a fund whose venue declares no calendar, or has no dealing session
     ahead;
   - a redemption of more units than are held, less those already being
     redeemed.

   A package with a fund among its legs is refused whole.
4. **What it costs a run without a fund.** `InstrumentRegistry` indexes its
   funds (`funds`), as it does its FX pairs. Every fund check reads the index
   first, and the record behind either index is read by one keyed read.
5. **Tests.** `tests/integration/test_funds_and_etfs.py` holds, by hand:
   - an ETF filling at its own quote;
   - a fund order placed before the cutoff dealt at that evening's NAV, not at
     the last price when it was placed;
   - one placed after the cutoff dealt at the next day's NAV;
   - a redemption beyond the units held refused;
   - a day order, a limit order, a package's leg and an undeclared dealing
     calendar refused.

# Compatibility

- Additive: two `AssetType` members appended, and a registry index of its
  funds, derived. No identity, digest or persisted byte moves for any run,
  registry or record that declares neither.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- A book can hold ETFs as what they are, and a researcher can limit or report
  them apart from shares.
- A fund-of-funds or a model portfolio of mutual funds can be backtested at
  the NAVs its orders would have dealt at.
- Not here: swing pricing, dilution levies, redemption gates and suspensions,
  and creation and redemption.
