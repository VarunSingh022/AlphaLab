"""Accounts this connector tracks -- the canonical broker account.

``AccountSnapshot`` described the same thing as
:class:`alphalab.broker.account.BrokerAccount` with different field names
(``cash_balance`` for ``cash``) and a subset of its fields. It is now that
class; ``broker_id`` and ``metadata``, which only a multi-broker router needs,
are optional fields on the canonical type.

A snapshot is what the venue last reported. Settling a fill moves ``cash``;
``equity`` and ``available_funds`` change when the venue next reports them, not
when AlphaLab infers them.
"""

from alphalab.broker.account import BrokerAccount

#: The canonical account, under the name this package has always used.
AccountSnapshot = BrokerAccount

__all__ = ["AccountSnapshot", "BrokerAccount"]
