"""Strict validation rules for Market Data state transitions."""

from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.exceptions import MarketDataValidationError
from alphalab.marketdata.state import MarketDataState


def validate_provider_registration(state: MarketDataState, config: ProviderConfig) -> None:
    if not config.provider_id.strip():
        raise MarketDataValidationError("Provider ID cannot be empty.")
    if config.provider_id in state.providers:
        raise MarketDataValidationError(f"Provider {config.provider_id} already registered.")
