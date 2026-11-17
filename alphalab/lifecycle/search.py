"""Successive halving through the ledger: every configuration a round evaluates is counted (v4.3).

:class:`~alphalab.research.sampling.SuccessiveHalving` is the schedule;
:func:`run_successive_halving` runs it. Each round is an experiment -- round
zero the one given, with the first budget, and each later round a child of the
round before it, holding the candidates that round kept -- so every
configuration any round evaluated is a trial in the ledger, under an identity
of its own, and counted (plan I.4, ADR-0072).

A search's selection bias is the whole search's. :class:`HalvingRecord` keeps
the configurations the search began with and the trials every round recorded;
an assessment of the configuration it selects counts at least the first, never
only the last round's survivors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from alphalab.common.registry import RunnerRegistry
from alphalab.lifecycle.designs import ExperimentError
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    ExperimentSpec,
    LedgerCount,
    TrialExecutor,
    TrialLedger,
    candidate_scores,
    run_experiment,
)
from alphalab.research.sampling import SuccessiveHalving
from alphalab.research.selection import SelectionMeasure

__all__ = ["HalvingRecord", "HalvingRound", "run_successive_halving"]


@dataclass(frozen=True, slots=True)
class HalvingRound:
    """One round of a successive-halving search.

    Attributes:
        budget: The budget every candidate of the round was given.
        spec: The round's experiment, its candidates those the round before
            kept, with the round's budget: what a lockbox is read through.
        experiment_id: The round's experiment.
        parent_id: The experiment it continues: the round before it, or for
            round zero, the parent the search was given.
        record: Its record, from :func:`~alphalab.lifecycle.run_experiment`.
        scores: Each candidate's mean score, rendered with the budget; ``None``
            for one whose trial did not complete everywhere.
    """

    budget: int
    spec: ExperimentSpec
    experiment_id: str
    parent_id: str | None
    record: ExperimentRecord
    scores: Mapping[str, float | None]


@dataclass(frozen=True, slots=True)
class HalvingRecord:
    """A successive-halving search, round by round.

    Attributes:
        rounds: Every round, in order.
        configurations: The candidates the search began with: the size of the
            search an assessment of its selection counts.
        counts: Every trial every round recorded, by outcome.
        selected: The last round's best candidate, budget included, ties to
            the first; ``None`` if no candidate of it completed everywhere.
    """

    rounds: tuple[HalvingRound, ...]
    configurations: int
    counts: LedgerCount
    selected: Mapping[str, Any] | None


def _total(counts: list[LedgerCount]) -> LedgerCount:
    return LedgerCount(
        completed=sum(count.completed for count in counts),
        insolvent=sum(count.insolvent for count in counts),
        strategy_failed=sum(count.strategy_failed for count in counts),
        refused=sum(count.refused for count in counts),
        failed=sum(count.failed for count in counts),
    )


def run_successive_halving(
    spec: ExperimentSpec,
    halving: SuccessiveHalving,
    measure: SelectionMeasure,
    registry: RunnerRegistry,
    runner: str,
    ledger: TrialLedger,
    executor: TrialExecutor,
) -> HalvingRecord:
    """Run ``halving`` over ``spec``'s candidates, every round an experiment in ``ledger``.

    ``spec``'s configuration type declares the budget's parameter, and its
    space does not name it: each round adds it. Rounds rank their candidates
    by :func:`~alphalab.lifecycle.experiments.candidate_scores` under
    ``measure``. A resume runs only the trials the ledger lacks, so a search
    stopped part way continues where it stopped.

    Raises:
        ExperimentError: If ``halving`` is not a SuccessiveHalving, or a round
            has no candidate with a score to rank.
    """

    if not isinstance(halving, SuccessiveHalving):
        raise ExperimentError(f"halving must be a SuccessiveHalving, got {halving!r}.")
    if not isinstance(measure, SelectionMeasure):
        raise ExperimentError(f"measure must be a SelectionMeasure, got {measure!r}.")
    rounds: list[HalvingRound] = []
    current = replace(spec, space=halving.round(0, spec.space.candidates))
    for index, budget in enumerate(halving.budgets):
        record = run_experiment(current, registry, runner, ledger, executor)
        scores = candidate_scores(current, ledger, measure)
        rounds.append(
            HalvingRound(budget, current, current.experiment_id, current.parent_id, record, scores)
        )
        if index == len(halving.budgets) - 1:
            break
        ranked = [scores[label] for label in current.space.rendered]
        if all(score is None for score in ranked):
            raise ExperimentError(
                f"Round {index} of the search has no candidate with a score: every trial "
                "failed somewhere, so no round can follow it. Every trial it ran is in the ledger."
            )
        kept = halving.kept(current.space, ranked)
        current = replace(
            spec, space=halving.round(index + 1, kept), parent_id=current.experiment_id
        )
    last = rounds[-1]
    best: tuple[float, Mapping[str, Any]] | None = None
    for candidate, label in zip(current.space.candidates, current.space.rendered, strict=True):
        score = last.scores[label]
        if score is not None and (best is None or score > best[0]):
            best = (score, candidate)
    return HalvingRecord(
        rounds=tuple(rounds),
        configurations=len(spec.space),
        counts=_total([each.record.counts for each in rounds]),
        selected=None if best is None else best[1],
    )
