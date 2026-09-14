"""High-level facade orchestrating execution against the protocol."""

from decimal import Decimal

from alphalab.broker.account import BrokerAccount
from alphalab.broker.state import BrokerState, ConnectionStatus


class BrokerEngine:
    """Facade orchestrating safe interaction with configured BrokerProtocols."""

    @staticmethod
    def initialize(broker_name: str, initial_cash: Decimal, currency: str) -> BrokerState:
        """Constructs an empty base state for the broker layer.

        ``currency`` is **required** as of v2.17, having defaulted to ``"USD"``.
        It is stamped onto the account this state carries and onto every
        figure derived from it, and nothing downstream refuses a wrong one --
        an account in the wrong currency reconciles against a venue silently
        and wrongly. ADR-0019's rule: a currency is named, never assumed.
        """
        account = BrokerAccount(
            account_id=f"{broker_name}-ACC",
            cash=initial_cash,
            equity=initial_cash,
            buying_power=initial_cash,
            margin=Decimal("0.00"),
            available_funds=initial_cash,
            currency=currency,
        )
        return BrokerState(
            broker_name=broker_name,
            connection_status=ConnectionStatus.DISCONNECTED,
            account=account,
        )
