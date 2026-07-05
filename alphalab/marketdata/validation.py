"""Strict validation rules for Market Data state transitions."""

from alphalab.common.validators import require_missing_mapping_key, require_non_empty_string
from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.exceptions import MarketDataValidationError
from alphalab.marketdata.state import MarketDataState


def validate_provider_registration(state: MarketDataState, config: ProviderConfig) -> None:
    require_non_empty_string(
        config.provider_id,
        "provider_id",
        message="Provider ID cannot be empty.",
        exception_type=MarketDataValidationError,
    )
    require_missing_mapping_key(
        state.providers,
        config.provider_id,
        f"Provider {config.provider_id} already registered.",
        exception_type=MarketDataValidationError,
    )
