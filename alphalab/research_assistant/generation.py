"""Deterministic strategy-candidate generation from a parameter search space.

No model, no sampling heuristics, no randomness: given a template name and a
discrete grid of parameter choices, this enumerates the full Cartesian product
in a stable order. "Generation" here means systematic enumeration of a
researcher-defined space, not a learned generative process -- this repository
has no LLM and no network access, and the honest scope is a reproducible grid
builder.

The enumeration is not this module's own (v3.12, ledger SCF-003). It is
:class:`~alphalab.research.walk_forward_optimization.ParameterSpace` -- the one
search space AlphaLab has, which walk-forward optimization and the cloud
research sweep read as well -- so a grid enumerates in one order, is validated by
one set of rules and has one identity, whichever of them searched it. Until
v3.12 there were four parameter searches and each built its own grid.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from alphalab.common.types import ParamValue
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.walk_forward_optimization import ParameterSpace
from alphalab.research_assistant.exceptions import ResearchAssistantInputError

__all__ = [
    "ParameterAxes",
    "ParameterSpace",
    "StrategyCandidate",
    "candidate_count",
    "generate_candidates",
    "parameter_space",
]

type ParameterAxes = Mapping[str, Sequence[ParamValue]]
"""Parameter name -> the discrete values that parameter may take, in order."""


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """One concrete point in a strategy's parameter space.

    Attributes:
        candidate_id: Stable identifier, ``"{template}-{index:03d}"`` where
            ``index`` is the candidate's position in the enumerated product.
        template: The strategy template this is a parameterisation of, e.g.
            ``"ma_crossover"``.
        parameters: One chosen value per parameter name.
    """

    candidate_id: str
    template: str
    parameters: Mapping[str, ParamValue] = field(default_factory=dict)


def parameter_space(space: ParameterSpace | ParameterAxes) -> ParameterSpace:
    """``space`` as the research authority's :class:`ParameterSpace`.

    A mapping of axes is the grid :meth:`ParameterSpace.grid` builds from it:
    parameter names sorted, so the order does not depend on the mapping's
    insertion order, and each parameter's own values in the order given.

    Raises:
        ResearchAssistantInputError: If the axes are empty, a parameter has no
            choices, or a value is not a finite bool, int, float or str.
    """
    if isinstance(space, ParameterSpace):
        return space
    if not space:
        raise ResearchAssistantInputError("space cannot be empty.")
    for name in sorted(space):
        if not space[name]:
            raise ResearchAssistantInputError(f"Parameter '{name}' has no choices.")
    try:
        return ParameterSpace.grid(space)
    except ResearchValidationError as error:
        raise ResearchAssistantInputError(str(error)) from error


def generate_candidates(
    template: str, space: ParameterSpace | ParameterAxes, limit: int | None = None
) -> tuple[StrategyCandidate, ...]:
    """Enumerates ``space`` as strategy candidates, in the space's order.

    With ``limit`` set, only the first ``limit`` candidates in that order are
    returned -- and only those are a search: a sweep over them counts ``limit``
    trials, not the size of the grid.

    Raises:
        ResearchAssistantInputError: If ``template`` is blank, ``space`` is
            empty, any parameter has no choices, or ``limit`` is not positive.
    """
    if not template.strip():
        raise ResearchAssistantInputError("template cannot be empty.")
    if limit is not None and limit <= 0:
        raise ResearchAssistantInputError(f"limit must be positive, got {limit}.")

    candidates = parameter_space(space).candidates
    if limit is not None:
        candidates = candidates[:limit]
    return tuple(
        StrategyCandidate(
            candidate_id=f"{template}-{index:03d}",
            template=template,
            parameters=dict(parameters),
        )
        for index, parameters in enumerate(candidates)
    )


def candidate_count(space: ParameterSpace | ParameterAxes) -> int:
    """Returns the size of the full Cartesian product of ``space``.

    Raises:
        ResearchAssistantInputError: If ``space`` is empty or a parameter has no
            choices.
    """
    return len(parameter_space(space))
