"""AlphaLab's canonical spelling of a crypto trading pair.

Until v3.10 this module also formatted and parsed three named exchanges' native
symbols -- Binance's concatenation, Coinbase's hyphen, Kraken's ``XBT`` for
Bitcoin -- behind ``to_exchange_symbol`` and ``parse_exchange_symbol``. A venue's
symbol spelling is that venue's quirk, and knowing it is the host application's
job (ledger BND-004). Inside AlphaLab it was also a second answer to "which
instrument does this provider symbol denote?", beside the one the instrument
registry gives: a provider's symbol for an instrument is declared as an alias on
its :class:`~alphalab.instrument.registry.InstrumentRecord` and resolved with
:meth:`~alphalab.instrument.registry.InstrumentRegistry.resolve`, and nothing
else should decide it.
"""


def to_canonical_symbol(base_asset: str, quote_asset: str) -> str:
    """Builds AlphaLab's canonical "BASE-QUOTE" symbol, e.g. "BTC-USDT"."""
    return f"{base_asset.upper()}-{quote_asset.upper()}"
