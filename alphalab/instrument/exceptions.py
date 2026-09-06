"""Domain exceptions for instrument identity.

Resolution failures are deliberately *not* here. ADR-0016 assigns the refusal of
an unregistered ``(provider, symbol)`` to the wire boundary, so
:class:`alphalab.market.exceptions.InstrumentResolutionError` lives in the market
layer with the rest of that boundary's vocabulary. This package never imports
``alphalab.market``; :meth:`~alphalab.instrument.registry.InstrumentRegistry.resolve`
answers ``None`` for an unknown pair and the boundary turns that into a refusal
that names the provider and the symbol.
"""

from alphalab.common.exceptions import AlphaLabError


class InstrumentError(AlphaLabError):
    """Base exception for all instrument identity errors."""


class InstrumentInputError(InstrumentError):
    """Raised when an instrument's declared fields cannot form a canonical key.

    Every rule this reports is a rule that exists to keep derivation
    byte-identical across processes: non-ASCII text, internal whitespace,
    control characters, or a missing required field. See ADR-0016 N3.
    """


class InstrumentRegistrationError(InstrumentError):
    """Raised when a registration would replace one identity with another.

    Registering the same record twice is a no-op. Registering *different*
    content under an identifier the registry already holds, or pointing one
    provider symbol at a second instrument, is refused rather than applied:
    silently overwriting an identity is how two runs come to disagree about what
    they traded.
    """
