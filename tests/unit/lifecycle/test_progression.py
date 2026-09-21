"""The research-to-live progression: what moves, what does not, and what is recorded.

Every test drives the real transition table. Nothing here restates the legal
moves in the test -- a test that carried its own copy of the rules would pass
against a table that had been quietly changed.
"""

from dataclasses import replace

import pytest

from alphalab.lifecycle import (
    INITIAL_STAGE,
    LEGAL_PROGRESSION_TRANSITIONS,
    PAUSABLE_STAGES,
    PROGRESSION_MODEL_STAGES,
    LifecycleInputError,
    LifecycleTransitionError,
    StageTransition,
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

REF = StrategyVersionRef("momentum", 3)


def _at(stage: StrategyLifecycleStage) -> StrategyProgression:
    """A progression walked to ``stage`` through the real table."""

    progression = begin_progression(REF)
    route = {
        StrategyLifecycleStage.RESEARCH: (),
        StrategyLifecycleStage.BACKTEST: (StrategyLifecycleStage.BACKTEST,),
        StrategyLifecycleStage.VALIDATION: (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
        ),
        StrategyLifecycleStage.PAPER: (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
        ),
        StrategyLifecycleStage.PRODUCTION_CANDIDATE: (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
        ),
        StrategyLifecycleStage.LIVE: (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
            StrategyLifecycleStage.LIVE,
        ),
        StrategyLifecycleStage.PAUSED: (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PAUSED,
        ),
        StrategyLifecycleStage.ARCHIVED: (StrategyLifecycleStage.ARCHIVED,),
    }[stage]
    for index, target in enumerate(route):
        progression = advance_progression(progression, target, "walked", float(index))
    return progression


class TestInitialState:
    def test_a_progression_begins_at_research_with_no_history(self) -> None:
        progression = begin_progression(REF)
        assert progression.stage is INITIAL_STAGE is StrategyLifecycleStage.RESEARCH
        assert progression.transitions == ()
        assert progression.reference == REF

    def test_beginning_records_no_transition_because_none_happened(self) -> None:
        """A self-referential RESEARCH -> RESEARCH entry would be a move nobody made."""

        assert begin_progression(REF).history.to_tuple() == ()

    def test_research_is_not_terminal(self) -> None:
        assert not begin_progression(REF).is_terminal


class TestValidTransitions:
    @pytest.mark.parametrize(
        "current",
        [stage for stage in StrategyLifecycleStage if stage is not StrategyLifecycleStage.PAUSED],
    )
    def test_every_declared_move_is_accepted(self, current: StrategyLifecycleStage) -> None:
        """Driven from the table itself, so a new member is covered automatically."""

        for target in sorted(LEGAL_PROGRESSION_TRANSITIONS[current], key=lambda stage: stage.name):
            progression = replace(_at(current), stage=current)
            moved = advance_progression(progression, target, "declared move", 10.0)
            assert moved.stage is target

    def test_the_whole_happy_path_walks_research_to_live(self) -> None:
        progression = begin_progression(REF)
        for index, stage in enumerate(
            (
                StrategyLifecycleStage.BACKTEST,
                StrategyLifecycleStage.VALIDATION,
                StrategyLifecycleStage.PAPER,
                StrategyLifecycleStage.PRODUCTION_CANDIDATE,
                StrategyLifecycleStage.LIVE,
            )
        ):
            progression = advance_progression(progression, stage, "promoted", float(index))
        assert progression.stage is StrategyLifecycleStage.LIVE
        assert [step.to_stage for step in progression.transitions] == [
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
            StrategyLifecycleStage.PRODUCTION_CANDIDATE,
            StrategyLifecycleStage.LIVE,
        ]

    def test_a_step_backwards_is_legal_and_recorded(self) -> None:
        """Paper that disagrees with the backtest goes back to validation."""

        progression = advance_progression(
            _at(StrategyLifecycleStage.PAPER),
            StrategyLifecycleStage.VALIDATION,
            "paper diverged from the backtest",
            99.0,
        )
        assert progression.stage is StrategyLifecycleStage.VALIDATION
        assert progression.transitions[-1].reason == "paper diverged from the backtest"


class TestInvalidTransitions:
    def test_a_stage_cannot_move_to_itself(self) -> None:
        with pytest.raises(LifecycleTransitionError, match="already in stage"):
            advance_progression(
                begin_progression(REF), StrategyLifecycleStage.RESEARCH, "again", 1.0
            )

    def test_research_cannot_skip_straight_to_live(self) -> None:
        with pytest.raises(LifecycleTransitionError, match="can only move to"):
            advance_progression(begin_progression(REF), StrategyLifecycleStage.LIVE, "skip", 1.0)

    def test_validation_cannot_skip_paper(self) -> None:
        with pytest.raises(LifecycleTransitionError, match="can only move to"):
            advance_progression(
                _at(StrategyLifecycleStage.VALIDATION),
                StrategyLifecycleStage.PRODUCTION_CANDIDATE,
                "skip",
                1.0,
            )

    def test_a_refused_move_changes_nothing(self) -> None:
        progression = _at(StrategyLifecycleStage.VALIDATION)
        with pytest.raises(LifecycleTransitionError):
            advance_progression(progression, StrategyLifecycleStage.LIVE, "skip", 1.0)
        assert progression.stage is StrategyLifecycleStage.VALIDATION
        assert len(progression.transitions) == 2

    def test_a_transition_with_no_reason_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="states no reason"):
            advance_progression(begin_progression(REF), StrategyLifecycleStage.BACKTEST, "   ", 1.0)

    def test_illegal_progression_move_answers_without_attempting_it(self) -> None:
        progression = begin_progression(REF)
        reason = illegal_progression_move(progression, StrategyLifecycleStage.LIVE)
        assert reason is not None and "can only move to" in reason
        assert illegal_progression_move(progression, StrategyLifecycleStage.BACKTEST) is None
        assert progression.stage is StrategyLifecycleStage.RESEARCH


class TestArchivedIsTerminal:
    def test_nothing_moves_out_of_archived(self) -> None:
        archived = archive_progression(begin_progression(REF), "abandoned", 5.0)
        assert archived.is_terminal
        assert LEGAL_PROGRESSION_TRANSITIONS[StrategyLifecycleStage.ARCHIVED] == frozenset()
        for target in StrategyLifecycleStage:
            with pytest.raises(LifecycleTransitionError):
                advance_progression(archived, target, "resurrect", 6.0)

    def test_every_stage_can_reach_archived(self) -> None:
        for stage in StrategyLifecycleStage:
            if stage is StrategyLifecycleStage.ARCHIVED:
                continue
            progression = replace(_at(stage), stage=stage)
            assert (
                archive_progression(progression, "retired", 1.0).stage
                is StrategyLifecycleStage.ARCHIVED
            )


class TestPauseAndResume:
    @pytest.mark.parametrize("stage", PAUSABLE_STAGES)
    def test_a_running_stage_can_be_paused_and_resumes_to_itself(
        self, stage: StrategyLifecycleStage
    ) -> None:
        progression = _at(stage)
        paused = pause_progression(progression, "operator halted trading", 100.0)
        assert paused.is_paused
        assert resume_target(paused) is stage
        resumed = resume_progression(paused, "operator resumed trading", 101.0)
        assert resumed.stage is stage

    @pytest.mark.parametrize(
        "stage",
        [stage for stage in StrategyLifecycleStage if stage not in PAUSABLE_STAGES],
    )
    def test_a_stage_that_is_not_running_cannot_be_paused(
        self, stage: StrategyLifecycleStage
    ) -> None:
        progression = replace(_at(stage), stage=stage)
        with pytest.raises(LifecycleTransitionError, match="not running"):
            pause_progression(progression, "hold", 1.0)

    def test_a_pause_cannot_be_used_to_skip_a_stage(self) -> None:
        """The property the whole pause rule exists for."""

        paused = pause_progression(_at(StrategyLifecycleStage.PAPER), "hold", 10.0)
        with pytest.raises(LifecycleTransitionError, match="resume"):
            advance_progression(paused, StrategyLifecycleStage.LIVE, "sneak in", 11.0)

    def test_a_paused_progression_can_still_be_archived(self) -> None:
        paused = pause_progression(_at(StrategyLifecycleStage.LIVE), "hold", 10.0)
        assert (
            archive_progression(paused, "abandoned while paused", 11.0).stage
            is StrategyLifecycleStage.ARCHIVED
        )

    def test_resuming_something_that_is_not_paused_is_refused(self) -> None:
        with pytest.raises(LifecycleTransitionError, match="nothing to resume"):
            resume_progression(_at(StrategyLifecycleStage.LIVE), "resume", 1.0)

    def test_a_handbuilt_pause_with_no_history_cannot_be_resumed(self) -> None:
        """No target is invented for a progression whose pause nobody recorded."""

        from alphalab.common.append_log import AppendOnlyLog

        orphan = StrategyProgression(REF, StrategyLifecycleStage.PAUSED, AppendOnlyLog())
        assert resume_target(orphan) is None
        with pytest.raises(LifecycleTransitionError, match="no recorded transition"):
            resume_progression(orphan, "resume", 1.0)
        # And it can still be retired, which is the one honest move left.
        assert archive_progression(orphan, "retired", 2.0).stage is StrategyLifecycleStage.ARCHIVED

    def test_resume_target_is_none_when_not_paused(self) -> None:
        assert resume_target(_at(StrategyLifecycleStage.LIVE)) is None

    def test_the_second_pause_resumes_to_the_second_stage(self) -> None:
        """The history is read backwards, so an older pause does not win."""

        progression = pause_progression(_at(StrategyLifecycleStage.PAPER), "first", 1.0)
        progression = resume_progression(progression, "back", 2.0)
        progression = advance_progression(
            progression, StrategyLifecycleStage.PRODUCTION_CANDIDATE, "cleared", 3.0
        )
        progression = pause_progression(progression, "second", 4.0)
        assert resume_target(progression) is StrategyLifecycleStage.PRODUCTION_CANDIDATE
        assert (
            resume_progression(progression, "back again", 5.0).stage
            is StrategyLifecycleStage.PRODUCTION_CANDIDATE
        )


class TestHistory:
    def test_every_move_records_who_when_and_why(self) -> None:
        progression = advance_progression(
            begin_progression(REF),
            StrategyLifecycleStage.BACKTEST,
            "first backtest completed",
            1234.5,
            actor_id="researcher-1",
        )
        step = progression.transitions[-1]
        assert step == StageTransition(
            StrategyLifecycleStage.RESEARCH,
            StrategyLifecycleStage.BACKTEST,
            "first backtest completed",
            1234.5,
            "researcher-1",
        )

    def test_an_unattributed_move_records_an_empty_actor_rather_than_a_guess(self) -> None:
        progression = advance_progression(
            begin_progression(REF), StrategyLifecycleStage.BACKTEST, "automated", 1.0
        )
        assert progression.transitions[-1].actor_id == ""

    def test_the_history_is_append_only_and_the_old_value_survives(self) -> None:
        first = _at(StrategyLifecycleStage.BACKTEST)
        second = advance_progression(first, StrategyLifecycleStage.VALIDATION, "validated", 2.0)
        assert len(first.transitions) == 1
        assert len(second.transitions) == 2
        assert first.stage is StrategyLifecycleStage.BACKTEST


class TestModelStageConsistency:
    def test_the_relation_is_total_over_the_progression_stages(self) -> None:
        assert set(PROGRESSION_MODEL_STAGES) == set(StrategyLifecycleStage)

    def test_a_consistent_pair_reports_no_conflict(self) -> None:
        version = StrategyVersion(
            name="momentum",
            version=3,
            definition=StrategyDefinition("momentum", "Momentum", "1", "a", "d"),
            stage=ModelStage.PRODUCTION,
        )
        assert progression_conflicts(_at(StrategyLifecycleStage.LIVE), version) == ()

    def test_a_live_progression_over_an_archived_version_is_a_conflict(self) -> None:
        version = StrategyVersion(
            name="momentum",
            version=3,
            definition=StrategyDefinition("momentum", "Momentum", "1", "a", "d"),
            stage=ModelStage.ARCHIVED,
        )
        conflicts = progression_conflicts(_at(StrategyLifecycleStage.LIVE), version)
        assert len(conflicts) == 1
        assert "ARCHIVED" in conflicts[0] and "LIVE" in conflicts[0]

    def test_a_progression_for_another_version_is_reported_before_anything_else(self) -> None:
        version = StrategyVersion(
            name="reversal",
            version=1,
            definition=StrategyDefinition("reversal", "Reversal", "1", "a", "d"),
            stage=ModelStage.NONE,
        )
        conflicts = progression_conflicts(begin_progression(REF), version)
        assert len(conflicts) == 1
        assert "different strategy versions" in conflicts[0]

    def test_a_conflict_is_reported_and_nothing_is_repaired(self) -> None:
        version = StrategyVersion(
            name="momentum",
            version=3,
            definition=StrategyDefinition("momentum", "Momentum", "1", "a", "d"),
            stage=ModelStage.NONE,
        )
        progression = _at(StrategyLifecycleStage.LIVE)
        progression_conflicts(progression, version)
        assert progression.stage is StrategyLifecycleStage.LIVE
        assert version.stage is ModelStage.NONE


class TestDeterminism:
    def test_the_same_sequence_of_moves_produces_an_equal_progression(self) -> None:
        def walk() -> StrategyProgression:
            progression = begin_progression(REF)
            for index, stage in enumerate(
                (
                    StrategyLifecycleStage.BACKTEST,
                    StrategyLifecycleStage.VALIDATION,
                    StrategyLifecycleStage.PAPER,
                )
            ):
                progression = advance_progression(progression, stage, "step", float(index))
            return progression

        assert walk() == walk()
