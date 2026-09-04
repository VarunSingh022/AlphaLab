"""Deterministic strategy-candidate generation from a parameter search space.

No model, no sampling heuristics, no randomness: given a template name and a
discrete grid of parameter choices, this enumerates the full Cartesian product
in a stable order. "Generation" here means systematic enumeration of a
researcher-defined space, not a learned generative process -- this repository
has no LLM and no network access, and the honest scope is a reproducible grid
builder.
"""

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.research_assistant.exceptions import ResearchAssistantInputError

ParameterSpace = Mapping[str, tuple[float, ...]]
"""Parameter name -> the discrete values that parameter may take."""


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
    parameters: Mapping[str, float] = field(default_factory=dict)


def generate_candidates(
    template: str, space: ParameterSpace, limit: int | None = None
) -> tuple[StrategyCandidate, ...]:
    """Enumerates the Cartesian product of ``space`` as strategy candidates.

    Parameter names are sorted so the enumeration order is independent of the
    mapping's insertion order; each parameter's own values keep the order given.
    With ``limit`` set, only the first ``limit`` candidates in that order are
    returned.

    Raises:
        ResearchAssistantInputError: If ``template`` is blank, ``space`` is
            empty, any parameter has no choices, or ``limit`` is not positive.
    """
    if not template.strip():
        raise ResearchAssistantInputError("template cannot be empty.")
    if not space:
        raise ResearchAssistantInputError("space cannot be empty.")
    if limit is not None and limit <= 0:
        raise ResearchAssistantInputError(f"limit must be positive, got {limit}.")

    names = sorted(space)
    for name in names:
        if not space[name]:
            raise ResearchAssistantInputError(f"Parameter '{name}' has no choices.")

    choice_lists = [space[name] for name in names]
    candidates = []
    for index, combination in enumerate(itertools.product(*choice_lists)):
        if limit is not None and index >= limit:
            break
        parameters = dict(zip(names, combination, strict=True))
        candidates.append(
            StrategyCandidate(
                candidate_id=f"{template}-{index:03d}",
                template=template,
                parameters=parameters,
            )
        )
    return tuple(candidates)


def candidate_count(space: ParameterSpace) -> int:
    """Returns the size of the full Cartesian product of ``space``.

    Raises:
        ResearchAssistantInputError: If ``space`` is empty or a parameter has no
            choices.
    """
    if not space:
        raise ResearchAssistantInputError("space cannot be empty.")
    total = 1
    for name, choices in space.items():
        if not choices:
            raise ResearchAssistantInputError(f"Parameter '{name}' has no choices.")
        total *= len(choices)
    return total
