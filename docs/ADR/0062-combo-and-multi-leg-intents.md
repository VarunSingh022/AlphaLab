# ADR-0062: Combo and Multi-Leg Intents -- A Package Trades as One Order

## Status

**Accepted** (2026-11-17). It is the record the v4.3.0 plan needs before M8
(plan section J, engine N16, gap GAP-26, workstream WS-11). The owner delegated
the programme's decisions within the charter's principles on 2026-11-10
(ADR-0056), and this record is that decision for combo orders.

It adds an intent kind and fields with empty defaults; `StrategyProtocol` is
unchanged. It reverses no accepted ADR.

---

# Context

**Every intent names one instrument.** `Intent` asks for a delta or a target
in one asset. Allocation nets what strategies ask for per instrument and terms
into one order, risk approves each order against the projected book, and the
OMS and the simulator work one asset at a time.

**A pair cannot trade as one order (GAP-26).** A pairs strategy, a calendar
spread or a basket hedge emits one intent per leg. Each leg is sized, approved
and filled on its own, so one leg can fill while the other is refused by risk,
capped by liquidity or blocked by a halt. The book is then left holding half a
trade that was never the strategy's decision. S-02's screen selects a pair, and
the plan's S-02 trades it as a combo.

**The engine works one asset's event at a time.** Two assets' prices are known
together only at an instant both were observed. The run already has that unit:
the **slice**, an instant's records, closed when the next instant's first record
arrives (`RunEngine.close_slice`, `SliceStrategyProtocol`).

# Decision

## 1. A package is an intent

- **One new kind.** `IntentKind.PACKAGE`, appended. A package intent names its
  legs in a new trailing field, `Intent.legs`: a tuple of `Leg(instrument,
  ratio)`. The instruments are bare `asset_id` strings, and each ratio is a
  signed, non-zero `Decimal`. `legs` is empty by default, and empty for every
  other kind.
- **What the fields mean.** `target` is the signed number of packages to trade
  now, so a leg's quantity is `target * strength * ratio`. `instrument` is the
  package's label, chosen by the strategy and recorded, never resolved. `terms`
  are the package's. A market package is the first form.
- **`StrategyProtocol` is unchanged.** A hook still returns `Iterable[Intent]`.
- **Refusals.** A package is refused as a whole, recorded with its reason, when
  it has fewer than two legs, a leg twice, a zero ratio, or terms other than a
  market order's.

## 2. Allocation nets packages, never legs

Packages with the same legs and terms net into one package order across
strategies, each strategy's share kept as any order's is. A package never nets
with a single-instrument intent, even in the same asset, because its legs trade
together and an order's do not.

## 3. Risk approves the package whole

Risk projects the book with every leg and approves or refuses the package as
one decision (AC-30). No leg is approved without the others. A refusal frees
everything allocation reserved for the package.

## 4. The OMS holds a package as linked legs

Each leg is an OMS order carrying the package's identifier in a new field,
written only when set. The legs are accepted together and become terminal
together. A leg is never worked, amended or cancelled alone: a `CANCEL` or
`AMEND` intent naming one leg's instrument is refused and recorded.

## 5. Execution is all or none, at one instant

- **The package fills at a slice close,** at the close of an instant's slice in
  which every leg was priced. Each leg fills at its own price at that instant.
- **Fill timing applies to the package.** Under `SAME_EVENT` it fills at the
  slice it was decided in, if every leg was priced there. Under `NEXT_EVENT`
  and `NEXT_OPEN` it fills at the next slice that prices every leg, a bar leg
  at its open under `NEXT_OPEN`.
- **All or none.** At its turn each leg's fill is decided by the run's fill
  policy, and the package fills only if every leg would fill in full. Otherwise
  no leg fills. A venue block on any leg blocks the package.
- **What is left.** Under `CANCEL`, an unfilled package is withdrawn after its
  turn. Under a resting remainder (ADR-0060) it keeps its turns until its rest
  ends.
- **Costs and venues per leg.** Each leg is priced by the cost model of the
  venue it is routed to (ADR-0061).

## 6. What the strategy sees, and what is recorded

- **Fills and order events.** Each leg's fill reaches `on_fill` as a
  `FillEvent`, and its order events as `OrderEvent`s, each carrying the
  package identifier.
- **The record.** Results, trade records and attribution keep the package
  identifier beside each leg, so a package reconciles leg by leg and as a
  whole.
- **Persistence.** The legs, the package identifier on each leg order and the
  package's working state are persisted in M8's pipeline schema step, 8 to 9,
  written only when a run placed a package. A run that placed none is
  unchanged.

# Settled at implementation (2026-11-21)

1. **Package intents.**
   - **The shape, and its refusals.** `IntentKind.PACKAGE`, `Leg` and
     `Intent.legs` as section 1 states. `validate_intent` refuses only a value
     that is not a package's shape at all -- legs that are not `Leg`s of an
     asset and a finite `Decimal`, or legs on another kind -- and the strategy
     fails as for any malformed intent. What a package asks for is the run's to
     judge: fewer than two legs, a leg twice, a zero ratio, a leg the run has
     not priced, terms other than a market order good for the day or until
     cancelled, a leg off the run's whole units, or routing other than
     `SIMULATED`. Such a package is refused whole and recorded as a
     `PackageRefused` in the strategy runtime's log, a new persisted event
     written only by a run that refuses one; the strategy keeps running.
   - **Where it enters.** With the batch's instructions: after its cancels and
     amends, before its single orders, at every intake a strategy's intents
     reach -- a market event's step, its feedback, a slice, a timer, a stop and
     a child's fill -- so none can skip it. Given a package directly,
     `AllocationEngine.allocate` refuses it, as it refuses every instruction
     that is not one order: sized whole it would read as a delta of its label.
   - **Netting and sizing.** Packages with the same legs, as a set of
     instrument and ratio, and the same terms net across strategies. Each
     netted package is allocated in a call of its own as its legs' deltas,
     `target * ratio` at the intent's strength, under `FixedQuantitySizing`
     whatever the run's sizing model. One call per package means a leg nets
     only with the same leg of the same package. Allocation must place every
     leg exactly as the package states, or the package is refused.
   - **Approved whole.** Risk evaluates each leg in turn against the book with
     the legs before it. A refusal of any returns the state from before the
     package -- every reservation unmade -- and records the refusal.
   - **The section 4 identifier lives in metadata, not a new field.** Each leg
     order carries `package_id` (its first leg's order id), `package_legs`
     (every leg's order id) and `package_label` in its metadata, as ADR-0060's
     remainders and latency do. A run's steps record its orders, and a new
     order field would have moved every run's record; metadata appears only
     on a leg, and every snapshot already carries it. No OMS or pipeline
     schema moves, so section 6's "persisted in schema 9" is settled the same
     way: nothing new is persisted.
   - **Worked only together.** A leg takes no turn at its asset's events,
     neither continuous nor in a reopening auction, and counts in no computed
     auction's interest. A cancel or amend naming it is refused, recorded as a
     `CancelRefused` or `AmendRefused`.
   - **The turn at a slice close.** `ExecutionPipeline.close_slice` works
     every package with a leg in the instant's assets before it delivers the
     slice. `RunEngine.close_slice` calls it when a package is working,
     whether or not a strategy wants the slice, and passes the run's fill
     policy. A run with neither closes nothing, as before. At its turn:
     - a package whose leg ended otherwise -- an expiry, a settlement, a split
       -- ends with it;
     - one past its day expires whole;
     - it waits for an instant at or after the one it was decided in under
       `SAME_EVENT`, or a later one under the other timings and when placed
       from feedback;
     - it waits for an instant that priced every leg, after each leg's latency
       (ADR-0060);
     - a venue block on any leg leaves it resting;
     - otherwise every leg's fill is decided by the run's fill policy at its
       own price there, a bar's open under `NEXT_OPEN`. Only if every leg
       would fill in full do all fill; else none does.
   - **What is left.** Under `CANCEL` an unfilled package is withdrawn after
     its turn, every leg together. Under a `RestRemainder` its first unfilled
     turn begins its rest: that many more turns, or that many seconds.
   - **Feedback and the record.** Each leg's fill and order events carry
     `package_id`, so a strategy sees its package whole; the step's and the
     slice's feedback deliver them as for any order. A snapshot decoder reads
     an older event without the field as its empty default. Results, trade
     records and attribution reach the package through each leg's order id;
     no record type gains a field.
   - **Tests.** `tests/integration/test_package_intents.py` holds
     hand-calculated fills:
     - a pair at one instant, A at 100 and B at 50;
     - the timings, at 101 and 49;
     - a package waiting for an instant that priced every leg, at 102 and 48;
     - a cap filling neither leg, then a resting remainder filling at the
       next instant;
     - risk refusing the package whole;
     - netting of six and four packages into ten, kept apart from an order for
       one leg;
     - a cancel naming a leg refused;
     - every refusal, and the refusal under a plan.

     They also cover the feedback's package id and a replay paused at each
     boundary resuming to the same result.

# Compatibility

- Additive: an appended intent kind, a trailing field with an empty default on
  `Intent`, a `Leg` type, and an optional package identifier on orders.
- No identity, digest or persisted byte moves for a run that places no
  package.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- A pair, a spread or a basket hedge trades as the one decision it is, or not
  at all.
- Risk sees the package it is asked to approve, not half of it.
- S-02's selected pair can be traded as one order.
