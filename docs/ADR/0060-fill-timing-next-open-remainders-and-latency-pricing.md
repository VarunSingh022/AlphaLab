# ADR-0060: Fill Timing at the Next Open, Resting Remainders, Strict Caps and Latency Pricing

## Status

**Accepted** (2026-11-17). It is the record the v4.3.0 plan needs before M8
(plan section J, gaps GAP-18, GAP-19, GAP-38 and GAP-53, workstream WS-11, and
the delegated decision D-V4). The owner delegated the programme's decisions
within the charter's principles on 2026-11-10 (ADR-0056), and this record is
that decision for fill timing, remainders, caps and latency.

It amends AC-29 (ADR-0014 decision 3, ADR-0021) for runs that declare a resting
remainder, and changes nothing for runs that do not. It reverses no accepted
ADR.

---

# Context

**How a simulated order fills today.** `ExecutionPipelineConfig.fill_timing`
has two members:
- `SAME_EVENT`, the default, fills at the price of the event that decided the
  order. No real order trades at the price that triggered it, and the run's
  `execution_assumptions` names this as optimistic.
- `NEXT_EVENT` leaves the order working until its asset's next event and fills
  it there, before the strategy is dispatched. For bars that is the next bar's
  **close**: one bar of delay, and the price at the end of it.

A daily strategy that decides at today's close and trades at tomorrow's open
cannot say so. It can declare an `OPG` order, which fills at the next daily
bar's open, but only on daily bars, and only order by order (GAP-18).

**A partial fill's remainder is cancelled in the same step** (ADR-0014
decision 3, AC-29). Under `LiquidityCappedFill` partial fills are routine, so a
large order trades one slice and the rest disappears. A capped policy given an
event with no size fills in full, the most optimistic outcome there is
(GAP-38).

**Latency moves only a timestamp.** A latency model stamps the fill later; the
price is still the deciding event's. A strategy whose orders take ninety
seconds to arrive is measured as if they arrived at once (GAP-19).

**The defaults are optimistic, and every one is stated** (GAP-53): same-event
timing, a frictionless cost model, fills on touch, and unlimited liquidity.
`execution_assumptions` names each, but nothing helps a researcher state the
conservative alternative in one place.

# Decision

Every change below is **opt-in**. A run that declares none of them keeps its
`result_id` and `configuration_id`, its snapshot bytes and its certificate
(AC-06). The default fill timing stays `SAME_EVENT` throughout 4.x (D-V4); 5.0
makes `fill_timing` required.

## 1. `FillTiming.NEXT_OPEN`

A third member, `NEXT_OPEN = "next_open"`, appended to `FillTiming`.

- **It works the order as `NEXT_EVENT` does.** An accepted simulated order works
  until its asset's next market event, and has its first turn there, before the
  strategy is dispatched.
- **At a bar, a market order fills at the bar's open,** where `NEXT_EVENT`
  fills it at the close. That is the one thing `NEXT_OPEN` changes. Every other
  kind already meets the next bar from its open under `NEXT_EVENT`. A held
  limit order's first turn is a resting turn over the whole bar: it fills at
  the bar's better open, or at its limit when the bar trades through it. A stop
  is triggered by the bar's range, and fills at the price that reached it. An
  order sent after the close arrives at the open, and rests through the bar
  that follows.
- **A gap is a gap.** A bar that opens away from the previous close fills a
  market order at its gapped open, which is what an order sent overnight meets.
- **At a quote, trade or tick, the turn is `NEXT_EVENT`'s.** Those events have
  no open.
- **The fill is recorded at the instant of the event that delivered the bar,**
  as an `OPG` auction fill already is. The run keeps one clock: a fill is never
  stamped before an event the run has already processed for another asset.
  The price is the open, which was knowable when the bar opened, after the
  order was decided.
- **Auction orders are unchanged.** An `OPG` or `CLS` order keeps its own path.

## 2. The intraday opening rule

With a calendar declared for an order's listing venue
(`ExecutionPipelineConfig.calendars`), a bar whose interval does not lie within
one of that venue's sessions does not give a `NEXT_OPEN` order its turn: a
pre-market or post-market bar passes it by, and the first bar within a session
fills it. So an order decided after a session's last bar fills at the next
session's first bar's open. A daily, weekly or monthly bar is a session bar and
is always in session. Without a calendar, every bar is in session, as every
run assumes today. An order that declares `OPG` goes to the opening auction by
its own path.

## 3. `RemainderPolicy`

A run-level policy for what a partial fill leaves, on
`ExecutionPipelineConfig.remainder`:
- **`CANCEL`, the default:** today's rule. The remainder is cancelled in the
  step that partially filled it, and its reservation is freed (ADR-0014
  decision 3).
- **`RestRemainder(events=n)` or `RestRemainder(seconds=s)`:** exactly one is
  stated. The remainder keeps working, with a turn at each of its asset's
  following events, for `n` of them or until `s` seconds after the partial
  fill. It is then cancelled, and what it holds is freed. Its turns are the
  order's own kind: a market remainder makes a market attempt at each event.

Only an order that would otherwise be cancelled for its remainder rests. A
limit order's remainder already rests at its limit. An immediate-or-cancel or
fill-or-kill order is withdrawn after its first turn, whatever the policy.
Under a resting remainder, AC-29 reads: a fresh order per event, and a partial
remainder cancelled when its declared rest ends.

## 4. Strict caps

A new policy, `StrictLiquidityCappedFill(participation_rate)`. Given an event
with no size it **refuses**, raising `ExecutionValidationError` that names the
asset and instant: a run that declared a cap cannot apply it to data without
sizes, and is told so instead of filling in full. Otherwise it decides as
`LiquidityCappedFill` does. It is a new class rather than a field on
`LiquidityCappedFill`, because a run's description writes every field of its
fill policy, and a new field would move every existing capped run's identity.

## 5. `LatencyPricing`

A run-level choice on `ExecutionPipelineConfig.latency_pricing`:
- **`STAMP`, the default:** today's rule. The latency model moves the fill's
  timestamp and nothing else.
- **`POST_LATENCY_EVENT`:** an order cannot meet the market before its latency
  has passed. Its first turn is its asset's first event at or after the
  decision instant plus the latency model's draw for the order. That event
  prices the turn as the run's fill timing prices it: at the open of a bar
  under `NEXT_OPEN`, at the event's price otherwise. Events before then pass
  the order by. The fill is stamped at the event, since the latency was spent
  waiting for it. A latency longer than the bar interval therefore fills at a
  later bar's price, which is the plan's acceptance.

## 6. Conservative choices, stated by builders

`alphalab.runtime.assumptions.conservative_execution` returns a pipeline
configuration and a fill policy that state the conservative choice in every
role the researcher has not:
- `NEXT_OPEN` fill timing;
- `POST_LATENCY_EVENT` latency pricing;
- a `StrictLiquidityCappedFill` at the stated participation rate.

It takes the cost model, the participation rate and the latency model as
required arguments, and refuses a frictionless cost model. A builder that
invented costs would put a number nobody chose into a run. It changes no
default. Its choices appear in `execution_assumptions`, whose `optimistic` is
empty for its output.

## 7. What is recorded

- `execution_assumptions` gains `remainder` and `latency_pricing`. Each appears
  in `settings()` only when declared, as `queue_model` does, so an undeclared
  run is identified as before. `fill_timing` and the fill policy's description
  carry `NEXT_OPEN` and the strict cap.
- The pipeline snapshot writes `remainder` and `latency_pricing` only when they
  are not the defaults. M8's persisted additions share one schema step, 8 to 9,
  written only when a run declares one of them; ADR-0061 adds the venues to
  it. A run declaring none writes version 8, byte for byte.
- `FillTiming.NEXT_OPEN`, `LatencyPricing`'s members and `RestRemainder` are
  persisted names, held by the persisted-name test.

# Settled at implementation (2026-11-17)

1. **`NEXT_OPEN` and the opening rule.**
   - **Held as `NEXT_EVENT` holds.** Both timings hold every simulated order to
     its asset's next event (`_HELD_TIMINGS`). A market order's turn at a bar of
     its asset takes the bar's open as its price (`_opening`). Every other turn,
     and every other order kind, is `NEXT_EVENT`'s.
   - **Out of session.** With a calendar declared for the asset's listing
     venue, or a default one, an intraday bar is in session when one window of
     the trading day its interval starts in holds the whole interval, from its
     start to its stamp. A bar out of session gives no order a turn and moves
     no queue, but an order whose expiry it reaches still expires. A day limit
     order decided in session therefore expires at the close, as it does today.
   - **Plan children are not governed by it.** Under `ALGORITHMIC` routing the
     parents go to the plan and the children are worked at the events their
     algorithm schedules, as under `NEXT_EVENT`.
   - **How the book values the fill.** A fill at the open values its position
     at the open until the next event, of any asset, re-marks it at the market
     price, as every fill priced away from its event's mark already is: a
     resting limit's, an `OPG` auction's. The book re-marks lazily (PRF-001),
     and changing that would move existing runs' identities.
   - **Schema 9.** The pipeline snapshot writes version 9 exactly when a run
     declares an M8 option, today a `NEXT_OPEN` fill timing, and version 8
     otherwise. It reads version 8 through an identity step. A payload whose
     declared version is not the one its own configuration implies is refused,
     so one state has one payload.
   - **Tests.** `tests/regression/test_next_open.py` holds hand-calculated fills:
     - at daily bars: 103 at the open, where `NEXT_EVENT` fills at 105 and
       `SAME_EVENT` at 100;
     - at one-minute bars across a session boundary: 101 at the next
       session's first open with the calendar, and 99 at the post-market bar
       without it;
     - limit and quote turns identical to `NEXT_EVENT`'s;
     - expiry on an out-of-session bar;
     - the assumptions;
     - a resume at version 9.

     The independent ledger now holds daily bars under all three timings, each
     fill's price held to its timing's rule
     (`tests/regression/test_accounting_matches_an_independent_ledger.py`).

# Compatibility

- Additive: a new `FillTiming` member, two new configuration fields with
  defaults that are today's behaviour, two new types and a builder.
- No identity, digest or persisted byte moves for a run that declares none of
  them.
- A payload written with any of them is version 9, which v4.2.1 refuses, as it
  refuses any payload newer than itself.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- A daily strategy can be measured at the next open in one declaration, and an
  intraday one across a session boundary.
- A capped run can keep working its order instead of discarding what one event
  could not fill, and cannot silently fill in full on data without sizes.
- Latency can cost what it costs.
- The conservative run is one call, and the optimistic default stays stated
  until 5.0 removes it.
