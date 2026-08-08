"""Immutable input shapes consumed by factor computations.

PriceSeries wraps existing `alphalab.market.Bar` data -- no new price representation
is introduced. FundamentalSnapshot is a minimal, explicit input shape for Value,
Quality, and Carry factors: no fundamentals data source exists elsewhere in AlphaLab
yet, so this defines only what the factor math itself requires. A future data
package can produce these snapshots; Factor Library does not care where they come
from, the same decoupling principle as `feature_store.protocol.FeatureValueProtocol`.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.market.bar import Bar


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """An ordered sequence of bars for a single asset.

    Attributes:
        asset_id: Identifier of the asset the bars belong to.
        bars: Bars in ascending chronological order. Every bar must share
            `asset_id`, enforced by `__post_init__`.
    """

    asset_id: str
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        mismatched = [b for b in self.bars if b.asset_id != self.asset_id]
        if mismatched:
            raise ValueError(
                f"PriceSeries.asset_id is '{self.asset_id}' but contains bars for "
                f"other assets: {sorted({b.asset_id for b in mismatched})}."
            )


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    """A single point-in-time fundamental data snapshot for one asset.

    Attributes:
        asset_id: Identifier of the asset this snapshot describes.
        timestamp: Unix timestamp the snapshot is as-of.
        price: Current market price per share.
        earnings_per_share: Trailing earnings per share.
        book_value_per_share: Most recent book value per share.
        dividend_per_share: Trailing annual dividend per share.
    """

    asset_id: str
    timestamp: float
    price: Decimal
    earnings_per_share: Decimal
    book_value_per_share: Decimal
    dividend_per_share: Decimal
