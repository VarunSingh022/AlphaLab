"""
AlphaLab Examples
=================

Example 41 : Strategy Lifecycle Progression

Difficulty : Intermediate

Estimated Time : 8 minutes

Prerequisites
-------------

✓ Example 12 (model and strategy lifecycle)

Topics
------

• The eight stages between an idea and live money
• Transitions that are refused, and the reason each refusal exists
• Pausing a live strategy, and resuming it to where it was
• Why a pause cannot be used to skip a stage
• How the progression relates to the registry's own stage, without replacing it

What this shows
---------------

AlphaLab already had two state machines with "lifecycle" in their name and
neither answers this question. `ModelStage` asks whether a *registered artifact*
may be promoted -- and research, backtest and validation are all `NONE` to it,
paper and live are both `PRODUCTION`, and there is no member for paused at all.
`strategy.state.LifecycleState` asks whether an *instance inside a session* is
running, which a deployed version does and stops doing many times without
anything else changing.

`StrategyLifecycleStage` is the third axis: how far a strategy has travelled
from research towards live money. It names no environment -- what is live
*where* stays the deployment ledger's single answer -- and it changes nothing:
recording `LIVE` is a claim somebody makes, not an operation on a machine.

Run

    python examples/41_strategy_lifecycle_progression.py
"""

from alphalab.lifecycle import (
    LEGAL_PROGRESSION_TRANSITIONS,
    PROGRESSION_MODEL_STAGES,
    LifecycleTransitionError,
    StrategyLifecycleStage,
    StrategyProgression,
    StrategyVersionRef,
    advance_progression,
    archive_progression,
    begin_progression,
    illegal_progression_move,
    pause_progression,
    progression_conflicts,
    resume_progression,
    resume_target,
)
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.model_registry import ModelStage
from alphalab.studio.strategy import StrategyDefinition

REFERENCE = StrategyVersionRef("momentum", 4)

#: Who moved the strategy, at each step. A real caller passes an
#: ``enterprise.Principal.principal_id``; ``""`` would be honest for a move no
#: principal requested, and is what the record carries when none is given.
RESEARCHER = "quant-7"
OPERATOR = "ops-2"


def show(progression: StrategyProgression) -> None:
    reachable = ", ".join(
        sorted(stage.name for stage in LEGAL_PROGRESSION_TRANSITIONS[progression.stage])
    )
    print(f"  stage: {progression.stage.name:<22} can move to: {reachable or '(nothing)'}")


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 41 : Strategy Lifecycle Progression")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    # 1. Where a strategy starts
    # ----------------------------------------------------------------- #

    print("\n[1] A progression begins at RESEARCH with an empty history")
    progression = begin_progression(REFERENCE)
    show(progression)
    print(f"  transitions recorded: {len(progression.transitions)}")
    print("  (Beginning is not a transition -- there is no stage it moved from.)")

    # ----------------------------------------------------------------- #
    # 2. The walk to live
    # ----------------------------------------------------------------- #

    print("\n[2] Research -> Backtest -> Validation -> Paper -> Candidate -> Live")
    walk = (
        (StrategyLifecycleStage.BACKTEST, "first backtest over DSV-2024Q1", RESEARCHER),
        (StrategyLifecycleStage.VALIDATION, "walk-forward and overfitting diagnostics", RESEARCHER),
        (StrategyLifecycleStage.PAPER, "cleared for paper on passing evidence", RESEARCHER),
        (StrategyLifecycleStage.PRODUCTION_CANDIDATE, "30 sessions of paper reviewed", OPERATOR),
        (StrategyLifecycleStage.LIVE, "capital allocated, deployed to live-eu", OPERATOR),
    )
    for index, (stage, reason, actor) in enumerate(walk):
        progression = advance_progression(progression, stage, reason, float(index), actor)
        print(f"  {progression.stage.name:<22} <- {reason}")

    # ----------------------------------------------------------------- #
    # 3. What is refused, and why
    # ----------------------------------------------------------------- #

    print("\n[3] Refusals")

    fresh = begin_progression(REFERENCE)
    refusal: str | None = illegal_progression_move(fresh, StrategyLifecycleStage.LIVE)
    print(f"  asking first:  RESEARCH -> LIVE  =>  {refusal}")
    print("  (A query, not an attempt: nothing was written to ask.)")

    try:
        advance_progression(fresh, StrategyLifecycleStage.LIVE, "ship it", 100.0)
    except LifecycleTransitionError as error:
        print(f"  attempting it: {error}")

    print(
        f"  and the progression is still {fresh.stage.name} with "
        f"{len(fresh.transitions)} transitions."
    )

    # ----------------------------------------------------------------- #
    # 4. Pausing, and the promotion it will not let you hide
    # ----------------------------------------------------------------- #

    print("\n[4] Pausing a live strategy")
    paused = pause_progression(progression, "venue outage, orders held", 200.0, OPERATOR)
    show(paused)
    target = resume_target(paused)
    print(f"  it would resume into: {target.name if target else '(unknown)'}")

    print("\n  A pause is a hold, not a rung on the ladder:")
    paper_paused = pause_progression(
        advance_progression(
            advance_progression(
                advance_progression(
                    begin_progression(REFERENCE), StrategyLifecycleStage.BACKTEST, "backtested", 1.0
                ),
                StrategyLifecycleStage.VALIDATION,
                "validated",
                2.0,
            ),
            StrategyLifecycleStage.PAPER,
            "to paper",
            3.0,
        ),
        "paused during paper",
        4.0,
    )
    try:
        advance_progression(paper_paused, StrategyLifecycleStage.LIVE, "resume into live", 5.0)
    except LifecycleTransitionError as error:
        print(f"  {error}")

    resumed = resume_progression(paused, "venue restored", 201.0, OPERATOR)
    print(f"\n  resumed: {resumed.stage.name}")

    # ----------------------------------------------------------------- #
    # 5. The audit trail
    # ----------------------------------------------------------------- #

    print("\n[5] Every move recorded, in the order it happened")
    for step in resumed.transitions:
        actor = step.actor_id or "(unattributed)"
        print(
            f"  t={step.timestamp:>6.1f}  {step.from_stage.name:<22} -> "
            f"{step.to_stage.name:<22} {actor:<12} {step.reason}"
        )

    # ----------------------------------------------------------------- #
    # 6. Archived is terminal
    # ----------------------------------------------------------------- #

    print("\n[6] Archiving")
    archived = archive_progression(resumed, "superseded by momentum@5", 300.0, OPERATOR)
    show(archived)
    print(f"  is_terminal: {archived.is_terminal}")
    try:
        advance_progression(archived, StrategyLifecycleStage.LIVE, "bring it back", 301.0)
    except LifecycleTransitionError as error:
        print(f"  {error}")
    print("  (A retired strategy that comes back is a new version of the line,")
    print("   and it starts again at RESEARCH.)")

    # ----------------------------------------------------------------- #
    # 7. Two axes, checked against each other
    # ----------------------------------------------------------------- #

    print("\n[7] The progression and the registry, compared rather than merged")
    version = StrategyVersion(
        name=REFERENCE.name,
        version=REFERENCE.version,
        definition=StrategyDefinition(
            "momentum", "Momentum", "4", "quant-7", "a crossover", {"fast": 10.0, "slow": 30.0}
        ),
        stage=ModelStage.PRODUCTION,
    )
    print(
        f"  registry says {version.stage.name}; LIVE is consistent with "
        f"{sorted(s.name for s in PROGRESSION_MODEL_STAGES[StrategyLifecycleStage.LIVE])}"
    )
    print(
        f"  conflicts for the LIVE progression: {progression_conflicts(resumed, version) or '()'}"
    )

    stale = begin_progression(REFERENCE)
    for conflict in progression_conflicts(stale, version):
        print(f"  conflict for a RESEARCH progression: {conflict}")
    print("  (Reported, not resolved. Which side is right is a question about")
    print("   what actually happened, and no mapping table can answer it.)")

    # ----------------------------------------------------------------- #

    print("\n[8] Invariants")
    checks = (
        (
            "the initial stage is RESEARCH",
            begin_progression(REFERENCE).stage is StrategyLifecycleStage.RESEARCH,
        ),
        (
            "ARCHIVED is the only terminal stage",
            [
                stage.name
                for stage in StrategyLifecycleStage
                if not LEGAL_PROGRESSION_TRANSITIONS[stage]
            ]
            == ["ARCHIVED"],
        ),
        (
            "a pause resumes to the stage it interrupted",
            resume_target(paused) is StrategyLifecycleStage.LIVE,
        ),
        ("the history is append-only", len(resumed.transitions) > len(progression.transitions)),
        ("the earlier value is unchanged", progression.stage is StrategyLifecycleStage.LIVE),
        ("the progression names no environment", not hasattr(resumed, "environment")),
        (
            "the same walk repeats exactly",
            begin_progression(REFERENCE) == begin_progression(REFERENCE),
        ),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")
    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 41 complete.")
    print("(Nothing here started a process, reached a venue or changed what is")
    print(" live. A progression is a record of claims somebody made.)")


if __name__ == "__main__":
    main()
