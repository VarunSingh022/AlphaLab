"""Immutable tracking of available instruments per provider."""

from dataclasses import dataclass

from alphalab.marketdata.symbols import SymbolMetadata


@dataclass(frozen=True, slots=True)
class ProviderCatalog:
    provider_id: str
    supported_symbols: tuple[SymbolMetadata, ...]