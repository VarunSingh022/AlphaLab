# Engine Integration Audit (phase 6)

Read after FUT-041 and FUT-043 were delivered and FUT-042's options written up
(ADR-0052), on the development branch after the phase 5 checkpoint at
`c348e69`. It continues [`ENGINE_INTEGRATION_AUDIT_PHASE5.md`](ENGINE_INTEGRATION_AUDIT_PHASE5.md)
on three questions the phase 6 brief asks:
- whether the host-application boundary is complete enough to integrate
  against;
- whether run evidence identifies every economic input it rests on;
- what the research engine supports beyond an extension interface.

Every finding was checked in the code before it is called a gap. The classes
are those of [`STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md):
1 correctness-critical; 2 a material missing capability; 3 an external
integration boundary; 4 an accepted limitation; 5 a future enhancement; 6
already implemented or resolved. Which release carries this work, and whether
the entries it proposes are accepted at the horizons proposed, are the
maintainer's decisions.

## 1. The host-application boundary

Phase 5 classified every integration point (interface, tested adapter,
production connection, synthetic input, verified convention); nothing there
changed except what a live session can now be given. What a live session can
receive as it happens, against what must still be declared before it starts:

| What the venue or a vendor publishes | Live delivery | Recorded, replayable | Evidence |
| --- | --- | --- | --- |
| Market data (bars, quotes, ticks, books) | `LiveSession.advance` | the run's record | `test_paper_and_live_sessions.py` |
| Observations (news, macro, sentiment) | `LiveSession.deliver_observation` | `RunSchedule.of(observations=)` | `test_heterogeneous_inputs_on_every_driver.py` |
| Volatility surfaces | `LiveSession.deliver_surface` (LIV-002) | `RunSchedule.of(surfaces=)` | same |
| Auction books and indications | `LiveSession.deliver_auction` (FUT-043) | `RunSchedule.of(auctions=)` | `test_live_auctions.py` |
| Fills, cancels, amendments, cash flows | `LiveSession` routing and `apply_cash_flow` | the run's and the broker mirror's record | `test_real_execution.py` |
| **Halts, price bands, band references** | **none: declared on the registry before the run** | -- | FUT-044 |
| **Instrument terms, borrow, margin terms** | none: a changed registry between sessions, declared (`registry_changed`, FUT-041) | the payload's registry digest | `test_registry_on_resume.py` |

**What a host's adapter must supply**, in addition to phase 5's list:
- **Rate limits and backoff.** AlphaLab's library has no rate-limit
  contract. Its reference adapter (`tests/reference_adapter`, in the test tree)
  retries a retryable status and never repeats a refusal. A host's adapter
  must respect its venue's limits and say whether a response was a refusal or
  an absence.
- **Reconnection.** The streaming source reconnects within its bounds,
  removes duplicate records and treats a silent venue as dead
  (`test_streaming_market_data.py`). The live session refuses a repeated
  fill. After a reconnect, the adapter reconciles: the order and position
  snapshots compared by `StateReconciliation`.
- **Arrival instants.** Each observation, surface and auction publication is
  given to the session when it arrived. The session stamps it so, and
  refuses one claiming to be known later.
- **Missing data.** A gap is not filled in. A strategy reads what arrived, a
  view what was published, and a margin check reads no stale surface.
- **Session transitions.** Each venue's calendar, zone and DST are declared
  (`VenueCalendars`); a venue's halt as it happens cannot yet be delivered
  (FUT-044).
- **Symbol mapping.** Provider symbols and dated aliases live on the
  registry; `SymbolMapping` joins a venue's symbols at reconciliation.

AlphaLab still ships no production connection and no verified market
convention, and nothing here says otherwise.

## 2. Economic inputs and the evidence that names them

| Input | How it reaches a run | Identified by | In a default manifest |
| --- | --- | --- | --- |
| Instrument records and their contract, settlement, margin, borrow and tick terms; classifications and aliases | the registry, a live object | `registry_digest`: a manifest when asked (REP-006); a paused replay's and a live run's payload (FUT-041) | **no** (FUT-042, ADR-0052) |
| Margin model and declared surfaces; venue controls; declared auction books and indications | the registry | as above | **no** (FUT-042) |
| Calendars, budgets, risk limits, constraints, account | the pipeline configuration | `configuration_id` | yes |
| Costs, latency, fill policy, sizing model | described in the configuration (PER-008) | `configuration_id`; `ExecutionAssumptions` | yes |
| Queue model; execution plan | the driver | `queue_model`, `execution_plan_id` | yes, when used |
| FX rates, funding, cash flows, corporate actions, payments, deliveries, observations | the schedule | `schedule_digest` | yes **if the producer names the schedule** -- held to it only for observations, surfaces and auctions (REP-009); for the rest, see FUT-046 |
| Delivered surfaces and auction publications | the schedule, or a live session | the run's record (`result_id`), and since REP-009 the schedule a manifest names must hold each | yes |
| The dataset, its revisions and parts | the dataset | `dataset_version`, content hash, parts | yes |
| Strategy code and configuration | the strategy | the fingerprint; the recorded strategy configuration | yes |
| Venue conditions as assumptions | the registry | `ExecutionAssumptions.venue_conditions` (EXE-014), a statement | stated, not identified |

**Every execution path records what it used.** Simulated, algorithmic and
external routing each state their assumptions (`ExecutionAssumptions`). An
external fill is the venue's, recorded as reported. The uncross records its
price, matched volume and imbalance from the book it read (FUT-033 and
FUT-043). Nothing is inferred from an object's type except the registry in a
default manifest, which is FUT-042 and is stated as a finding (REP-007).

**Found and fixed (REP-009).** A manifest could name no schedule, or a
schedule without them, for a run that was delivered volatility surfaces or
auction publications. The run kept them in its record, so the manifest named
less than the run read. `manifest_for_run` now refuses both cases.

**Not detectable, so proposed (FUT-046).** A run given FX rates, cash flows
or corporate actions by schedule keeps their effects, not the entries. A
manifest naming no schedule for it is not refused, and a rerun without the
schedule diverges rather than reproduces. No false claim is possible, but
the manifest does not say what was missing.

## 3. The research engine

Checked against tests, not against the existence of an interface:

| Capability | Evidence | Class |
| --- | --- | --- |
| Researcher-defined strategies from the documented contracts | examples 70, 72, 79; `docs/COMPOSING_STRATEGIES.md` | 6 |
| Features, factors and models on the canonical path, fitted before a boundary and inferred after | example 73; `test_research_cannot_see_the_future.py` and 9 other test files on fitting and look-ahead | 6 |
| Feature and dataset versioning | `alphalab.feature_store` (registry, metadata); dataset versions and content hashes in every manifest | 6 |
| Walk-forward, purged and out-of-sample evaluation | 8 test files | 6 |
| Multi-asset optimisation and risk budgets | `alphalab.portfolio_optimizer`, `alphalab.analytics`; 9 test files | 6 |
| Cross-currency accounting, FX timing | 74 test files touching currencies; matrix 2.27 | 6 |
| Alternative data, fundamentals, macro, sentiment | point-in-time observations (FUT-004, FUT-008); example 72 | 6 for the mechanism; **3** for the data, which is the host's |
| Risk and P&L attribution | 13 test files | 6 |
| Execution quality and market impact | `alphalab.execution.quality`, impact models; 18 test files | 6 for measurement; a calibrated impact model is the researcher's (3) |
| Execution across venues in one simulated run | one `ExecutionSimulator` per run: every listing priced by one cost and latency model; routing across venues not evaluable historically | **2**: FUT-045 |
| Operational monitoring and reconciliation | `live_health`, `StateReconciliation`; 27 test files | 6 |

**Corrected in passing.** The gaps report marked options on baskets, cash in
lieu, quanto consideration and Bermudan exercise as requiring new capability
(D) under FUT-016. FUT-016 delivered them in v4.2.0
(`test_advanced_contract_terms.py`); the row now says so.

## 4. Findings

| Finding | Class | Disposition |
| --- | --- | --- |
| A resumed run's registry checked by type | 1 | **Fixed** (FUT-041) |
| A rerun from a manifest compared without its registry or schedule | 1 | **Fixed** (REP-008) |
| A manifest omitting the schedule that delivered surfaces or auctions the run kept | 1 | **Fixed** (REP-009) |
| Auction books and indications during a live run | 2 | **Delivered** (FUT-043) |
| A default manifest's registry | 4, identity | FUT-042: options in ADR-0052, the maintainer's decision |
| Halts, bands and references delivered during a live run | 2 | FUT-044 (proposed v4.4) |
| Venue-specific execution models in one simulated run | 2 | FUT-045 (proposed v4.4) |
| A run's record naming the schedule it was given | 4, identity | FUT-046 (proposed v4.3) |
| Rate limits, reconnection, real venues and data, verified conventions | 3 | the host's, with the requirements above |
| Depth beyond the top of book | 3 | unchanged (BDY-003) |

Nothing here claims global completeness. What a run is given is identified or
said to be unidentified, what it is not given is refused or stated, and the
venues, data and rules of any real market remain the host's.
