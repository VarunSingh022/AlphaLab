"""Strongly typed identifiers for OMS entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from alphalab.common.ids import new_id


@dataclass(frozen=True)
class OrderId:
    """Typed UUID-backed identifier for an Order.

    Its hash is computed once, at construction, and is the value the generated
    dataclass hash always had -- ``hash((value,))`` -- so every set and mapping
    keyed by order ids iterates exactly as before. Computing it on every lookup
    took three Python-level calls, and an OMS transition looks an order id up
    about twenty-five times (ledger PRF-006).
    """

    __slots__ = ("_hash", "value")

    value: uuid.UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "_hash", hash((self.value,)))

    def __hash__(self) -> int:
        return self._hash  # type: ignore[attr-defined,no-any-return]

    def __reduce__(self) -> tuple[type[OrderId], tuple[uuid.UUID]]:
        return (OrderId, (self.value,))

    @classmethod
    def generate(cls) -> OrderId:
        """Generates a new, unique OrderId.

        Draws from the ambient identifier source, so an OrderId minted inside
        ``use_id_source`` is reproducible (see :mod:`alphalab.common.ids`).
        """
        return cls(uuid.UUID(str(new_id())))
