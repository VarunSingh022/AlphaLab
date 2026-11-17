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
