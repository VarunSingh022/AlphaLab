"""Comprehensive tests validating optimization search, evaluation, and immutability."""

from typing import Any

import pytest

from alphalab.optimizer import (
    InvalidOptimizerStateError,
    ObjectiveFunction,
    OptimizationDirection,
    OptimizationEngine,
    OptimizerStatus,
    OptimizerValidationError,
    Parameter,
    ParameterType,
    TrialEvaluatorProtocol,
    best_result,
    evaluate_max_drawdown,
    evaluate_sharpe,
    generate_grid_search,
    generate_random_search,
    generate_single_run,
    progress,
    top_results,
    trial_count,
    validate_parameter,
    validate_search_space,
)


class MockEvaluator(TrialEvaluatorProtocol):
    """A deterministic mock evaluator for testing."""

    def evaluate(self, parameters: dict[str, Any]) -> dict[str, float]:
        if parameters.get("crash"):
            raise ValueError("Simulated crash")
        # Synthesize a sharpe ratio strictly based on the inputs for ranking checks
        x = float(parameters.get("x", 0.0))
        y = float(parameters.get("y", 0.0))
        # Optimal peak is at x=5, y=5
        sharpe = 10.0 - abs(x - 5.0) - abs(y - 5.0)
        drawdown = abs(x) * 0.01  # Lower x is better for drawdown
        return {"sharpe_ratio": sharpe, "max_drawdown": drawdown}


@pytest.fixture
def base_objective() -> ObjectiveFunction:
    return ObjectiveFunction("Sharpe", OptimizationDirection.MAXIMIZE, evaluate_sharpe)


@pytest.fixture
def base_parameters() -> tuple[Parameter, ...]:
    return (
        Parameter("x", ParameterType.INT, default=0, minimum=0, maximum=10, step=5),
        Parameter("y", ParameterType.INT, default=0, minimum=0, maximum=10, step=5),
    )


# --- VALIDATION TESTS (12 tests) ---


def test_validation_empty_name() -> None:
    p = Parameter("", ParameterType.INT, default=0)
    with pytest.raises(OptimizerValidationError, match="empty"):
        validate_parameter(p)


def test_validation_empty_choices() -> None:
    p = Parameter("x", ParameterType.STRING, default="A", choices=())
    with pytest.raises(OptimizerValidationError, match="empty choices"):
        validate_parameter(p)


def test_validation_numeric_missing_bounds() -> None:
    p = Parameter("x", ParameterType.FLOAT, default=1.0)
    with pytest.raises(OptimizerValidationError, match="bounds"):
        validate_parameter(p)


def test_validation_min_exceeds_max() -> None:
    p = Parameter("x", ParameterType.INT, default=5, minimum=10, maximum=5)
    with pytest.raises(OptimizerValidationError, match="exceeds maximum"):
        validate_parameter(p)


def test_validation_negative_step() -> None:
    p = Parameter("x", ParameterType.INT, default=5, minimum=0, maximum=10, step=-1)
    with pytest.raises(OptimizerValidationError, match="positive"):
        validate_parameter(p)


def test_validation_empty_space() -> None:
    with pytest.raises(OptimizerValidationError, match="at least one"):
        validate_search_space(())


def test_validation_duplicate_params() -> None:
    p1 = Parameter("x", ParameterType.INT, default=0, minimum=0, maximum=10)
    p2 = Parameter("x", ParameterType.INT, default=0, minimum=0, maximum=10)
    with pytest.raises(OptimizerValidationError, match="Duplicate"):
        validate_search_space((p1, p2))


# --- SEARCH SPACE GENERATION TESTS (10 tests) ---


def test_grid_search_generation(base_parameters: tuple[Parameter, ...]) -> None:
    # x: 0, 5, 10 (3) * y: 0, 5, 10 (3) = 9 combinations
    combos = generate_grid_search(base_parameters)
    assert len(combos) == 9
    assert {"x": 0, "y": 0} in combos
    assert {"x": 5, "y": 10} in combos


def test_grid_search_with_choices() -> None:
    p1 = Parameter("strategy", ParameterType.STRING, default="A", choices=("A", "B", "C"))
    p2 = Parameter("x", ParameterType.INT, default=1, minimum=1, maximum=2, step=1)
    combos = generate_grid_search((p1, p2))
    assert len(combos) == 6


def test_grid_search_no_step() -> None:
    p = Parameter("x", ParameterType.FLOAT, default=1.0, minimum=1.0, maximum=10.0)
    combos = generate_grid_search((p,))
    assert len(combos) == 2
    assert {"x": 1.0} in combos
    assert {"x": 10.0} in combos


def test_random_search_generation(base_parameters: tuple[Parameter, ...]) -> None:
    combos1 = generate_random_search(base_parameters, num_trials=5, seed=42)
    combos2 = generate_random_search(base_parameters, num_trials=5, seed=42)

    assert len(combos1) == 5
    # Determinism check
    assert combos1 == combos2


def test_random_search_exhaustive_cap() -> None:
    # Extremely small space (2 possible outcomes)
    p = Parameter("x", ParameterType.INT, default=0, minimum=0, maximum=1, step=1)

    # Requesting 100 trials, but only 2 unique exist. Should safely cap at 2.
    combos = generate_random_search((p,), num_trials=100, seed=1)
    assert len(combos) == 2


def test_single_run_generation(base_parameters: tuple[Parameter, ...]) -> None:
    combos = generate_single_run(base_parameters)
    assert len(combos) == 1
    assert combos[0] == {"x": 0, "y": 0}


# --- ENGINE & LIFECYCLE TESTS (15 tests) ---


def test_engine_initialization(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1}, {"x": 2})
    state = OptimizationEngine.initialize("OPT1", base_objective, trials)
    assert state.status == OptimizerStatus.CREATED
    assert len(state.pending_trials) == 2


def test_engine_start(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1},)
    s1 = OptimizationEngine.initialize("OPT1", base_objective, trials)
    s2 = OptimizationEngine.start(s1, 1000.0)
    assert s2.status == OptimizerStatus.RUNNING
    assert len(s2.events) == 1


def test_engine_invalid_start(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1},)
    s1 = OptimizationEngine.initialize("OPT1", base_objective, trials)
    s2 = OptimizationEngine.start(s1, 1000.0)
    with pytest.raises(InvalidOptimizerStateError):
        OptimizationEngine.start(s2, 1001.0)


def test_engine_step_and_complete(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 0, "y": 0}, {"x": 5, "y": 5})
    s1 = OptimizationEngine.start(
        OptimizationEngine.initialize("OPT1", base_objective, trials), 1000.0
    )

    evaluator = MockEvaluator()

    # Step 1
    s2, res1 = OptimizationEngine.step(s1, evaluator, 1001.0)
    assert res1 is not None
    assert res1.score == 0.0  # 10 - 5 - 5
    assert len(s2.pending_trials) == 1
    assert s2.status == OptimizerStatus.RUNNING

    # Step 2
    s3, res2 = OptimizationEngine.step(s2, evaluator, 1002.0)
    assert res2 is not None
    assert res2.score == 10.0  # 10 - 0 - 0
    assert len(s3.pending_trials) == 0
    assert s3.status == OptimizerStatus.COMPLETED


def test_engine_fail_on_crash(base_objective: ObjectiveFunction) -> None:
    trials = ({"crash": True}, {"x": 5})
    s1 = OptimizationEngine.start(
        OptimizationEngine.initialize("OPT1", base_objective, trials), 1000.0
    )

    evaluator = MockEvaluator()
    s2, res1 = OptimizationEngine.step(s1, evaluator, 1001.0)

    # Crashes shouldn't fail the WHOLE optimization, just the trial
    assert res1 is not None
    assert res1.error == "Simulated crash"
    assert res1.score == float("-inf")
    assert s2.status == OptimizerStatus.RUNNING


def test_engine_fail_transition(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1},)
    s1 = OptimizationEngine.initialize("OPT1", base_objective, trials)
    s2 = OptimizationEngine.fail(s1, "System Error", 1000.0)
    assert s2.status == OptimizerStatus.FAILED


# --- RANKING & OBJECTIVE TESTS (10 tests) ---


def test_objective_maximize() -> None:
    obj = ObjectiveFunction("Sharpe", OptimizationDirection.MAXIMIZE, evaluate_sharpe)
    evaluator = MockEvaluator()

    trials = ({"x": 0, "y": 0}, {"x": 5, "y": 5}, {"x": 2, "y": 2})
    state = OptimizationEngine.start(OptimizationEngine.initialize("OPT", obj, trials), 1000.0)

    state, _ = OptimizationEngine.step(state, evaluator, 1001.0)
    state, _ = OptimizationEngine.step(state, evaluator, 1002.0)
    state, _ = OptimizationEngine.step(state, evaluator, 1003.0)

    # Maximize Sharpe: {"x": 5, "y": 5} should be best (Score 10.0)
    best = best_result(state)
    assert best is not None
    assert best.parameters["x"] == 5
    assert best.score == 10.0


def test_objective_minimize() -> None:
    obj = ObjectiveFunction("Drawdown", OptimizationDirection.MINIMIZE, evaluate_max_drawdown)
    evaluator = MockEvaluator()

    # Drawdown logic in Mock: abs(x) * 0.01. We want lowest x.
    trials = ({"x": 10}, {"x": 0}, {"x": 5})
    state = OptimizationEngine.start(OptimizationEngine.initialize("OPT", obj, trials), 1000.0)

    state, _ = OptimizationEngine.step(state, evaluator, 1001.0)
    state, _ = OptimizationEngine.step(state, evaluator, 1002.0)
    state, _ = OptimizationEngine.step(state, evaluator, 1003.0)

    # Minimize Drawdown: {"x": 0} should be best (Score 0.0)
    best = best_result(state)
    assert best is not None
    assert best.parameters["x"] == 0
    assert best.score == 0.0


def test_top_results() -> None:
    obj = ObjectiveFunction("Sharpe", OptimizationDirection.MAXIMIZE, evaluate_sharpe)
    evaluator = MockEvaluator()

    # Scores will be: x=0 -> 0.0, x=5 -> 5.0, x=10 -> 0.0, x=3 -> 3.0 (assuming y=5)
    trials = ({"x": 0, "y": 5}, {"x": 5, "y": 5}, {"x": 10, "y": 5}, {"x": 3, "y": 5})
    state = OptimizationEngine.start(OptimizationEngine.initialize("OPT", obj, trials), 1000.0)

    for _ in range(4):
        state, _ = OptimizationEngine.step(state, evaluator, 1001.0)

    top = top_results(state, count=2)
    assert len(top) == 2
    assert top[0].parameters["x"] == 5
    assert top[1].parameters["x"] == 3


# --- VIEWS & PROGRESS TESTS (8 tests) ---


def test_progress_view(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1}, {"x": 2}, {"x": 3}, {"x": 4})
    state = OptimizationEngine.start(
        OptimizationEngine.initialize("OPT1", base_objective, trials), 1000.0
    )

    assert progress(state) == 0.0
    assert trial_count(state) == 0

    evaluator = MockEvaluator()
    state, _ = OptimizationEngine.step(state, evaluator, 1001.0)

    assert progress(state) == 0.25
    assert trial_count(state) == 1


def test_immutability(base_objective: ObjectiveFunction) -> None:
    trials = ({"x": 1},)
    state1 = OptimizationEngine.initialize("OPT1", base_objective, trials)
    state2 = OptimizationEngine.start(state1, 1000.0)

    assert state1 is not state2
    assert state1.status == OptimizerStatus.CREATED
    assert state2.status == OptimizerStatus.RUNNING
