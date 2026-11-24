# ADR-0067: FX Instruments and Value Dates -- a Currency Deal Is Two Currencies

## Status

**Accepted** (2026-11-23), under the owner's delegation of the programme's
decisions within the charter's principles (ADR-0056, 2026-11-10). It is the
record the v4.3.0 plan needs before M10 (plan section G.2 and G.3, engine N15,
gap GAP-23, workstream WS-27, scenario S-05, acceptance GR-3). It reverses no
accepted decision. It narrows how a `FOREX` instrument may be booked, by an
addition a record declares; a record that declares nothing is booked as it
always was.

---

# Context

**An FX trade is booked as units of a pair (GAP-23).** `economics_for` books
a `FOREX` record that declares no economics as a cash equity
(`instrument/economics.py`). Buying a million EUR/USD at 1.08 holds a million
"units" of the pair, valued in USD at its price. It moves 1.08 million USD of
cash and no euros. The account never holds the currency it bought. Its euro
cash, its exposure in euros, and the cash a forward will move on its value
date are all wrong, or absent.

**What the account already models.**
- **Settlement truth is per currency and permanent; reporting converts on
  demand** (nowandfuture section 14, invariant 4). Cash is kept per
  currency, and a converted figure uses a supplied rate with provenance
  (`portfolio/fx.py`, ADR-0020).
- **No triangulation, no implicit inversion, no invented rate (AC-17).** A
  cross goes through a currency the caller names.
- **Trade-date booking with pending settlement (FUT-015).** A fill moves cash
  at its instant. A market whose trades settle later records each fill's
  settlement instant from a declared rule and the venue's calendar, and keeps
  unsettled credits and debits apart from settled cash.
- **An exchange's legs may be delivered after its effective instant
  (FUT-037).** Each undelivered leg is a ledger transaction of its own,
  pending until its delivery instant.
- **Settlement dates count trading days of a supplied calendar**
  (`conventions/settlement.py`). The calendar is taken by the
  `TradingDayCalendar` protocol, so `conventions` imports nothing but
  `common`.
- **Package intents (ADR-0062).** Several legs trade as one order, all or
  none, at one instant.

**What FX needs that nothing states.**
- **Value dates by both calendars.** Spot settles a stated number of
  business days after the trade, counted where both currencies' markets are
  open: T+2 for most pairs, T+1 for some. Pairs against the US dollar, and
  some crosses, also respect the US calendar on the value date. None of this
  is a property of a pair's name, so it is declared, never inferred (plan
  G.3).
- **Forwards** settle at a later value date, either stated outright or as a
  tenor from spot under a business-day convention and an end-of-month rule.
- **Swaps** are a near deal and a far deal in opposite directions. A
  **rollover** is a swap that moves a position's value date.
- **Non-deliverable forwards (NDFs)** deliver no base currency. On a
  declared fixing date they settle the difference between the contract rate
  and a fixing, in a declared settlement currency. The fixing comes from a
  declared source, and is refused when absent or stale.

# Decision

## 1. A declared FX deal books two currencies

- **`FxTerms`** joins `OptionTerms` and `FutureTerms` on `InstrumentRecord`,
  outside its identity key. It declares:
  - the base and quote currencies, each an ISO 4217 code, distinct;
  - the deal's kind: `SPOT`, `FORWARD` or `NDF`;
  - its value-date rule (section 2);
  - for an NDF, its fixing (section 4).
- **A fill of a record that declares `FxTerms` is an exchange of two
  amounts.** Buying `q` at price `p` credits `q` of the base currency and
  debits `q x p` of the quote currency. Selling does the reverse. Each amount
  is rounded once to its currency's minor unit, half-even, under
  `ACCOUNTING_CONTEXT` (AC-18). Each is a cash leg pending until the deal's
  value date, as FUT-015 and FUT-037 already book pending legs.
- **The account holds currencies, not pairs.** A deal leaves no position in
  the pair: what it leaves is cash in two currencies, settled or pending.
  Its value in any currency is reported by converting those balances with
  supplied rates, on demand. Nothing converts at fill time (invariant 4), and
  nothing triangulates (AC-17).
- **A record that declares no `FxTerms` is booked as it always was**, as
  units of a cash equity. Every existing run keeps its identities, digests
  and bytes, so the old booking stays available to any run that wants it
  (WS-27's migration line).

## 2. Value dates, declared and computed in `conventions`

- **`ValueDateRule`** states:
  - the spot lag, in business days;
  - the calendar of each currency;
  - whether the US-dollar rule applies, with the US calendar it reads;
  - for a forward, an outright value date, or a tenor in months or weeks
    from spot, with a `BusinessDayConvention` (following, modified
    following, preceding) and an end-of-month rule.

  None has a default (AC-12).
- **The computation lives in `alphalab.conventions`**, beside day counts and
  settlement rules: the business-day and value-date rules are WS-13's N21
  items, which M10 completes. It takes each calendar by the
  `TradingDayCalendar` protocol, as `settlement_date` does. So `conventions`
  still imports only `common`, and a run's calendars may come from packs
  (ADR-0059) or from the researcher.
- **What the calendars must state is refused when absent.** A currency's
  calendar the rule needs but no one supplied is refused, never assumed open
  (plan G.1).

## 3. Forwards, swaps and rollovers

- **A forward is a deal whose value date is later.** Its legs stay pending
  until then. Before it settles, it is valued as its two pending amounts
  converted at supplied rates, undiscounted. That valuation is stated, and
  discounting at a curve is ADR-0065's, declared when a run wants it.
- **A swap is a package of two deals** (ADR-0062): the near leg and the far
  leg, opposite in direction, traded as one order at one instant, all or
  none. Its legs are ordinary declared FX deals with their own value dates.
- **A rollover is a swap a strategy states**: it closes the near value
  date's exposure and opens the far one's. It moves cash in both currencies
  exactly as its two legs say. The engine rolls nothing on its own.

## 4. NDFs settle the difference, at a declared fixing

- **An NDF declares** its settlement currency, its fixing date (from a stated
  lag before the value date, under the rule's calendars) and the name of its
  fixing source.
- **At its fixing it books one amount.** That amount is
  `notional x (fixing - contract rate)` in the settlement currency, with the
  sign of the deal's direction, pending until the value date. No base
  currency moves at any point.
- **The fixing is a supplied rate.** It is an `FxRate` with provenance, from
  the named source, as of the fixing instant. A missing, stale or
  future-dated fixing is refused, as every conversion's rate is
  (ADR-0020), and the run stops with the reason rather than settle at an
  invented rate.

## 5. What is recorded

- `FxTerms`, `ValueDateRule` and the NDF fixing are persisted with the
  instrument registry, outside the record's identity key, and written only
  when declared. A registry that declares none keeps its bytes and its
  digest.
- Each leg is a ledger transaction with the settlement metadata FUT-015
  already writes. So what is pending at any instant follows from the ledger
  alone, and a restored run rebuilds the same index.
- `execution_assumptions` and a fingerprint gain nothing. FX booking is a
  property of the instrument, not of the execution model.

# Settled at implementation (2026-11-23)

1. **Value dates** (`alphalab.conventions.business_days`).
   - **Two lists of calendars, not a US-dollar flag.** `ValueDateRule` states
     its spot lag and two tuples of calendars: those a counted day must be
     open in, and those the value date must be open in. Section 2's "whether
     the US-dollar rule applies" is stated by what the lists hold:
     - a common USD pair counts in the other currency's calendar alone, and
       settles where both are open;
     - a common cross counts in both its currencies' calendars, and also
       settles on a US business day.

     No rule reads a pair's name. A market whose convention differs states
     its own lists.
   - **Spot** counts the lag in the counted calendars, then rolls forward to
     the first day every settling calendar is open. **A forward** adds its
     tenor to spot, under a stated `BusinessDayConvention` and end-of-month
     rule, against the settling calendars.
   - **Conventions** are ISDA 2006 section 4.12's following, modified
     following and preceding, and `UNADJUSTED`, a stated choice for the dates
     a contract leaves alone. A tenor's months clamp to a shorter month.
     Under the end-of-month rule, a start on its month's last business day
     lands on the last business day of the month reached.
   - **Tests.** `tests/unit/conventions/test_business_days.py` holds each
     convention at a weekend and at a month's end. It also holds:
     - the end-of-month rule, stated and not;
     - a USD pair whose value date the Fourth of July moves, and which
       Thanksgiving the day before spot does not delay;
     - a cross that settles on a US business day;
     - a one-day lag, and forwards from a month's last business day;
     - a `MarketCalendar` read as a calendar;
     - every refusal.
2. **FX terms on the record** (`alphalab.instrument.terms`).
   - **Calendars are named by id.** The registry is persisted, and a calendar
     is the run's, from a pack (ADR-0059) or the researcher. So the record's
     `FxValueDateRule` names each calendar by its `calendar_id`, and the run
     resolves it to section 1's `ValueDateRule` with the calendars it
     declares. A value date that needs an id the run does not declare is
     refused.
   - **The record is the pair, priced in its quote.** A record that declares
     `FxTerms` is a `FOREX` record whose currency is the quote. Its economics
     are a cash equity's or none, so one unit is one unit of the base
     currency.
   - **The rule's shape is checked where it is declared.**
     - Spot states no later date.
     - A forward or an NDF states exactly one of an outright date and a
       tenor, and a tenor states its convention and end-of-month rule.
     - An NDF states its fixing, which settles in one of its two currencies.
       No other kind has a fixing.
   - **Persisted by name.** `FxKind` and `BusinessDayConvention` are written
     by member name and pinned (ledger PER-004). A tenor is written by its
     label and a date in ISO form. A record that declares no terms writes
     `null`, as before.
   - **Tests.** `tests/unit/instrument/test_fx_terms.py` holds each kind on
     its record, every refusal, and the codec's and the registry snapshot's
     round trips.

# Compatibility

- Additive. A new terms kind, value-date rules and business-day conventions
  in `conventions`, an NDF fixing, and two-currency booking for a record that
  declares FX terms.
- No identity, digest or persisted byte moves for any run, registry or
  record that declares none.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- An FX forward books cash in two currencies with a holiday-adjusted value
  date, and the account's cash per currency equals a hand calculation (GR-3).
- A carry book of forwards rolled as swaps can be researched as a strategy
  (S-05).
- A currency-hedged construction across markets can hold its hedges as what
  they are (S-14, with ADR-0065 and ADR-0066).
- Not here: FX options beyond the `options` engine, and vendor rate feeds.
