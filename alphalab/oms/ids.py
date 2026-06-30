"""Strongly typed identifiers for OMS entities."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrderId:
    """Typed UUID-backed identifier for an Order."""

    value: uuid.UUID

    @classmethod
    def generate(cls) -> "OrderId":
        """Generates a new, unique OrderId."""
        return cls(uuid.uuid4())
