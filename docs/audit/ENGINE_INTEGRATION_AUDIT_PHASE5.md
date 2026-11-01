# Engine Integration Audit (phase 5)

Read after FUT-037 to FUT-039 were delivered, on the development branch after
the phase 4 checkpoint at `cde89f6`. It answers four questions the phase 5
brief asks of the engine as a whole: whether a run's instrument terms are part
of what identifies its experiment, what of each external feed and venue is
actually integrated, how much of execution is data and how much is model, and
what else stands between the engine and global use. Every finding was checked in
the code before it is called a gap ([`STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md),
whose classes this uses: 1 correctness-critical; 2 a material engine capability
gap; 3 an explicit external boundary; 4 an accepted limitation; 5 a future
enhancement; 6 already implemented or resolved).

Which release carries this work, and whether the entries it proposes are
accepted at the horizons proposed, are the maintainer's decisions.

## 1. The instrument registry and a run's identity

**What changes a result.** A run's `InstrumentRegistry` holds, per instrument,
its record (asset type, venue, currency), economics (multiplier, settlement
model, lot), option and corporate-action terms, settlement convention, margin
terms, borrow terms and tick grid, its classifications, dimensions and dated
aliases; and, for the account, a margin model -- with its declared surfaces or
the feed it reads (FUT-039) -- and the venues' trading controls: bands, band
rules and references, halts, auction uncross rules, published auction books and
indications (FUT-040). Every one of them changes what a run does wherever it
binds: what may be traded and how much, what a fill costs, what is refused, what
is margined and liquidated, what a strategy is shown.

**How each is represented.** The record a run keeps -- and so `result_id`, its
SHA-256 -- holds what the registry *did*: every fill, refusal, margin call and
event. The run's configuration record names the registry by type alone
(`ConfigRecord.instruments_type`); the sizing model, simulator and fill policy,
by contrast, are described in full (PER-008), so two runs differing in a cost or
fill parameter are two configurations. The registry snapshot
(`alphalab.instrument.snapshot.capture`) is complete: every record field but the
derived `asset_id` is carried, and the registry's indexes are rebuilt from the
records on restore (checked field by field against `InstrumentRecord` and
`InstrumentRegistry`).

**What a manifest identifies.**

| Manifest | Terms that changed the result | Terms the record does not show |
| --- | --- | --- |
| `manifest_for_run(...)`, the default | told apart: `result_id` differs; a rerun under them is `DIVERGED`, its cause unnamed | **not told apart**: the same manifest; a rerun is `REPRODUCED` |
| `manifest_for_run(..., instruments=True)` (REP-006) | told apart, and a rerun is `INPUTS_DIFFER` naming `instruments` | told apart: a rerun is `INPUTS_DIFFER`, "nevertheless matched" |

`tests/integration/test_registry_identity.py` pins each cell with genuine reruns
of example 72 under other registries, and shows that every kind of term above
moves the registry's digest while a registry rebuilt from its snapshot does not.

**Is `instruments=True` enough?** For the registry, yes: it is the digest of
the complete snapshot, read off the run, and `verify_manifest` recomputes it.
It is not the default, so a manifest asked for nothing new still names the
registry by type alone. Making it the default, or reporting its absence as a
`manifest_gaps` entry, would change what existing calls return -- every default
manifest of a run holding a registry would identify something new, and every
`REPRODUCIBLE` certification of one would stop passing -- so neither was done.
What was done (REP-007): `assess_reproducibility` reports it as a *finding*, a
fact that changes none of its outcomes, and certification carries it among its
limitations. Promoting it is FUT-042, the maintainer's decision.

**A resumed run.** `restore` checks the registry it is given back by type alone,
by ADR-0027 decision 5 -- written when the registry held records and
classifications and the sizing model and simulator were checked by type too.
Since then the simulator and sizing model are checked by description (PER-008),
and the registry holds margin, controls, borrow and surfaces; a run resumed
under different terms continues silently. A refusal would also refuse a live
host's legitimate reference-data update between sessions, so this is a design
decision for the maintainer: FUT-041.

**Migration, if the maintainer promotes it.** Old manifests keep verifying --
`canonical_manifest_key` renders the registry digest only when present -- but a
default manifest of the same run would get a new `manifest_id`, so a store keyed
by manifest identity would see a second key for one experiment. The
migration is to record the digest under a new manifest version and state which
version a store's keys were made under; nothing published needs rewriting.

## 2. External feeds and venue integration

Each integration point classified: an *interface* is a contract with no venue
behind it; a *tested adapter* is code exercised end to end against a counterpart
in the test suite; a *production connection* is one to a real venue or vendor;
a *synthetic input* is data the tests or examples construct; a *verified
convention* is a market's rule shipped and checked against its source.

| Integration point | What AlphaLab ships | Class | Evidence |
| --- | --- | --- | --- |
| Venue execution contract | `BrokerProtocol` (`submit_order`, `cancel_order`, `replace_order`, `apply_execution`, `order_status`, `account`, `positions`, `heartbeat`, `connect`), `VenueEvent`, the capability model, `BrokerAdapter` | interface | `alphalab/broker/protocol.py`, matrix 2.24 |
| Paper venue | `PaperBroker` | tested adapter, in process; its fills are synthetic | `tests/integration/test_paper_and_live_sessions.py`, the consumer contract |
| An HTTP venue | `tests/reference_adapter` (`HttpVenueTransport`): signed requests, idempotent client order ids, retries | tested adapter, **in the test tree only**, against a local server (`tests/integration/venue_server.py`) over a real socket; the venue is fictitious | `tests/integration/test_real_execution.py` |
| A real broker or exchange | none | production connection: **none**, by design (BND-001, ADR-0046) | ROADMAP *Community* |
| Live session | `LiveSession` (advance, route, amend, cash flows, observations, and since this audit surfaces) | interface, tested with the paper venue and the reference adapter | `tests/regression/test_heterogeneous_inputs_on_every_driver.py` |
| Market-data transports | `HttpTransport`, `WebSocketTransport` (RFC 6455), `StreamingSource` | tested adapters against local servers (`tests/integration/stream_server.py`); no vendor endpoint shipped (ADR-0045) | `tests/integration/test_streaming_*` |
| Provider history | `BarHistoryProvider`, `ProviderHistorySource`, `normalize_wire_bars` | interface and normalization adapter; inputs synthetic | `tests/integration/test_provider_source_session.py` |
| Files and vendor exports | CSV and row ingestion, `ingest_observations`, `RawSource` provenance | tested adapters for the caller's files; every shipped file is synthetic | example 72 |
| Information during a run | `ObservationDelivery`, `RunSchedule`, `ObservedSurface` (FUT-039) | interface for what the host supplies, point in time; inputs synthetic | `test_surface_feeds.py`, example 72 |
| Reconciliation | `StateReconciliation`, `SymbolMapping` | tested against the paper venue and the reference adapter's snapshots | `tests/integration/test_real_execution.py` |
| Calendars, settlement, lots, ticks, day counts | `MarketCalendar`, `TickSchedule`, `InstrumentEconomics`, `DayCount` | declared by the caller; **no market's rules shipped as verified** -- a MIC in the docs is an example (BDY-013) | ROADMAP *Deliberate boundaries* |
| Venue controls | bands, band rules, halts, auctions, published books and indications | declared inputs; synthetic in every test and example | `alphalab/instrument/controls.py` |

**What an adapter must supply.** For execution: an implementation of
`BrokerProtocol` translating the vendor's protocol both ways, its events as
`VenueEvent`s, the derived client order id as the venue's idempotency key, a
clock, and reconnection followed by reconciliation. For data: wire records
with their provenance and knowledge instants, delivered when they arrived
(`deliver_observation`, `deliver_surface`), never stamped earlier. For
conventions: the market's calendars, settlement, lots, ticks, bands and halts,
from the market's own publications. None of this is in AlphaLab, and nothing
here says a live integration exists because an interface does.

**Finding fixed in passing (LIV-002).** FUT-039 gave `RunEngine` a surface
delivery and no live session call: a live host would have reached into
`state.run`. `LiveSession.deliver_surface` records a surface as known when the
session received it -- a replay of the record then reads it from the same
instant -- and refuses one claiming to be known later.

## 3. Market depth and execution realism

| What shapes a fill | Data, validated model or synthetic assumption | Where it is stated |
| --- | --- | --- |
| Prices, bars, quotes, trade prints | data the caller supplies | the dataset's provenance and version |
| Spread (`QuotedHalfSpread` reads the quote; `FixedHalfSpread` assumes one) | data, or a declared assumption | `ExecutionAssumptions.costs`, the configuration |
| Impact (`LinearImpact`, `SquareRootImpact`) | a model with the caller's coefficients; not calibrated by AlphaLab | `ExecutionAssumptions.costs`, the configuration |
| Commission, fees, tax | declared schedules | `ExecutionAssumptions.costs` |
| Latency (`ConstantLatency`, `DeterministicLatency`) | a declared assumption | `ExecutionAssumptions.latency` |
| Participation cap (`LiquidityCappedFill`) | a declared share of printed volume | `ExecutionAssumptions.fill_policy` |
| Fill timing | a declared rule; `SAME_EVENT` named optimistic | `ExecutionAssumptions.optimistic` |
| Queue position (`PriceTimeQueue`) | a model over the caller's queue observations; without one, named optimistic for passive orders | `ExecutionAssumptions.queue_model`, `passive_fills`, the manifest |
| A reopening auction | the venue's published book as the caller declares it, or the first print | **was stated nowhere a result's reader looks**; now `venue_conditions` |
| Bands, halts, borrow, margin, ticks, settlement | declared venue, lender and account terms | **was stated nowhere a result's reader looks**; now `venue_conditions` |
| Depth beyond the top of book | not modelled: no book is walked, no depth inferred | boundary (BDY-003) |

**Finding fixed (EXE-014).** A result's `ExecutionAssumptions` described its
costs, latency, fill policy and queue model, but not the registry's venue
conditions -- a computed uncross from a declared book, a band, a halt, a
lender's terms, a margin model -- so a reader of the results could not tell
that execution rested on them. `ExecutionAssumptions.venue_conditions` now
states each, one sentence apiece, as the caller's declaration not verified
against a venue. It is a statement, not a setting, so no fingerprint or
manifest identity moves; the manifest identifies the terms themselves with
`instruments=True` (section 1).

## 4. The broader gap audit

| Finding | Class | Disposition |
| --- | --- | --- |
| A manifest asked for nothing new names the registry by type alone, and no assessment said so | 1 | **Fixed** (REP-007): an assessment finding; promoting it is FUT-042 |
| A result's execution assumptions omitted its venue conditions | 2 | **Fixed** (EXE-014): `venue_conditions` |
| A live session had no surface delivery | 2 | **Fixed** (LIV-002): `LiveSession.deliver_surface` |
| A resumed run checks its registry by type alone | 4, design | FUT-041, **delivered in phase 6** |
| A default manifest's registry, as a gap or by default | 4, identity | FUT-042 (proposed v5.0): the maintainer's decision |
| Auction books and indications delivered during a run, for a live session | 5 | FUT-043, **delivered in phase 6** |
| A strategy's view of a reopening auction's published information | 2 | FUT-040, **delivered in phase 5** |
| A real venue, vendor or market's verified rules | 3 | unchanged (BND-001, BDY-013) |
| Depth beyond the top of book | 3 | unchanged (BDY-003) |
| Execution costs, latency, fill and queue models identified | 6 | PER-008, FUT-011 |

Nothing here claims global completeness. What is declared is applied and, since
this audit, stated beside the results; what is not declared is refused or
stated; the rules, data and venues of any real market remain the host's.
