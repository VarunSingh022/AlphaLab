# ADR-0016: Instrument Identity and Resolution Authority

## Status

Accepted (v2.7.0).

Resolves the unstated identity rule introduced by `8cc9a55` ("feat(core):
implement immutable domain models") and never revisited by ADR-0008, which
consolidated the canonical execution types without examining the identity
constraint they carry. Depends on ADR-0011 for the canonical market data model
and ADR-0012 for the broker boundary. ADR-0015 listed "a security master" among
the things not in its scope; this ADR supplies the identity half of that, and
deliberately not the classification half.

**Amended by ADR-0027 (v2.11.0)**, which supplies the classification half. Two
statements below are superseded and nothing else is: testing invariant 9's
second clause — "`TradeRecord.sector_id` remains `None`" — now holds only when
no configured registry classifies the asset; and the non-goal "Sector
classification data, or any sector value" is superseded for the *mechanism*
alone, and remains in force for shipping any taxonomy or reference data. N5's
exclusion of `sector` from the identity key is unchanged and is what ADR-0027
relies on.

---

# Context

`core.Fill.__post_init__` calls `validate_uuid_id(self.asset_id, "asset_id")`,
and `core.Trade.__post_init__` does the same. Every type upstream of them
carries `asset_id: str` with no constraint at all — `Quote`, `Bar`, `Tick`,
`OrderBookSnapshot`, `MarketRecord`, `OrderRequest`, `oms.order.Order`,
`OrderInstruction` and `ExecutionReport`. `AssetId` is a `NewType`, so
`AssetId(report.asset_id)` in `runtime.execution_adapters` is a no-op cast that
enforces nothing at runtime.

`market.validation` validates prices, sizes, spreads and timestamps. It does not
validate `asset_id`.

`SymbolMap.asset_id` is `self.mapping.get(symbol, symbol)`, and `DEFAULT_POLICY`
carries an empty mapping. A provider symbol therefore becomes an `asset_id`
verbatim, which is what `market.normalization` documents as the default identity
rule: "The provider `symbol` becomes `asset_id` verbatim by default."

Executed against the real stack — `StaticTransport` -> `binanceClient` ->
`binanceAdapter` -> `ProviderHistorySource` -> `TradingSession` ->
`ExecutionPipeline`, with the strategy targeting the actually-normalized id:

| Identity policy | Resulting `asset_id` | Result at first fill |
| --- | --- | --- |
| `NormalizationPolicy()` — the documented default | `'BTCUSDT'` | `DomainValidationError` |
| `SymbolMap({"BTCUSDT": "BTC"})` — the shape `test_normalization.py` asserts | `'BTC'` | `DomainValidationError` |
| `SymbolMap({"BTCUSDT": "b1f4c2d0-..."})` — hand-authored UUID | UUID | fills = 1 |

The failure is **late-binding**. Market data, strategy, allocation and risk all
succeed; the refusal fires only at the execution -> core adapter, which is the
last stage. That is why it survived three releases: nothing on the path between
normalization and the fill has an opinion about what an `asset_id` is.

The test suite documents the workaround rather than the defect.
`tests/integration/test_paper_and_live_sessions.py` carries the comment "Asset
ids on the execution path are UUIDs -- `core.Fill` validates them -- so this is
a fixed one rather than a fresh `uuid4()`", and
`tests/integration/test_provider_source_session.py` maps `"BTCUSDT"` to a
hand-authored UUID. `new_asset_id` appears in exactly one test file. Meanwhile
`tests/unit/market/test_normalization.py` asserts that a `SymbolMap` produces
`"AAPL"` — a value the execution path cannot accept. Both tests pass, because
nothing exercises the join.

Identity enters the system from **two** directions, not one: normalization
(`policy.asset_id(symbol)`) and strategy intents (`strategy.events.Intent`,
whose `instrument` is a bare `str`). When the two disagree the run does not
fail — it produces **zero fills**, because allocation never sizes an order for
an asset the market never priced.

No security master exists. `marketdata.SymbolMetadata` (symbol, asset_class,
exchange, currency) and `marketdata.ProviderCatalog` are exported from
`alphalab.marketdata` and are constructed nowhere, including in tests; neither
carries an `asset_id` or a sector.

---

# Decision drivers

- **D1.** `validate_uuid_id` is the only enforced identity invariant on the
  path, and it is what made this defect visible at all. It must not be weakened.
- **D2.** A refusal must occur where the identity is *created*, not where it is
  finally *consumed*. Late refusal is why this survived three releases.
- **D3.** Reproducibility is a first-class AlphaLab property (ADR-0003,
  `DeterministicIdSource`, `id_scope`). Two deployments configured
  independently must agree on the identity of the same instrument, or evidence
  and deployments cannot be compared across environments.
- **D4.** Provider adapters must not learn AlphaLab's identity scheme. v2.5
  deliberately made the adapter layer canonical-agnostic.
- **D5.** No new persisted state and no schema movement. See ADR-0017.
- **D6.** Sector must become *possible* without becoming *required*: v2.6's
  "absent, not fabricated" rule has to survive this ADR intact.

---

# Decision

Introduce `alphalab.instrument`, a new package owning exactly one concept: the
canonical identity of a tradable instrument.

A **canonical `asset_id`** is an opaque, UUID-shaped, AlphaLab-issued identifier
for one instrument. It is not a ticker, not a provider symbol, and carries no
parseable meaning.

It is **deterministic**: `uuid5(ALPHALAB_INSTRUMENT_NAMESPACE,
canonical_instrument_key)`. UUIDv5 output is a valid UUID, so `validate_uuid_id`
accepts it unchanged — verified by execution, not assumed: a derived id passes
`validate_uuid_id` and constructs a real `core.Fill`.

Determinism gives **stability without coordination**. The same declared
instrument yields the same `asset_id` in every environment, forever, with no
shared database and no persisted registry. A registry that minted random UUIDs
would make two independently configured environments disagree about the identity
of the same instrument, and evidence from one could not be compared with
evidence from the other.

**Registration is still required.** Derivation alone would re-create passthrough
with extra steps, and would make every typo a new instrument. `InstrumentRegistry`
refuses to resolve a `(provider, symbol)` pair it was not told about.
Determinism governs *what* the id is; registration governs *whether there is
one*.

## 1. Two identity modes, both named

`NormalizationPolicy` carries an explicit `identity` mode. It is never `None`:
an absent value silently selecting the unsafe behaviour is the shape of the
original defect.

```text
IdentityResolution = InstrumentRegistry | UnresolvedIdentity

UnresolvedIdentity (frozen, slots)
    symbols: SymbolMap = SymbolMap()

UNRESOLVED_IDENTITY = UnresolvedIdentity()      # module-level singleton

NormalizationPolicy
    identity: IdentityResolution = UNRESOLVED_IDENTITY
    provider: str = ""
```

| Mode | Behaviour | Permitted use |
| --- | --- | --- |
| `InstrumentRegistry` | Resolves `(provider, symbol) -> asset_id`; refuses an unregistered pair | **The only mode permitted on any path that can reach a `Fill` or `Trade`** |
| `UnresolvedIdentity` | v2.6 `SymbolMap` passthrough | Low-level unit testing of the wire -> canonical lift **only** |

`UnresolvedIdentity` is a utility mode for testing the wire -> canonical lift in
isolation. It is **not an identity path to `Fill` or `Trade`**: the values it
produces are provider symbols, and `core.Fill` / `core.Trade` will refuse them.
It **cannot be the identity mode of a production `ProviderHistorySource`**, and
`ProviderHistorySource.of` refuses it. A caller who assembles `MarketRecord`s by
hand from `UnresolvedIdentity` output and feeds them to a `TradingSession` is
outside the supported configuration and will fail at the first fill — the v2.6
behaviour, now documented rather than discovered.

`DEFAULT_POLICY` retains `UNRESOLVED_IDENTITY`. It is therefore, by this ADR,
**not a production execution configuration**, and its docstring says so.

Making the registry a mandatory field outright was considered and rejected: it
would delete the ability to unit-test `to_decimal`, venue and currency
injection, `vwap` / `trade_count` absence and book-level ordering without
constructing a registry — the concerns `normalize_wire_*` actually exists to
enforce, and which have nothing to do with identity. The two-mode design fixes
the production path while leaving the lift independently testable, and names the
unsafe mode so it cannot be selected by accident.

## 2. The canonical instrument key, normatively

Byte-identical derivation across independent processes is the property the whole
design rests on, so the construction is specified normatively rather than by
example. It follows the two existing house precedents —
`lifecycle.evidence.evidence_id_for` and
`deployment_manager.packaging.compute_checksum` — both of which build a
newline-joined `label=value` rendering and hash it.

### N1 — Namespace constant

```text
ALPHALAB_INSTRUMENT_NAMESPACE = UUID("1935bdfa-e8c0-5611-ae10-607c3a67c19b")
```

Derived as `uuid5(NAMESPACE_DNS, "instrument.alphalab.dev")` so any reader can
recompute and audit it rather than trust a magic literal. It is declared once,
as a hard-coded `UUID` literal and **not recomputed at import**, and is frozen
for the life of the scheme. Changing it changes every `asset_id` in existence
and requires a new ADR.

### N2 — Key construction

```python
canonical_instrument_key = "\n".join(
    [
        "alphalab.instrument.v1",  # scheme tag -- fixed literal, first line
        f"asset_type={asset_type}",
        f"exchange={exchange}",
        f"symbol={symbol}",
        f"currency={currency}",
    ]
)
asset_id = str(uuid5(ALPHALAB_INSTRUMENT_NAMESPACE, canonical_instrument_key))
```

Field order is **fixed as written and is part of the specification**, not
sorted. The field set is fixed and small; `evidence_id_for` sorts only its
open-ended `metrics` mapping and uses fixed order for its scalars, and this
follows that precedent. The scheme tag is the **first line**, so any future
change to the key shape bumps `v1` and leaves existing ids derivable.

Worked example, executed rather than hypothetical:

```text
key      = 'alphalab.instrument.v1\nasset_type=equity\nexchange=XNAS\nsymbol=AAPL\ncurrency=USD'
asset_id = '2b670078-27a6-57c2-b359-4e64d8809ea2'      # UUID version 5
```

This id satisfies `validate_uuid_id` and constructs a `core.Fill`.

### N3 — Field normalization

Applied at `InstrumentRecord` construction, before derivation.

| Field | Rule |
| --- | --- |
| Character set | Every key field must be **printable ASCII** (`0x20`-`0x7E`). Non-ASCII is **refused**. This eliminates Unicode normalization (NFC/NFD) as a cross-process divergence source outright, and costs nothing: tickers, MICs and ISO 4217 codes are ASCII. |
| Whitespace | Leading and trailing whitespace is **stripped**. Any **internal** whitespace is **refused**. A canonical symbol with an internal space is a data error; a provider symbol that legitimately has one (`"AAPL US Equity"`) belongs in `aliases`, which is not a key field. |
| Case — `symbol` | Uppercased, ASCII-only. `str.upper()` on an ASCII-validated string is total and locale-independent. |
| Case — `exchange` | Uppercased, ASCII-only. MIC codes are uppercase (`XNAS`, `XNYS`). |
| Case — `currency` | Uppercased, ASCII-only. ISO 4217 is uppercase. |
| Case — `asset_type` | Already lowercase; see N4. |
| Separator safety | The line separator is `\n` and the label separator is `=`. Any key field containing `\n`, `\r` or any control character is **refused at construction**. Following the `lifecycle.identity._validate` precedent, AlphaLab **refuses rather than escapes**: an escaping scheme is a second thing to get right and a second thing to keep compatible. An `=` inside a value is safe, because the key is a digest input and is never parsed back. |
| Missing values | `asset_type`, `exchange`, `symbol` and `currency` are **all required and non-empty**. There is **no `"UNKNOWN"` sentinel**: a sentinel would derive one identity for two genuinely different instruments, which is v2.6's "absent, not fabricated" rule violated at the identity layer. An instrument whose exchange or currency is unknown is not registerable. |

### N4 — Enum representation

`asset_type` renders as **`AssetType.value`** — the declared lowercase string:
`"equity"`, `"future"`, `"option"`, `"forex"`, `"crypto"`, `"cash"`.

`core.enums.AssetType` is a `@unique StrEnum` whose values are its declared
canonical spellings, so `.value` is the stable serialized form.
`evidence_id_for` uses `.name` for `ValidationMethod` because that enum is
`auto()`-valued and has no declared string; the rule in both cases is the same —
use the declared, stable form.

Changing any `AssetType` **value** changes every derived `asset_id` for that
type. It is a breaking change to instrument identity and requires an ADR and a
scheme-tag bump. Adding a new member is safe.

### N5 — Fields deliberately excluded from the key

| Excluded | Why |
| --- | --- |
| `sector` | Descriptive and **mutable**. A reclassification would silently re-identify the instrument and orphan every historical `Fill`. This exclusion is what makes deferred sector classification safe. |
| `aliases` | A new provider mapping must **not** change the instrument's identity. Aliases are lookup keys into the registry, not identity inputs, and are exempt from N3's normalization rules because they must reproduce the provider's own spelling verbatim. |
| `asset_id` | Derived from the key; including it would be circular. |

## 3. The `Intent` guarantee is conditional

`alphalab.instrument` exposes instrument resolution as a public API that any
package may call, including strategies. **`alphalab.strategy` acquires no
dependency on `alphalab.instrument` or `alphalab.market`, and `Intent` is
unchanged and unvalidated.** `Intent.instrument` remains a bare `str`, no
`Intent` construction is intercepted, and a strategy remains free to build one
from any string.

The guarantee is therefore conditional, and is stated as such:

> When a strategy obtains its instrument identifier through the canonical
> resolution API, for an instrument registered in the same `InstrumentRegistry`
> the run's `NormalizationPolicy` uses, that identifier equals the `asset_id` on
> that instrument's market records.

This follows from both sides deriving from one authority. A strategy that
hard-codes a ticker, or resolves against a different registry, is outside the
guarantee: its orders will be sized against an asset the market never priced and
the run will produce **zero fills** rather than an error. That is the observed
v2.6 failure mode, which this ADR documents but does not detect.

Detection is not in v2.7. It would mean either validating `Intent.instrument` —
a market-layer dependency inside `alphalab.strategy`, which the current layering
forbids — or having the pipeline flag intents naming unpriced assets, a
behavioural change to allocation that would need its own decision about whether
an unpriced intent is an error or a normal no-op.
`tests/integration/test_mark_to_market_pipeline.py` shows an unpriced asset is
currently a legitimate outcome. Both are deferred.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Canonical `asset_id` value and derivation | `alphalab.instrument.identity` |
| `(provider, symbol) -> asset_id` mapping | `alphalab.instrument.registry.InstrumentRegistry` |
| Instrument descriptive metadata | `alphalab.instrument.record.InstrumentRecord` |
| Refusal at the wire boundary | `alphalab.market.normalization`, which calls the registry |
| Admission of a production source | `alphalab.market.provider.ProviderHistorySource.of` |
| UUID enforcement on execution | `alphalab.core` — **unchanged** |

Dependency direction: `alphalab.instrument` imports only `alphalab.common` and
`alphalab.core.ids` / `core.enums`. `alphalab.market` imports
`alphalab.instrument`. Nothing imports it in reverse, and `alphalab.core` gains
no dependency.

This settles the ownership question the v2.7 archaeology left open.
`market.normalization.SymbolMap` becomes a resolver strategy under
`UnresolvedIdentity`, not an authority. `marketdata.SymbolMetadata` and
`data.DatasetMetadata` are explicitly **not** the authority and are left
untouched.

---

# Data model changes

New, in `alphalab.instrument`:

```text
InstrumentRecord (frozen, slots)
    asset_id:    str                      # derived; UUID-shaped
    symbol:      str                      # AlphaLab's canonical symbol
    asset_type:  AssetType                # alphalab.core.enums.AssetType
    exchange:    str
    currency:    str
    sector:      str | None = None        # DECLARED, NEVER POPULATED IN v2.7
    aliases:     Mapping[str, str] = {}   # provider -> that provider's symbol

InstrumentRegistry (frozen, slots)
    instruments: PersistentMap[str, InstrumentRecord]          # asset_id -> record
    by_provider: PersistentMap[str, PersistentMap[str, str]]   # provider -> symbol -> asset_id

UnresolvedIdentity (frozen, slots)
    symbols: SymbolMap = SymbolMap()

InstrumentResolutionError(MarketValidationError)
```

Changed:

```text
NormalizationPolicy
  + identity: IdentityResolution = UNRESOLVED_IDENTITY
  + provider: str = ""
  - symbols: SymbolMap                     # REMOVED -- moved onto UnresolvedIdentity

ProviderHistorySource.of
  ~ policy: NormalizationPolicy            # default removed; now required
```

`NormalizationPolicy.symbols` is **removed**, not retained alongside `identity`.
`SymbolMap` still exists and is still exported; it now lives on the
`UnresolvedIdentity` mode, so a v2.6 policy is rewritten as::

    NormalizationPolicy(symbols=SymbolMap({...}))                     # v2.6
    NormalizationPolicy(identity=UnresolvedIdentity(SymbolMap({...})))  # v2.7

Keeping both fields would leave two answers to "what instrument is this symbol?"
on one policy, which is the ambiguity this ADR exists to remove. This is a
breaking change to a public constructor and is recorded as such in
`CHANGELOG.md`; §15 states the blast radius.

Unchanged, explicitly: `Quote`, `Bar`, `Tick`, `OrderBookSnapshot`,
`MarketRecord`, `OrderRequest`, `oms.order.Order`, `OrderInstruction`,
`ExecutionReport`, `Fill`, `Trade`, `TradeRecord`, `AssetId`,
`validate_uuid_id`, `Intent`. Every one keeps `asset_id` (or `instrument`) as
`str`. **Only the value changes** — from a ticker to a canonical id. That is
what makes this change surgical: no signature on the execution path moves.

`InstrumentRegistry` is **configuration, not state**. It is threaded through
`NormalizationPolicy`, which is not persisted. No snapshot, no schema, and no
interaction with `tests/regression/test_snapshot_field_coverage.py`.

---

# Boundary behavior

Enforcement is at two points, and the distinction is the substance of this ADR.

**Resolution — `market.normalization.normalize_wire_quote` / `_trade` / `_bar` /
`_book`.** When `identity` is an `InstrumentRegistry`, an unregistered
`(provider, symbol)` raises `InstrumentResolutionError`. When `identity` is
`UnresolvedIdentity`, the symbol passes through exactly as in v2.6. These
functions are a low-level lift and remain usable in both modes.

**Admission — `market.provider.ProviderHistorySource.of`.** This is the
production provider -> execution entry point, and it refuses a policy whose
`identity` is not an `InstrumentRegistry`, before any provider call:

```text
ProviderHistorySource requires InstrumentRegistry-backed identity resolution.
This policy uses UnresolvedIdentity, which passes provider symbols through
unchanged and cannot produce an asset_id that core.Fill / core.Trade will
accept. Register the instruments and supply an InstrumentRegistry.
```

The `policy: NormalizationPolicy = DEFAULT_POLICY` default on
`ProviderHistorySource.of` is removed as part of this: a defaulted parameter
whose default value is always refused is a trap.

A resolution failure must name the provider and the symbol, and must be an
`InstrumentResolutionError` — a `MarketValidationError` subclass — and **never**
a `core.DomainValidationError`. The whole point is that the identity failure no
longer travels to the execution adapter to die there.

---

# Persistence semantics

None. No new persisted type, no snapshot change, no serializer change, no schema
version movement, and no `DEFAULT_SCHEMA_VERSION` interaction.
`InstrumentRegistry` is built by the caller at configuration time, exactly as
`RiskLimits` and `CapitalBudget` are.

Identity stability across processes comes from determinism, not from storage:
`uuid5` of the same canonical key is the same value in every process, forever. A
caller who wants the registry durable serializes it themselves; AlphaLab does
not require it.

---

# Testing invariants

1. A run driven from a provider through `ProviderHistorySource` with a
   **registered** instrument reaches a fill. Today this is impossible without a
   hand-authored UUID.
2. Normalizing an **unregistered** provider symbol under an `InstrumentRegistry`
   raises `InstrumentResolutionError` naming provider and symbol — at
   normalization, not at the fill.
3. `ProviderHistorySource.of` refuses a policy whose identity mode is
   `UnresolvedIdentity`, before any provider call.
4. No `DomainValidationError` for `asset_id` can originate at
   `runtime.execution_adapters`. Regression guard: drive the full path with an
   unregistered symbol and assert the exception type and originating module.
5. Derivation is stable: the same declared instrument yields the same `asset_id`
   across processes and across two independently constructed registries.
6. Given one `InstrumentRegistry`, resolving an instrument through the canonical
   API yields the same `asset_id` that normalization assigns to that
   instrument's records from any registered provider. This is a property of the
   registry, testable without touching `alphalab.strategy`.
7. Every resolved `asset_id` satisfies `validate_uuid_id`; the derivation and
   the invariant agree by construction.
8. A **golden-value derivation test** pins `canonical_instrument_key` and the
   resulting `asset_id` for a fixed `InstrumentRecord` — the worked example in
   N2 — so any change to field order, normalization, enum rendering or the
   namespace fails loudly. This mirrors ADR-0017's frozen-digest test.
9. `InstrumentRecord.sector` is `None` for every instrument v2.7 can construct,
   and `TradeRecord.sector_id` remains `None`. v2.6's absence rule is preserved.

Suites: `tests/unit/instrument/`,
`tests/regression/test_instrument_identity_reaches_a_fill.py`, and an extension
of `tests/integration/test_provider_source_session.py`.

---

# Migration and compatibility

No persisted data is affected, so there is nothing to migrate.

Source compatibility: `NormalizationPolicy` gains two defaulted fields, so
existing construction sites compile unchanged, and a policy left on
`UNRESOLVED_IDENTITY` behaves exactly as v2.6 did. The behavioural break is
opt-in per policy, plus the removed default on `ProviderHistorySource.of`.

---

# Explicit non-goals

- Sector classification data, or any sector value. `InstrumentRecord.sector` is
  declared and left `None`.
- A corporate-actions, listings or reference-data feed.
- Deleting or merging the five asset-class enums.
- Reviving `marketdata.SymbolMetadata` or `marketdata.ProviderCatalog`.
- Validating `asset_id` inside `Intent`, `OrderRequest`, `oms.order.Order` or
  `ExecutionReport`. One boundary, not five.
- Detecting an `Intent` that names an unresolved or unpriced instrument.
- Any change to `Fill` / `Trade` validation.

---

# Consequences

Benefits. The documented default path can reach a fill. Failure moves from the
last stage to the first and names the offending symbol. One authority answers
"what is this instrument" for both market data and strategies. `asset_id`
becomes reproducible across environments with no shared infrastructure. Sector
becomes a one-field change later instead of an architecture change, because N5
keeps it out of the key.

Costs. A new top-level package. Operators must register instruments — real work
that v2.6 hid by making the path fail late. `ProviderHistorySource.of` loses a
parameter default. A second identity entry point, `Intent`, is addressed by
convention rather than enforcement, and the zero-fill mismatch remains
undetected in v2.7.

---

# Alternatives Considered

**Relax `validate_uuid_id` to accept any non-empty string.** Rejected. It
deletes the only invariant that caught this defect, converts a loud failure into
a silent one, and makes `AssetId` meaningless.

**Promote `SymbolMap` to the authority by requiring a total mapping.** Rejected.
`SymbolMap` is a `Mapping[str, str]` with no notion of provider, no metadata, no
minting rule and no room for sector. It would still require every operator to
hand-author UUIDs — the status quo of `test_provider_source_session.py`,
formalised.

**Adopt `marketdata.SymbolMetadata` / `ProviderCatalog` as the authority.**
Rejected. Wrong layer: they are provider-facing and describe what a vendor
offers. Neither has an `asset_id` field, neither has tests, and neither has
consumers. Reviving an orphan into a load-bearing role is how the two-`Order`
problem in ADR-0008 arose.

**Mint a random UUID4 per instrument at registration.** Rejected. Two
environments registering "AAPL" independently get different ids, so a
`ValidationEvidence` from staging cannot be compared with one from production.
It also makes the registry the sole source of truth, which must then itself be
persisted and synchronised — new persisted state, contradicting D5.

**Derive `asset_id` implicitly from any symbol, without registration.**
Rejected: every typo becomes a new instrument.

**Put the registry in `alphalab.market`.** Rejected: it would make
`alphalab.strategy` depend on the market layer merely to name an instrument.

**Make `NormalizationPolicy.identity` optional, with `None` meaning
passthrough.** Rejected: an absent value silently selecting the unsafe
behaviour is the shape of the original defect. The mode is named instead.

**Escape separator characters in key fields.** Rejected in favour of refusal,
following `lifecycle.identity._validate`. An escaping scheme is a second thing
to get right and a second thing to keep compatible forever.

---

# Release impact

Minor-version feature with one declared behavioural break, confined to the
production admission point. The public API grows by one package. No persisted
format changes.

Blast radius, verified by inspection rather than estimated:

- `tests/unit/market/test_normalization.py` — **unchanged**. Its assertions use
  a local `_POLICY` and an explicit `SymbolMap`, both of which exercise
  `UnresolvedIdentity`, which is preserved.
- `tests/integration/test_provider_source_session.py` — must register the
  instrument instead of mapping to a hand-authored UUID.
- `market/provider.py` — the only production caller of `normalize_wire_*`.

On the five competing asset-class enums, a decision without a cleanup:
`core.enums.AssetType` is **nominated** as the eventual single authority. It is
the only one with production consumers (`broker.position`, `brokers.position`),
and `InstrumentRecord.asset_type` uses it. `marketdata.AssetClass`,
`data.DataAssetClass`, `live.AssetClass` and `crypto.InstrumentType` are
untouched and undeprecated in v2.7. Convergence is v3.0 removal work.
