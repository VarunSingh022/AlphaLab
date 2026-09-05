"""Safety properties of the external boundary, asserted rather than assumed.

Every concern here is one a broker integration gets wrong in a way that costs
money: a duplicated order, a credential in a log line, an order sent into a
connection that has not resynced, or an identifier ambiguous enough that one
order's fills land on another.
"""

import dataclasses
from decimal import Decimal

import pytest

from alphalab.broker import (
    BrokerAccount,
    BrokerEngine,
    BrokerExecution,
    BrokerOrder,
    BrokerPosition,
    BrokerState,
    ConnectionStatus,
    ExternalOrderMap,
    PaperBroker,
)
from alphalab.broker.reconciliation import ReconciliationReport
from alphalab.runtime.broker_routing import RoutingConfig, RoutingResult

_CREDENTIAL_WORDS = (
    "key",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "auth",
    "bearer",
    "signature",
)


@pytest.mark.parametrize(
    "model",
    [
        BrokerOrder,
        BrokerExecution,
        BrokerAccount,
        BrokerPosition,
        BrokerState,
        RoutingConfig,
        RoutingResult,
        ReconciliationReport,
        ExternalOrderMap,
    ],
)
def test_no_boundary_type_carries_credential_material(model: type) -> None:
    """Secrets belong to whoever builds an adapter, never to the domain model.

    A credential on one of these would end up in an event, a snapshot, a
    reconciliation report or a repr -- all of which are meant to be logged.
    """
    fields = {f.name.lower() for f in dataclasses.fields(model)}
    leaked = {name for name in fields for word in _CREDENTIAL_WORDS if word in name}
    assert not leaked, f"{model.__name__} carries credential-shaped fields: {sorted(leaked)}"


def test_broker_events_carry_no_credential_material() -> None:
    """Events are the thing most likely to be written to a log."""
    from alphalab.broker import events as broker_events

    for name in dir(broker_events):
        candidate = getattr(broker_events, name)
        if not (isinstance(candidate, type) and dataclasses.is_dataclass(candidate)):
            continue
        fields = {f.name.lower() for f in dataclasses.fields(candidate)}
        leaked = {n for n in fields for word in _CREDENTIAL_WORDS if word in n}
        assert not leaked, f"{name} carries credential-shaped fields: {sorted(leaked)}"


def test_only_connected_can_trade() -> None:
    """Every non-connected state must refuse orders, including new ones."""
    assert ConnectionStatus.CONNECTED.can_trade
    for status in ConnectionStatus:
        if status is not ConnectionStatus.CONNECTED:
            assert not status.can_trade, f"{status.name} must not accept orders"


def test_a_disconnect_never_silently_reopens_trading() -> None:
    state = BrokerEngine.initialize("V", Decimal("1000"), "USD")
    broker = PaperBroker()

    connected, _ = broker.connect(state, 1.0)
    dropped, _ = broker.disconnect(connected, "link lost", 2.0)

    assert broker.status(dropped) is ConnectionStatus.DISCONNECTED
    assert not broker.status(dropped).can_trade


def test_a_heartbeat_does_not_reconnect_a_dropped_link() -> None:
    """Liveness is not connectivity: a heartbeat must not imply a usable link."""
    state = BrokerEngine.initialize("V", Decimal("1000"), "USD")
    broker = PaperBroker()

    beaten, _ = broker.heartbeat(state, 1.0)

    assert beaten.last_heartbeat == 1.0
    assert broker.status(beaten) is ConnectionStatus.DISCONNECTED


def test_an_order_identifier_is_never_ambiguous() -> None:
    """Two named fields, so nothing has to infer which identity it holds."""
    fields = {f.name for f in dataclasses.fields(BrokerOrder)}
    assert {"oms_order_id", "broker_order_id"} <= fields
    assert "order_id" not in fields


def test_an_execution_names_the_order_it_belongs_to_unambiguously() -> None:
    fields = {f.name for f in dataclasses.fields(BrokerExecution)}
    assert "broker_order_id" in fields
    assert "order_id" not in fields
    # And keeps the venue's own record of the fill, for tracing back.
    assert "external_id" in fields


def test_identifier_bindings_cannot_be_silently_overwritten() -> None:
    from alphalab.broker.exceptions import BrokerValidationError

    mapping = ExternalOrderMap().bind("OMS-1", "B-1")
    with pytest.raises(BrokerValidationError):
        mapping.bind("OMS-1", "B-2")
    with pytest.raises(BrokerValidationError):
        mapping.bind("OMS-2", "B-1")
