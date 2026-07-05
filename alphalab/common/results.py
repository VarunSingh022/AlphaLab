"""Shared result containers."""

from dataclasses import dataclass
from typing import TypeVar

from alphalab.common.exceptions import AlphaLabValidationError

ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class Result[ValueT]:
    """Minimal immutable success or failure result."""

    ok: bool
    value: ValueT | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.ok and self.error is not None:
            raise AlphaLabValidationError("successful results cannot include an error")
        if not self.ok and not self.error:
            raise AlphaLabValidationError("failed results must include an error")
