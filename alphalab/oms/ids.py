"""Strongly typed identifiers for OMS entities."""

import uuid
from dataclasses import dataclass

from alphalab.common.ids import new_id


@dataclass(frozen=True, slots=True)
class OrderId:
    """Typed UUID-backed identifier for an Order."""

    value: uuid.UUID

    @classmethod
    def generate(cls) -> "OrderId":
        """Generates a new, unique OrderId.

        Draws from the ambient identifier source, so an OrderId minted inside
        ``use_id_source`` is reproducible (see :mod:`alphalab.common.ids`).
        """
        return cls(uuid.UUID(str(new_id())))
