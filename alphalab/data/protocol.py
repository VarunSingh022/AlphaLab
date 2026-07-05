"""Immutable interface protocol for universal data extractors."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class DataExtractorProtocol(Protocol):
    """Pure functional interface defining external row retrieval."""

    def fetch_raw_rows(self) -> Sequence[Mapping[str, Any]]: ...
