"""Execution reports this connector settles -- the canonical broker execution.

``ExecutionReport`` was a second definition of
:class:`alphalab.broker.execution.BrokerExecution` that differed only in naming
its order field ``order_id`` instead of ``broker_order_id``. It is now that
class, so a fill an adapter produces can be settled by this router without a
copy, and ``external_id`` is preserved end to end.
"""

from alphalab.broker.execution import BrokerExecution

#: The canonical execution, under the name this package has always used.
ExecutionReport = BrokerExecution

__all__ = ["BrokerExecution", "ExecutionReport"]
