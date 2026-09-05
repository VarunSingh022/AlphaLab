"""Positions this connector tracks -- the canonical broker position.

``PositionSnapshot`` duplicated :class:`alphalab.broker.position.BrokerPosition`
and ``AssetClass`` duplicated :class:`alphalab.core.enums.AssetType`. Both are
now the canonical types. The ``position_id`` field is gone: a position is
identified by the account and symbol it belongs to, which is exactly the key
:class:`~alphalab.brokers.state.BrokerConnectorState` stores it under, so the
field restated the key rather than adding to it.
"""

from alphalab.broker.position import BrokerPosition
from alphalab.core.enums import AssetType

#: Canonical instrument categories, under the name this package has always used.
AssetClass = AssetType

#: The canonical position, under the name this package has always used.
PositionSnapshot = BrokerPosition

__all__ = ["AssetClass", "BrokerPosition", "PositionSnapshot"]
