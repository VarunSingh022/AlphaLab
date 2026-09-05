"""Orders this connector routes -- the canonical broker order.

Before v2.3 this module defined its own ``BrokerOrder`` with a single,
ambiguous ``order_id``: it was impossible to tell whether that field held
AlphaLab's identifier or the venue's, which is precisely the distinction
reconciliation depends on. The canonical order in :mod:`alphalab.broker.order`
carries both, so this package routes that one.

``OrderStatus.SUBMITTED`` -- an order sent but not yet acknowledged -- is still
not a canonical lifecycle state, and still must not join
:class:`alphalab.core.enums.OrderStatus`. It is now a member of
:class:`alphalab.broker.order.BrokerOrderStatus`, alongside the other states
that exist only between AlphaLab and a venue, so every adapter shares one set of
them instead of each package defining its own.
"""

from alphalab.broker.order import BrokerOrder, BrokerOrderStatus

#: Broker-local operational states, under the name this package has always used.
OrderStatus = BrokerOrderStatus

__all__ = ["BrokerOrder", "BrokerOrderStatus", "OrderStatus"]
