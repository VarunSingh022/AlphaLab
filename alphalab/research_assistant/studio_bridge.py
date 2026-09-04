"""Turning a generated candidate into a first-class ``alphalab.studio`` strategy.

``alphalab.studio.strategy.StrategyDefinition`` already exists as the canonical
"a strategy and its parameter bounds" record. The assistant generates parameter
points; this is the one adapter that lifts a chosen point into that existing
type, rather than inventing a parallel definition.
"""

from collections.abc import Mapping

from alphalab.research_assistant.generation import StrategyCandidate
from alphalab.studio.strategy import StrategyDefinition


def to_strategy_definition(
    candidate: StrategyCandidate,
    name: str,
    version: str,
    author: str,
    description: str,
    metadata: Mapping[str, str] | None = None,
) -> StrategyDefinition:
    """Builds a :class:`StrategyDefinition` from a candidate.

    The candidate's ``candidate_id`` becomes ``strategy_id`` and its parameters
    are carried across unchanged; ``template`` is added to the metadata so the
    definition still records which search it came from.
    """
    merged_metadata = {"template": candidate.template}
    if metadata:
        merged_metadata.update(metadata)
    return StrategyDefinition(
        strategy_id=candidate.candidate_id,
        name=name,
        version=version,
        author=author,
        description=description,
        parameters=dict(candidate.parameters),
        metadata=merged_metadata,
    )
