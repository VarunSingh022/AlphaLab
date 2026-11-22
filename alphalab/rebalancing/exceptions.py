"""What the rebalancing package refuses (v4.3, ADR-0063 section 6)."""

from __future__ import annotations

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = ["RebalancingInputError"]


class RebalancingInputError(AlphaLabValidationError):
    """A rebalancing strategy, trigger, capital rule or model output that cannot be used as stated.

    Raised for what is malformed -- a trigger with no period, a zone that does
    not exist, a model whose problem trades an asset the strategy was not
    given -- never for an outcome: a construction that is not optimal is a
    recorded rebalance that trades nothing, not an exception.
    """
