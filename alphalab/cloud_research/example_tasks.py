"""Example real tasks, used both to demonstrate and to test cloud_research.

These are genuine, quant-relevant units of work (training and evaluating a model),
not toy arithmetic -- the point of this package is running work like this in
parallel across a worker pool, so the example should look like what someone would
actually submit, and be importable by dotted path from a spawned worker process
the same way any real task must be.
"""

from alphalab.ml.evaluation import r_squared
from alphalab.ml.linear_regression import predict_linear, train_linear_regression


def train_and_evaluate_linear_model(
    x: list[list[float]], y: list[float], l2_penalty: float = 0.0
) -> dict[str, float]:
    """Trains a linear regression model and reports its in-sample R^2.

    Accepts plain lists rather than the tuples `alphalab.ml` normally uses,
    since job payloads travel as ordinary, JSON-shaped data.
    """
    x_tuple = tuple(tuple(row) for row in x)
    y_tuple = tuple(y)
    feature_names = tuple(f"x{i}" for i in range(len(x_tuple[0])))

    model = train_linear_regression(feature_names, x_tuple, y_tuple, l2_penalty=l2_penalty)
    predictions = predict_linear(model, x_tuple)
    r2 = r_squared(y_tuple, predictions)

    return {"r_squared": r2, "intercept": model.intercept, "l2_penalty": l2_penalty}


def always_fails(message: str = "deliberate failure") -> None:
    """A task that always raises, used to test the real failure path -- a job
    submitted to a genuine worker process that genuinely raises, not a mocked
    failure, so the resulting fail_job transition is tested against real behavior.
    """
    raise ValueError(message)
