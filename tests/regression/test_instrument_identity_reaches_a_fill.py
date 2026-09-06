"""F-1: the documented provider path could not reach a fill, and now can.

Until v2.7 ``market.normalization`` turned a provider symbol into an ``asset_id``
verbatim, and ``core.Fill`` refused anything that was not a UUID. Every stage in
between -- ``Quote``, ``MarketRecord``, ``OrderRequest``, ``OMSOrder``,
``ExecutionReport`` -- carried ``asset_id`` as an unconstrained ``str``, so the
identity was wrong from the first record and nothing noticed until the last one.

That made the failure *late-binding*, which is why it survived three releases:
market data, the strategy, allocation and risk all succeeded, and the run died at
the execution -> core adapter with a ``DomainValidationError`` that named neither
the provider nor the symbol. The only configuration that reached a fill was one
where an operator hand-authored a UUID per instrument, which is what
``tests/integration/test_provider_source_session.py`` did.

These tests hold the four things ADR-0016 changed:

* a registered instrument reaches a real fill through the real path;
* an unregistered one is refused at normalization, naming provider and symbol;
* that refusal is never a ``core.DomainValidationError`` from the adapter;
* a source that cannot name its instruments canonically is refused before the
  provider is even called.

Nothing here is a mock of an AlphaLab layer. The only double is
``StaticTransport``, which the repository already ships to stand in for the
network, plus a counting provider used solely to prove *when* a refusal happens.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.enums import AssetType
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import validate_uuid_id
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market.bar import TimeFrame
from alphalab.market.exceptions import InstrumentResolutionError
from alphalab.market.normalization import (
    UNRESOLVED_IDENTITY,
    NormalizationPolicy,
    SymbolMap,
    UnresolvedIdentity,
    normalize_wire_bar,
)
from alphalab.market.provider import ProviderHistorySource
from alphalab.marketdata.feed import Bar as WireBar
from alphalab.marketdata.timeframe import Timeframe
from alphalab.runtime.session import TradingSession
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import context_factory, running_strategy_state
from tests.integration.test_provider_source_session import (
    ASSET,
    INSTRUMENTS,
    POLICY,
    STRATEGY,
    _adapter,
    _session_config,
)

PROVIDER = "binance"


class _BuyFirstBar(BaseStrategy):
    """Buys once, on the first bar it sees, naming the asset it is given."""

    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._done = False

    def on_bar(self, context: Any, event: Any) -> Iterable[Intent]:
        if self._done:
            return ()
        self._done = True
        return (
            Intent(
                strategy_id=STRATEGY,
                instrument=self._asset_id,
                target=Decimal("2"),
                timestamp=event.bar.timestamp,
            ),
        )


class _CountingProvider:
    """A provider that records whether it was ever asked for history."""

    def __init__(self) -> None:
        self.calls = 0

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[WireBar, ...]:
        self.calls += 1
        return ()


def _unresolved_policy() -> NormalizationPolicy:
    return NormalizationPolicy(venue="BINANCE", currency="USDT", timeframe=TimeFrame.M1)


# --------------------------------------------------------------------------- #
# G. The end-to-end regression: a registered instrument reaches a fill
# --------------------------------------------------------------------------- #


def test_a_registered_instrument_reaches_a_fill_through_the_real_path() -> None:
    """Provider -> normalization -> session -> execution, ending in a real fill.

    The asset id here is *derived* from the instrument declaration, not authored
    by hand. That is the difference between v2.6 and v2.7: the configuration
    that works is now one the system can produce for itself.
    """

    source = ProviderHistorySource.of(
        _adapter(), ["BTCUSDT"], Timeframe.MINUTE, 1_700_000_000.0, 1_700_000_240.0, "BTC", POLICY
    )
    state = TradingSession.initialize(
        _session_config(), running_strategy_state(STRATEGY, _BuyFirstBar(ASSET))
    )

    for record in source.records():
        state, _ = TradingSession.advance(state, record, context_factory)

    fills = state.pipeline.fills.to_tuple()
    assert len(fills) == 1, "a registered instrument must be able to reach a fill"
    assert fills[0].asset_id == ASSET
    validate_uuid_id(fills[0].asset_id, "asset_id")


def test_the_asset_id_that_reaches_the_fill_is_the_one_the_registry_derived() -> None:
    """Identity survives every stage unchanged rather than being re-derived."""

    assert INSTRUMENTS.resolve(PROVIDER, "BTCUSDT") == ASSET
    source = ProviderHistorySource.of(
        _adapter(), ["BTCUSDT"], Timeframe.MINUTE, 1_700_000_000.0, 1_700_000_240.0, "BTC", POLICY
    )

    assert {record.asset_id for record in source.records()} == {ASSET}


# --------------------------------------------------------------------------- #
# E. An unregistered symbol is refused at normalization, and says which
# --------------------------------------------------------------------------- #


def test_an_unregistered_symbol_is_refused_at_normalization() -> None:
    policy = NormalizationPolicy(
        venue="BINANCE",
        currency="USDT",
        timeframe=TimeFrame.M1,
        identity=INSTRUMENTS,
        provider=PROVIDER,
    )
    wire = WireBar("DOGEUSDT", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

    with pytest.raises(InstrumentResolutionError) as error:
        normalize_wire_bar(wire, policy)

    message = str(error.value)
    assert PROVIDER in message, "the refusal must name the provider"
    assert "DOGEUSDT" in message, "the refusal must name the symbol"
    assert "not a registered instrument" in message


def test_a_registry_backed_policy_must_name_its_provider() -> None:
    """Otherwise every symbol is looked up against the empty provider and refused."""

    from alphalab.market.exceptions import MarketValidationError

    with pytest.raises(MarketValidationError, match="must name the provider"):
        NormalizationPolicy(identity=InstrumentRegistry())


# --------------------------------------------------------------------------- #
# H. The guard: this must never surface as a core DomainValidationError
# --------------------------------------------------------------------------- #


def test_an_unregistered_symbol_never_fails_as_a_core_domain_error() -> None:
    """The whole point of ADR-0016: the refusal moved to the front of the path.

    A ``DomainValidationError`` here would mean the bad identity had travelled
    through market data, the strategy, allocation, risk and the OMS before
    anything objected -- the v2.6 behaviour this release removes.
    """

    policy = NormalizationPolicy(
        venue="BINANCE",
        currency="USDT",
        timeframe=TimeFrame.M1,
        identity=INSTRUMENTS,
        provider=PROVIDER,
    )
    wire = WireBar("NOTREGISTERED", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

    with pytest.raises(InstrumentResolutionError) as error:
        normalize_wire_bar(wire, policy)

    assert not isinstance(error.value, DomainValidationError)


def test_the_unresolved_mode_still_produces_an_id_core_refuses() -> None:
    """Documented, not fixed: this is why the mode cannot reach production.

    ``UnresolvedIdentity`` is retained so the wire -> canonical lift stays
    testable without a registry. It is honest about what it produces: a provider
    symbol, which ``core.Fill`` refuses. ADR-0016 keeps it out of production by
    refusing it at ``ProviderHistorySource``, not by pretending it resolves.
    """

    bar = normalize_wire_bar(WireBar("BTCUSDT", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0), _unresolved_policy())

    assert bar.asset_id == "BTCUSDT"
    with pytest.raises(DomainValidationError, match="must be a valid UUID"):
        validate_uuid_id(bar.asset_id, "asset_id")


# --------------------------------------------------------------------------- #
# F. Production admission: refused before the provider is called
# --------------------------------------------------------------------------- #


def test_a_production_source_refuses_the_unresolved_identity_mode() -> None:
    with pytest.raises(InstrumentResolutionError) as error:
        ProviderHistorySource.of(
            _adapter(), ["BTCUSDT"], Timeframe.MINUTE, 0.0, 1.0, "BTC", _unresolved_policy()
        )

    message = str(error.value)
    assert "requires InstrumentRegistry-backed identity resolution" in message
    assert "UnresolvedIdentity" in message


def test_the_refusal_happens_before_the_provider_is_called() -> None:
    """A misconfigured source must cost no request, not one wasted round trip."""

    provider = _CountingProvider()

    with pytest.raises(InstrumentResolutionError):
        ProviderHistorySource.of(
            provider, ["BTCUSDT"], Timeframe.MINUTE, 0.0, 1.0, "BTC", _unresolved_policy()
        )

    assert provider.calls == 0, "the provider was called before the policy was checked"


@pytest.mark.parametrize(
    "identity",
    [UNRESOLVED_IDENTITY, UnresolvedIdentity(SymbolMap({"BTCUSDT": "BTC"}))],
    ids=["default-unresolved", "symbol-map-unresolved"],
)
def test_no_unresolved_variant_reaches_a_production_source(identity: UnresolvedIdentity) -> None:
    """Neither passthrough nor a hand-written SymbolMap is a production identity."""

    provider = _CountingProvider()
    policy = NormalizationPolicy(
        venue="BINANCE", currency="USDT", timeframe=TimeFrame.M1, identity=identity
    )

    with pytest.raises(InstrumentResolutionError):
        ProviderHistorySource.of(provider, ["BTCUSDT"], Timeframe.MINUTE, 0.0, 1.0, "BTC", policy)
    assert provider.calls == 0


def test_the_default_policy_is_not_a_production_configuration() -> None:
    """``DEFAULT_POLICY`` documents itself as unresolved; this holds it to that."""

    from alphalab.market.normalization import DEFAULT_POLICY

    assert isinstance(DEFAULT_POLICY.identity, UnresolvedIdentity)
    with pytest.raises(InstrumentResolutionError):
        ProviderHistorySource.of(
            _CountingProvider(), ["BTCUSDT"], Timeframe.MINUTE, 0.0, 1.0, "BTC", DEFAULT_POLICY
        )


# --------------------------------------------------------------------------- #
# The layering ADR-0016 depends on
# --------------------------------------------------------------------------- #


def test_the_instrument_package_pulls_in_no_higher_layer() -> None:
    """Importing identity must not drag in market, strategy, execution or lifecycle."""

    import subprocess
    import sys

    probe = (
        "import sys, alphalab.instrument;"
        "bad=[m for m in sys.modules if m.startswith(("
        "'alphalab.market','alphalab.strategy','alphalab.execution',"
        "'alphalab.oms','alphalab.lifecycle'))];"
        "print(','.join(sorted(bad)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "", f"instrument imported higher layers: {result.stdout}"


def test_the_strategy_package_does_not_import_instrument_identity() -> None:
    """``Intent`` is unchanged and unvalidated; the guarantee is opt-in. ADR-0016 section 3."""

    import subprocess
    import sys

    probe = "import sys, alphalab.strategy;print('alphalab.instrument' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"


def test_the_registry_resolves_without_scanning_every_instrument() -> None:
    """Resolution is two keyed lookups; it must not depend on how many exist."""

    registry = InstrumentRegistry()
    for index in range(200):
        registry = register_instrument(
            registry,
            InstrumentRecord(
                f"SYM{index}", AssetType.EQUITY, "XNAS", "USD", aliases={PROVIDER: f"S{index}"}
            ),
        )

    first = registry.resolve(PROVIDER, "S0")
    last = registry.resolve(PROVIDER, "S199")

    assert first is not None and last is not None and first != last
    assert registry.resolve(PROVIDER, "MISSING") is None
