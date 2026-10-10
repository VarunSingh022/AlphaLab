"""A reference REST venue adapter, kept with the tests that exercise it.

``RestVenueBroker`` and its ``HttpVenueTransport`` were part of ``alphalab.broker``
until v3.11. They hold venue credentials and speak one made-up venue protocol,
which is an application's to own (ledger BRK-007): AlphaLab defines the
contract an adapter meets -- ``BrokerProtocol``, the normalized execution events,
reconciliation -- and ships ``PaperBroker`` as its simulation. This package is
the worked example of a real adapter meeting that contract, tested against a
local venue server over a real socket. It is not importable from ``alphalab``.
"""
