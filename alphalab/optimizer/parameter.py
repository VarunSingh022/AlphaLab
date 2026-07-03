"""Immutable parameter model for defining search spaces."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class ParameterType(Enum):
    """Defines the data type of a tunable parameter."""

    INT = auto()
    FLOAT = auto()
    STRING = auto()
    BOOLEAN = auto()


@dataclass(frozen=True, slots=True)
class Parameter:
    """Immutable definition of a single dimension in the optimization search space."""

    name: str
    param_type: ParameterType
    default: Any
    minimum: Any | None = None
    maximum: Any | None = None
    step: Any | None = None
    choices: tuple[Any, ...] | None = None
