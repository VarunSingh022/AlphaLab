"""LSTM backpropagation through time and the attention backward pass (ledger SCF-004).

Every analytical gradient is compared with central differences on a scalar loss
that reads every output, for every parameter, every input and the initial state.
The tolerance is tight -- a relative error of 1e-6 -- not the package default,
because a smooth function's central difference at a step of 1e-5 is good to
about 1e-9, and a wrong gradient is wrong by far more than that.

Then the two train: an LSTM learns to recall the first element of a sequence --
which only a gradient that flows back through every step can teach it -- and a
self-attention layer learns to average its sequence.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import replace

import pytest

from alphalab.deep_learning import (
    DenseLayer,
    DLInputError,
    LSTMCell,
    LSTMRegressor,
    LSTMState,
    SelfAttentionLayer,
    apply_self_attention_gradients,
    backward_self_attention,
    create_lstm_cell,
    create_lstm_regressor,
    create_self_attention_layer,
    forward_self_attention,
    lstm_backward,
    lstm_forward_sequence,
    lstm_sequence,
    numerical_gradient,
    predict_lstm,
    scaled_dot_product_attention,
    scaled_dot_product_attention_backward,
    train_lstm,
)
from alphalab.deep_learning.dense import DenseGradients

RTOL = 1e-6
ATOL = 1e-8

Vectors = tuple[tuple[float, ...], ...]


def _close(analytical: tuple[float, ...], numerical: tuple[float, ...]) -> None:
    assert len(analytical) == len(numerical)
    worst = max(
        abs(a - n) - (ATOL + RTOL * abs(n)) for a, n in zip(analytical, numerical, strict=True)
    )
    assert worst <= 0.0, f"a gradient disagrees with central differences by {worst:.3g}"


def _random(rng: random.Random, rows: int, columns: int) -> Vectors:
    return tuple(tuple(rng.uniform(-1.0, 1.0) for _ in range(columns)) for _ in range(rows))


def _flat(rows: Vectors) -> tuple[float, ...]:
    return tuple(value for row in rows for value in row)


def _shaped(flat: tuple[float, ...], rows: int, columns: int) -> Vectors:
    return tuple(tuple(flat[r * columns : (r + 1) * columns]) for r in range(rows))


# --------------------------------------------------------------------------- #
# Parameters, flattened and back
# --------------------------------------------------------------------------- #


def _layer_params(layer: DenseLayer) -> tuple[float, ...]:
    return (*_flat(layer.weights), *layer.bias)


def _layer_from(layer: DenseLayer, params: tuple[float, ...]) -> tuple[DenseLayer, int]:
    rows, columns = len(layer.weights), len(layer.bias)
    used = rows * columns + columns
    weights = _shaped(params[: rows * columns], rows, columns)
    return replace(layer, weights=weights, bias=tuple(params[rows * columns : used])), used


def _gradient_params(gradients: DenseGradients) -> tuple[float, ...]:
    return (*_flat(gradients.weight_gradients), *gradients.bias_gradients)


def _gates(cell: LSTMCell) -> tuple[DenseLayer, ...]:
    return (cell.forget_gate, cell.input_gate, cell.candidate_gate, cell.output_gate)


def _cell_params(cell: LSTMCell) -> tuple[float, ...]:
    return tuple(value for gate in _gates(cell) for value in _layer_params(gate))


def _cell_from(cell: LSTMCell, params: tuple[float, ...]) -> LSTMCell:
    rebuilt: list[DenseLayer] = []
    offset = 0
    for gate in _gates(cell):
        layer, used = _layer_from(gate, params[offset:])
        rebuilt.append(layer)
        offset += used
    forget, input_, candidate, output = rebuilt
    return LSTMCell(forget, input_, candidate, output, hidden_size=cell.hidden_size)


# --------------------------------------------------------------------------- #
# LSTM
# --------------------------------------------------------------------------- #

INPUT, HIDDEN, STEPS = 3, 4, 6


def _lstm_problem() -> tuple[LSTMCell, Vectors, LSTMState, Vectors, tuple[float, ...]]:
    rng = random.Random(7)
    cell = create_lstm_cell(INPUT, HIDDEN, seed=11)
    # Non-zero biases, so no gate sits at a symmetric point by construction.
    cell = _cell_from(cell, tuple(value + rng.uniform(-0.3, 0.3) for value in _cell_params(cell)))
    sequence = _random(rng, STEPS, INPUT)
    initial = LSTMState(
        hidden=tuple(rng.uniform(-0.5, 0.5) for _ in range(HIDDEN)),
        cell=tuple(rng.uniform(-0.5, 0.5) for _ in range(HIDDEN)),
    )
    read_hidden = _random(rng, STEPS, HIDDEN)
    read_cell = tuple(rng.uniform(-1.0, 1.0) for _ in range(HIDDEN))
    return cell, sequence, initial, read_hidden, read_cell


def _lstm_loss(
    cell: LSTMCell,
    sequence: Vectors,
    initial: LSTMState,
    read_hidden: Vectors,
    read_cell: tuple[float, ...],
) -> float:
    """A loss reading every hidden state and the final cell state."""

    states = lstm_forward_sequence(cell, sequence, initial)
    loss = sum(
        weight * value
        for state, weights in zip(states, read_hidden, strict=True)
        for weight, value in zip(weights, state.hidden, strict=True)
    )
    return loss + sum(w * c for w, c in zip(read_cell, states[-1].cell, strict=True))


def _lstm_gradients() -> tuple[object, ...]:
    cell, sequence, initial, read_hidden, read_cell = _lstm_problem()
    steps = lstm_sequence(cell, sequence, initial)
    gradients = lstm_backward(cell, steps, read_hidden, read_cell)
    return cell, sequence, initial, read_hidden, read_cell, gradients


def test_lstm_parameter_gradients_match_central_differences() -> None:
    cell, sequence, initial, read_hidden, read_cell = _lstm_problem()
    gradients = lstm_backward(cell, lstm_sequence(cell, sequence, initial), read_hidden, read_cell)
    analytical = tuple(
        value
        for gate in (
            gradients.forget_gate,
            gradients.input_gate,
            gradients.candidate_gate,
            gradients.output_gate,
        )
        for value in _gradient_params(gate)
    )

    numerical = numerical_gradient(
        lambda params: _lstm_loss(
            _cell_from(cell, params), sequence, initial, read_hidden, read_cell
        ),
        _cell_params(cell),
    )

    assert len(analytical) == 4 * (HIDDEN + INPUT + 1) * HIDDEN
    _close(analytical, numerical)


def test_lstm_input_gradients_match_at_every_step() -> None:
    cell, sequence, initial, read_hidden, read_cell = _lstm_problem()
    gradients = lstm_backward(cell, lstm_sequence(cell, sequence, initial), read_hidden, read_cell)

    numerical = numerical_gradient(
        lambda flat: _lstm_loss(cell, _shaped(flat, STEPS, INPUT), initial, read_hidden, read_cell),
        _flat(sequence),
    )

    _close(_flat(gradients.input_gradients), numerical)
    # The first input reaches the loss only through every later step.
    assert any(abs(value) > 1e-6 for value in gradients.input_gradients[0])


def test_lstm_initial_state_gradients_match() -> None:
    cell, sequence, initial, read_hidden, read_cell = _lstm_problem()
    gradients = lstm_backward(cell, lstm_sequence(cell, sequence, initial), read_hidden, read_cell)

    hidden = numerical_gradient(
        lambda h: _lstm_loss(cell, sequence, replace(initial, hidden=h), read_hidden, read_cell),
        initial.hidden,
    )
    cell_state = numerical_gradient(
        lambda c: _lstm_loss(cell, sequence, replace(initial, cell=c), read_hidden, read_cell),
        initial.cell,
    )

    _close(gradients.initial_hidden, hidden)
    _close(gradients.initial_cell, cell_state)


def test_the_cached_forward_pass_is_the_forward_pass() -> None:
    cell, sequence, initial, _, _ = _lstm_problem()

    assert tuple(step.state for step in lstm_sequence(cell, sequence, initial)) == (
        lstm_forward_sequence(cell, sequence, initial)
    )


@pytest.mark.parametrize(
    ("hidden", "final", "match"),
    [
        (((0.0,) * HIDDEN,) * (STEPS - 1), None, "entries for a sequence"),
        (((0.0,) * (HIDDEN - 1),) * STEPS, None, "must have 4 entries"),
        (((0.0,) * HIDDEN,) * STEPS, (0.0,), "grad_final_cell"),
    ],
)
def test_lstm_backward_refuses_mis_shaped_gradients(
    hidden: Vectors, final: tuple[float, ...] | None, match: str
) -> None:
    cell, sequence, initial, _, _ = _lstm_problem()
    steps = lstm_sequence(cell, sequence, initial)

    with pytest.raises(DLInputError, match=match):
        lstm_backward(cell, steps, hidden, final)
    with pytest.raises(DLInputError, match="cannot be empty"):
        lstm_backward(cell, (), ())


def _recall_task(rng: random.Random, count: int) -> tuple[tuple[Vectors, ...], Vectors]:
    sequences = tuple(tuple((rng.uniform(-1.0, 1.0),) for _ in range(5)) for _ in range(count))
    return sequences, tuple((sequence[0][0],) for sequence in sequences)


def _recall_error(model: LSTMRegressor, sequences: tuple[Vectors, ...], targets: Vectors) -> float:
    return sum(
        (predict_lstm(model, sequence)[0] - target[0]) ** 2
        for sequence, target in zip(sequences, targets, strict=True)
    ) / len(sequences)


def test_an_lstm_learns_to_recall_the_first_element_of_its_sequence() -> None:
    """Only a gradient that reaches the first step through the other four can teach this.

    Judged on sequences it never saw: always answering zero scores about 1/3.
    """

    rng = random.Random(3)
    sequences, targets = _recall_task(rng, 40)
    unseen, answers = _recall_task(rng, 50)
    model = create_lstm_regressor(input_size=1, hidden_size=8, output_size=1, seed=5)

    trained, losses = train_lstm(model, sequences, targets, learning_rate=0.3, epochs=60)

    assert losses[-1] < 0.1 * losses[0], losses
    assert _recall_error(trained, unseen, answers) < 0.1 * _recall_error(model, unseen, answers)
    # Training is a pure function of its inputs.
    short = train_lstm(model, sequences[:4], targets[:4], learning_rate=0.3, epochs=2)
    assert short == train_lstm(model, sequences[:4], targets[:4], learning_rate=0.3, epochs=2)


# --------------------------------------------------------------------------- #
# Attention
# --------------------------------------------------------------------------- #

N_QUERIES, N_KEYS, D_K, D_V = 3, 4, 5, 2


def _attention_problem() -> tuple[Vectors, Vectors, Vectors, Vectors]:
    rng = random.Random(17)
    return (
        _random(rng, N_QUERIES, D_K),
        _random(rng, N_KEYS, D_K),
        _random(rng, N_KEYS, D_V),
        _random(rng, N_QUERIES, D_V),
    )


def _attention_loss(queries: Vectors, keys: Vectors, values: Vectors, read: Vectors) -> float:
    context = scaled_dot_product_attention(queries, keys, values).context
    return sum(
        r * c
        for row, weights in zip(context, read, strict=True)
        for r, c in zip(weights, row, strict=True)
    )


@pytest.mark.parametrize("which", ["queries", "keys", "values"])
def test_attention_gradients_match_central_differences(which: str) -> None:
    queries, keys, values, read = _attention_problem()
    output = scaled_dot_product_attention(queries, keys, values)
    gradients = scaled_dot_product_attention_backward(queries, keys, values, output, read)
    inputs = {"queries": queries, "keys": keys, "values": values}
    shape = len(inputs[which]), len(inputs[which][0])

    def loss(flat: tuple[float, ...]) -> float:
        changed = {**inputs, which: _shaped(flat, *shape)}
        return _attention_loss(changed["queries"], changed["keys"], changed["values"], read)

    _close(_flat(getattr(gradients, which)), numerical_gradient(loss, _flat(inputs[which])))


def test_attention_backward_refuses_a_mis_shaped_gradient() -> None:
    queries, keys, values, read = _attention_problem()
    output = scaled_dot_product_attention(queries, keys, values)

    with pytest.raises(DLInputError, match="grad_context"):
        scaled_dot_product_attention_backward(queries, keys, values, output, read[:-1])


SEQUENCE, WIDTH = 4, 3


def _projections(layer: SelfAttentionLayer) -> tuple[DenseLayer, ...]:
    return (layer.query, layer.key, layer.value)


def _layer_with(layer: SelfAttentionLayer, params: tuple[float, ...]) -> SelfAttentionLayer:
    rebuilt: list[DenseLayer] = []
    offset = 0
    for projection in _projections(layer):
        built, used = _layer_from(projection, params[offset:])
        rebuilt.append(built)
        offset += used
    query, key, value = rebuilt
    return SelfAttentionLayer(query, key, value)


def _self_attention_problem() -> tuple[SelfAttentionLayer, Vectors, Vectors]:
    rng = random.Random(23)
    layer = create_self_attention_layer(WIDTH, key_size=2, value_size=3, seed=29)
    params = tuple(
        value + rng.uniform(-0.2, 0.2)
        for projection in _projections(layer)
        for value in _layer_params(projection)
    )
    return _layer_with(layer, params), _random(rng, SEQUENCE, WIDTH), _random(rng, SEQUENCE, 3)


def _self_attention_loss(layer: SelfAttentionLayer, sequence: Vectors, read: Vectors) -> float:
    context = forward_self_attention(layer, sequence).attention.context
    return sum(
        r * c
        for row, weights in zip(context, read, strict=True)
        for r, c in zip(weights, row, strict=True)
    )


def test_self_attention_parameter_and_input_gradients_match() -> None:
    layer, sequence, read = _self_attention_problem()
    gradients = backward_self_attention(layer, forward_self_attention(layer, sequence), read)
    analytical = tuple(
        value
        for projection in (gradients.query, gradients.key, gradients.value)
        for value in _gradient_params(projection)
    )
    params = tuple(
        value for projection in _projections(layer) for value in _layer_params(projection)
    )

    _close(
        analytical,
        numerical_gradient(
            lambda p: _self_attention_loss(_layer_with(layer, p), sequence, read), params
        ),
    )
    _close(
        _flat(gradients.input_gradients),
        numerical_gradient(
            lambda flat: _self_attention_loss(layer, _shaped(flat, SEQUENCE, WIDTH), read),
            _flat(sequence),
        ),
    )


def _train(
    layer: SelfAttentionLayer,
    data: tuple[tuple[Vectors, Vectors], ...],
    epochs: int,
    rate: float,
    step: Callable[[SelfAttentionLayer, Vectors, Vectors, float], tuple[SelfAttentionLayer, float]],
) -> tuple[SelfAttentionLayer, list[float]]:
    losses = []
    for _ in range(epochs):
        total = 0.0
        for sequence, target in data:
            layer, loss = step(layer, sequence, target, rate)
            total += loss
        losses.append(total / len(data))
    return layer, losses


def _attention_step(
    layer: SelfAttentionLayer, sequence: Vectors, target: Vectors, rate: float
) -> tuple[SelfAttentionLayer, float]:
    cache = forward_self_attention(layer, sequence)
    context = cache.attention.context
    count = len(context) * len(context[0])
    loss = (
        sum(
            (c - t) ** 2
            for row, goal in zip(context, target, strict=True)
            for c, t in zip(row, goal, strict=True)
        )
        / count
    )
    grad = tuple(
        tuple(2.0 * (c - t) / count for c, t in zip(row, goal, strict=True))
        for row, goal in zip(context, target, strict=True)
    )
    gradients = backward_self_attention(layer, cache, grad)
    return apply_self_attention_gradients(layer, gradients, rate), loss


def test_a_self_attention_layer_learns_to_average_its_sequence() -> None:
    rng = random.Random(41)
    data = []
    for _ in range(12):
        sequence = _random(rng, SEQUENCE, WIDTH)
        mean = tuple(sum(row[d] for row in sequence) / SEQUENCE for d in range(WIDTH))
        data.append((sequence, (mean,) * SEQUENCE))
    layer = create_self_attention_layer(WIDTH, key_size=2, value_size=WIDTH, seed=43)

    _, losses = _train(layer, tuple(data), epochs=60, rate=0.3, step=_attention_step)

    assert losses[-1] < 0.05 * losses[0], losses
