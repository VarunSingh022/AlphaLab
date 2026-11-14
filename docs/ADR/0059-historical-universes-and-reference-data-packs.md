# ADR-0059: Historical Universes and Reference-Data Packs -- Membership as It Was Known, and Data as Evidence

## Status

**Accepted** (2026-11-14). It is the second record the v4.3.0 plan needs before
M3 (plan sections G.1 and G.2, engine-register items N10 and N23, workstream
WS-13, gap GAP-43). The owner delegated the decision to its milestone on
2026-11-10, and this record is that decision (`docs/work/v4.3.0`, TD-16).

It amends no accepted ADR. It settles, for membership, the question ADR-0026
deferred and `UniverseView` already answered: what an instrument universe is
belongs to the instrument registry. It keeps BDY-013 for the engine -- the
engine ships no market data -- and gives that data a format to arrive in.

---

# Context

A backtest over an index's constituents is only as honest as the list of
constituents it reads. Two failures are routine, and both make a strategy look
better than it was:

- **Survivorship.** Today's members applied to the past leave out every company
  that was removed or delisted -- the losers -- and include every company before
  it was added.
- **Look-ahead.** An addition announced on 1 June and effective on 15 June is
  known on 1 June, not on 1 January. A list of effective dates alone lets a
  strategy act on it early.

AlphaLab has neither problem only because it has no dated membership at all.
`UniverseView` is "the instruments this run may trade, as its registry declares
them", and the registry declares instruments, not their membership of anything
over time. `DatedAlias` already dates a symbol over `[valid_from, valid_to)`.
The registry already reads corporate adjustments point-in-time, keeping those
announced by the view's instant. Neither dates membership (GAP-43).

Membership comes from an index provider or an exchange, and AlphaLab ships no
such data (BDY-013). The plan's policy (section G.1) is that mechanisms live in
the engine and data in versioned packs, each entry evidence of where it came
from. Nothing yet says what a pack is.

# Decision

## 1. A membership change is dated twice: when it takes effect, and when it was known

`instrument.membership` holds `MembershipChange(universe, asset_id, kind,
effective_at, announced_at)`:

- `kind` is `ADDITION`, `REMOVAL` or `DELISTING`;
- `effective_at` is the first instant the change holds;
- `announced_at` is when it became known, at or before `effective_at`.

A `HistoricalUniverse` is a universe's changes in order. It refuses:

- an asset removed or delisted while not a member;
- one added while already a member;
- a change announced after it took effect. Such data was written later, and
  reading it as known earlier is the look-ahead this record exists to prevent;
- two changes to one asset at one instant.

`members(at, *, as_of)` is the set that holds at `at`, as known at `as_of`: it
reads only the changes announced at or before `as_of`. Reading the past with
today's knowledge is allowed and says so (`as_of` after `at`). Reading the future
with today's knowledge is what an announced change makes possible, and is
allowed for the same reason.

## 2. A strategy reads membership through its universe, as known at its now

`InstrumentRegistry` carries the run's historical universes beside its
instruments, because the registry is the authority on what a universe is
(`UniverseView`, ADR-0026). `context.universe.members(universe, at)` answers with
`as_of` fixed at the context's instant, so a strategy can never read a change
before its announcement. A registry without a historical universe of that name
refuses the question rather than answering with its whole instrument list.

The registry's digest covers the universes it carries, so a run's provenance
(ADR-0053) names the membership it traded against. A registry without them
digests exactly as before.

## 3. A survivorship-biased control is caught where the bias arises

`research.bias.membership_review(trades, universe)` compares a run's trades
with the membership as it was known at each trade:

- a trade in an asset that was not a member then;
- a trade in an asset whose addition had not yet been announced.

Each is reported with its instant. A control run against today's survivors,
applied to the past, trades companies before they joined. The review names those
trades, and the measured difference between the control's return and the
point-in-time run's is the bias. This is the plan's GR-5, and its acceptance
test runs exactly that pair. The review scores nothing (RES-001): it lists what
happened.

## 4. A reference pack is data with its evidence, in one canonical format

`data.packs.ReferencePack` is the format N10 needs at M3; its loader and the
data distributions themselves are M10's. A pack holds:

- `pack_id`, `version`;
- the source: the publisher and the document it was taken from;
- `as_of`: when the data was taken;
- the validity range it claims;
- a licence note;
- its entries, typed. The first entry type is the `MembershipChange`.

Its content digest is SHA-256 over the canonical serialization of the entries,
under the scheme `alphalab.reference_pack.v1`. A pack whose entries do not
match its stated digest is refused. A query outside its validity range is
refused, and a missing entry is refused, never assumed (plan G.1). A missing
holiday would otherwise be an extra session.

A pack is evidence, not truth. A run that used one cites its `pack_id`, version
and digest beside its record, as ADR-0053's provenance does for a schedule. It
is never assumed authentic because its digest matches.

# Departures from the plan's text, and why

- **The query is `members(universe, at)` on the universe view, with `as_of`
  the context's instant** (plan N23: `context.universe.members(at)`). A run may
  carry several universes, an index and its sector sub-indices, so the
  universe is named. `as_of` is not the strategy's to choose, or it could
  choose the future.
- **Delisting ends membership; it does not yet settle a position.** A
  `DELISTING` change ends an asset's membership. Cashing out a position held
  through a delisting is execution's, as a scheduled settlement is, and research
  keeps its delisting returns (`factor_library`). Wiring a delisting into a
  backtest's positions is recorded as FUT-050.

# Compatibility

- Additive. A registry without historical universes digests, snapshots and
  answers exactly as before, and `UniverseView`'s existing answers are
  unchanged.
- New names: `instrument.membership` (`MembershipChange`, `MembershipKind`,
  `HistoricalUniverse`), `UniverseView.members`, `research.bias.membership_review`,
  `data.packs.ReferencePack`.
- No `result_id`, `configuration_id`, digest or persisted format moves for any
  run, registry or snapshot made before.

# Known limitations

**Known limitations**

- **A delisting does not settle a held position.** Membership ends at a
  delisting; the position's cash-out is a scheduled settlement the run is given,
  until a delisting is wired into positions.
- **AlphaLab ships no membership data.** A pack's format and its checks are the
  engine's; the data is a distribution's or the researcher's (BDY-013).

# Consequences

- `instrument` gains dated membership, and the registry carries it.
- `research.bias` gains a review that names a survivorship-biased run's trades.
- `data` gains the pack format a later loader reads.
- A backtest over an index can be held to its constituents as they were known,
  and a control that is not is caught by what it traded.
