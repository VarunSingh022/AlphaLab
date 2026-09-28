"""The canonical record of what a strategy is: its identity and its parameters.

Moved here from ``alphalab.studio`` in v3.11 (ledger SCF-001). Strategy Studio
"ran" backtests and pipelines by storing metrics its caller made up, and carried
users, sessions and workspaces -- an application's concerns. Its one canonical
type was this record, which the lifecycle, the research assistant and every
strategy version already used; it now lives with the strategy runtime, and the
scaffold around it is gone.

v3.11 also widens the parameters from ``float`` to
:data:`~alphalab.common.types.ParamValue`: a lookback is an integer and a mode is
a string, and forcing both through ``float`` changed what the caller declared.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from alphalab.common.types import ParamValue
from alphalab.strategy.exceptions import StrategyValidationError

__all__ = ["StrategyDefinition", "numeric_parameters"]


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrategyValidationError(f"StrategyDefinition.{what} must be a non-blank string.")
    return value


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """A strategy's identity, its declared parameters and free-form metadata.

    Attributes:
        strategy_id: The strategy's identifier.
        name: Its human-readable name.
        version: The caller's version label for this definition.
        author: Who wrote it.
        description: What it does.
        parameters: Each parameter's declared value -- a string, an integer, a
            float or a boolean, and nothing else, so a definition serializes
            exactly and reads back identically.
        metadata: Free-form text, not interpreted.
    """

    strategy_id: str
    name: str
    version: str
    author: str
    description: str
    parameters: Mapping[str, ParamValue] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for what in ("strategy_id", "name", "version"):
            _text(getattr(self, what), what)
        for what in ("author", "description"):
            if not isinstance(getattr(self, what), str):
                raise StrategyValidationError(f"StrategyDefinition.{what} must be a string.")
        parameters: dict[str, ParamValue] = {}
        for key, value in self.parameters.items():
            _text(key, f"parameters key {key!r}")
            if not isinstance(value, str | int | float | bool):
                raise StrategyValidationError(
                    f"StrategyDefinition parameter {key!r} is a {type(value).__name__}; a "
                    "parameter is a string, an integer, a float or a boolean, so it "
                    "serializes exactly and reads back as what was declared."
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise StrategyValidationError(
                    f"StrategyDefinition parameter {key!r} is {value!r}. NaN equals nothing -- "
                    "not even itself -- and an infinity has no exact serialized form, so no "
                    "definition holding either could be matched or read back."
                )
            parameters[key] = value
        metadata: dict[str, str] = {}
        for key, text in self.metadata.items():
            if not isinstance(key, str) or not isinstance(text, str):
                raise StrategyValidationError(
                    f"StrategyDefinition metadata must map strings to strings; got "
                    f"{key!r}: {text!r}."
                )
            metadata[key] = text
        object.__setattr__(self, "parameters", MappingProxyType(parameters))
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def numbers(self) -> Mapping[str, float]:
        """The parameters as floats, for a consumer that reads numbers only.

        See :func:`numeric_parameters`.

        Raises:
            StrategyValidationError: If any parameter is a string or a boolean.
        """

        return numeric_parameters(self.parameters)


def numeric_parameters(parameters: Mapping[str, ParamValue]) -> Mapping[str, float]:
    """Declared parameters as floats, for a consumer that reads numbers only.

    What a strategy factory that reads numbers calls on the parameters the
    :class:`~alphalab.strategy.registry.StrategyClassRegistry` hands it. An
    integer is a number and is given as one. A string is not, and nor is a
    boolean -- Python counts ``True`` as ``1``, and a flag read as a weight of
    one is exactly the silent reinterpretation declaring the type stops.

    Raises:
        StrategyValidationError: If any parameter is a string or a boolean.
    """

    numbers: dict[str, float] = {}
    for key, value in parameters.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise StrategyValidationError(
                f"Strategy parameter {key!r} is {value!r}, not a number; read it as what "
                "it was declared, not as a float."
            )
        numbers[key] = float(value)
    return MappingProxyType(numbers)
