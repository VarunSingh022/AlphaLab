"""Comprehensive tests for the Deep Learning Engine: activations, gradient checking,
dense layers, networks, conv1d, LSTM, and attention."""

import math
from dataclasses import FrozenInstanceError

import pytest

from alphalab.deep_learning import (
    ActivationType,
    Conv1DLayer,
    DenseLayer,
    DLInputError,
    LSTMCell,
    LSTMState,
    Sequential,
    activation_derivative,
    apply_activation,
    apply_conv1d_gradients,
    apply_gradients,
    backward_conv1d,
    backward_dense,
    create_conv1d_layer,
    create_dense_layer,
    create_lstm_cell,
    forward_conv1d,
    forward_dense,
    forward_network,
    gradients_match,
    initial_lstm_state,
    lstm_forward_sequence,
    lstm_forward_step,
    numerical_gradient,
    predict_network,
    relu,
    relu_derivative,
    scaled_dot_product_attention,
    sigmoid,
    sigmoid_derivative,
    softmax,
    tanh_activation,
    tanh_derivative,
    train_network,
    train_step,
)

# --------------------------------------------------------------------------- #
# Activations
# --------------------------------------------------------------------------- #


def test_relu_zero_for_negative() -> None:
    assert relu(-5.0) == 0.0


def test_relu_identity_for_positive() -> None:
    assert relu(5.0) == 5.0


def test_relu_derivative() -> None:
    assert relu_derivative(1.0) == 1.0
    assert relu_derivative(-1.0) == 0.0


def test_sigmoid_at_zero_is_half() -> None:
    assert sigmoid(0.0) == pytest.approx(0.5)


def test_sigmoid_stable_for_large_negative() -> None:
    # Must not overflow/raise for very negative input.
    assert sigmoid(-1000.0) == pytest.approx(0.0, abs=1e-9)


def test_sigmoid_stable_for_large_positive() -> None:
    assert sigmoid(1000.0) == pytest.approx(1.0, abs=1e-9)


def test_sigmoid_derivative_at_zero() -> None:
    assert sigmoid_derivative(0.0) == pytest.approx(0.25)


def test_tanh_at_zero_is_zero() -> None:
    assert tanh_activation(0.0) == pytest.approx(0.0)


def test_tanh_derivative_at_zero_is_one() -> None:
    assert tanh_derivative(0.0) == pytest.approx(1.0)


def test_softmax_sums_to_one() -> None:
    result = softmax((1.0, 2.0, 3.0))
    assert sum(result) == pytest.approx(1.0)


def test_softmax_uniform_for_equal_inputs() -> None:
    result = softmax((1.0, 1.0, 1.0))
    assert result == pytest.approx((1 / 3, 1 / 3, 1 / 3))


def test_activation_dispatch_matches_direct_call() -> None:
    assert apply_activation(ActivationType.SIGMOID, 0.5) == sigmoid(0.5)
    assert apply_activation(ActivationType.RELU, -0.5) == relu(-0.5)
    assert apply_activation(ActivationType.TANH, 0.5) == tanh_activation(0.5)
    assert apply_activation(ActivationType.LINEAR, 0.5) == 0.5


def test_activation_derivative_dispatch_matches_direct_call() -> None:
    assert activation_derivative(ActivationType.SIGMOID, 0.5) == sigmoid_derivative(0.5)
    assert activation_derivative(ActivationType.LINEAR, 0.5) == 1.0


# --------------------------------------------------------------------------- #
# Gradient checking utility itself
# --------------------------------------------------------------------------- #


def test_numerical_gradient_matches_known_analytical_derivative() -> None:
    """f(x,y) = x^2 + 3xy has analytical gradient (2x+3y, 3x)."""

    def f(params: tuple[float, ...]) -> float:
        x, y = params
        return x**2 + 3 * x * y

    numerical = numerical_gradient(f, (2.0, 1.0))
    expected = (2 * 2.0 + 3 * 1.0, 3 * 2.0)
    assert numerical == pytest.approx(expected, abs=1e-3)


def test_gradients_match_true_for_close_values() -> None:
    assert gradients_match((1.0, 2.0), (1.0001, 2.0001)) is True


def test_gradients_match_false_for_different_values() -> None:
    assert gradients_match((1.0, 2.0), (5.0, 10.0)) is False


def test_numerical_gradient_rejects_empty_params() -> None:
    with pytest.raises(DLInputError):
        numerical_gradient(lambda p: 0.0, ())


# --------------------------------------------------------------------------- #
# Dense layer: gradient-checked
# --------------------------------------------------------------------------- #


def _flatten_dense(layer: DenseLayer) -> tuple[float, ...]:
    flat: list[float] = []
    for row in layer.weights:
        flat.extend(row)
    flat.extend(layer.bias)
    return tuple(flat)


def _unflatten_dense(
    flat: tuple[float, ...], n_in: int, n_out: int, activation: ActivationType
) -> DenseLayer:
    weights = tuple(tuple(flat[i * n_out + j] for j in range(n_out)) for i in range(n_in))
    bias = tuple(flat[n_in * n_out :])
    return DenseLayer(weights=weights, bias=bias, activation=activation)


def test_dense_backward_matches_numerical_gradient() -> None:
    layer = create_dense_layer(3, 2, ActivationType.SIGMOID, seed=1)
    x = (0.5, -0.2, 0.9)
    cache = forward_dense(layer, x)
    grad_output = (1.0, 1.0)
    grads = backward_dense(layer, cache, grad_output)

    def loss_fn(flat_params: tuple[float, ...]) -> float:
        test_layer = _unflatten_dense(flat_params, 3, 2, ActivationType.SIGMOID)
        return sum(forward_dense(test_layer, x).output)

    numerical = numerical_gradient(loss_fn, _flatten_dense(layer))
    analytical = (
        tuple(grads.weight_gradients[i][j] for i in range(3) for j in range(2))
        + grads.bias_gradients
    )

    assert gradients_match(analytical, numerical)


def test_dense_backward_input_gradients_match_numerical() -> None:
    layer = create_dense_layer(3, 2, ActivationType.TANH, seed=2)
    x = (0.3, 0.1, -0.4)
    cache = forward_dense(layer, x)
    grad_output = (1.0, 1.0)
    grads = backward_dense(layer, cache, grad_output)

    def loss_fn(flat_x: tuple[float, ...]) -> float:
        return sum(forward_dense(layer, flat_x).output)

    numerical = numerical_gradient(loss_fn, x)
    assert gradients_match(grads.input_gradients, numerical)


def test_forward_dense_raises_on_input_size_mismatch() -> None:
    layer = create_dense_layer(3, 2, ActivationType.RELU, seed=1)
    with pytest.raises(DLInputError):
        forward_dense(layer, (1.0, 2.0))


def test_create_dense_layer_is_deterministic_given_same_seed() -> None:
    a = create_dense_layer(3, 2, ActivationType.RELU, seed=7)
    b = create_dense_layer(3, 2, ActivationType.RELU, seed=7)
    assert a.weights == b.weights


def test_create_dense_layer_breaks_symmetry() -> None:
    """Different neurons in the same layer must not start with identical weights."""
    layer = create_dense_layer(3, 4, ActivationType.RELU, seed=1)
    columns = [tuple(row[j] for row in layer.weights) for j in range(4)]
    assert len(set(columns)) == 4


def test_apply_gradients_returns_new_layer_not_mutated() -> None:
    layer = create_dense_layer(2, 1, ActivationType.LINEAR, seed=1)
    cache = forward_dense(layer, (1.0, 1.0))
    grads = backward_dense(layer, cache, (1.0,))
    updated = apply_gradients(layer, grads, learning_rate=0.1)
    assert updated.weights != layer.weights
    assert layer.weights == create_dense_layer(2, 1, ActivationType.LINEAR, seed=1).weights


def test_dense_layer_is_immutable() -> None:
    layer = create_dense_layer(2, 1, ActivationType.LINEAR, seed=1)
    with pytest.raises(FrozenInstanceError):
        layer.bias = (99.0,)  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Network: end-to-end training, verified by solving XOR
# --------------------------------------------------------------------------- #


def _xor_network() -> Sequential:
    hidden = create_dense_layer(2, 4, ActivationType.TANH, seed=1)
    output = create_dense_layer(4, 1, ActivationType.SIGMOID, seed=2)
    return Sequential(layers=(hidden, output))


def test_network_solves_xor() -> None:
    """The classic proof a multi-layer network's nonlinearity actually works: a
    linear model cannot solve XOR, so successfully doing so is strong evidence
    the forward+backward+update pipeline is correctly implemented end-to-end."""
    network = _xor_network()
    x = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    y = ((0.0,), (1.0,), (1.0,), (0.0,))

    trained, losses = train_network(network, x, y, learning_rate=0.5, epochs=3000)

    assert losses[-1] < losses[0]
    for xi, yi in zip(x, y, strict=True):
        prediction = predict_network(trained, xi)
        assert prediction[0] == pytest.approx(yi[0], abs=0.1)


def test_train_network_loss_decreases_monotonically_on_average() -> None:
    network = _xor_network()
    x = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    y = ((0.0,), (1.0,), (1.0,), (0.0,))
    _, losses = train_network(network, x, y, learning_rate=0.5, epochs=500)
    # Not strictly monotonic every epoch, but the back half should clearly beat
    # the front half on average.
    front_half = sum(losses[:100]) / 100
    back_half = sum(losses[-100:]) / 100
    assert back_half < front_half


def test_train_step_returns_new_network_not_mutated() -> None:
    network = _xor_network()
    updated, _ = train_step(network, (0.0, 1.0), (1.0,), learning_rate=0.1)
    assert updated.layers[0].weights != network.layers[0].weights


def test_forward_network_output_matches_manual_layer_chaining() -> None:
    network = _xor_network()
    x = (0.5, 0.5)
    _, output = forward_network(network, x)

    cache1 = forward_dense(network.layers[0], x)
    cache2 = forward_dense(network.layers[1], cache1.output)
    assert output == cache2.output


def test_train_network_rejects_mismatched_sample_counts() -> None:
    network = _xor_network()
    with pytest.raises(DLInputError):
        train_network(network, ((0.0, 0.0),), ((1.0,), (0.0,)), learning_rate=0.1, epochs=10)


def test_train_network_rejects_non_positive_epochs() -> None:
    network = _xor_network()
    with pytest.raises(DLInputError):
        train_network(network, ((0.0, 0.0),), ((1.0,),), learning_rate=0.1, epochs=0)


# --------------------------------------------------------------------------- #
# Conv1D: gradient-checked, including input gradients
# --------------------------------------------------------------------------- #


def _flatten_conv(layer: Conv1DLayer) -> tuple[float, ...]:
    return (*layer.kernel, layer.bias)


def _unflatten_conv(flat: tuple[float, ...], activation: ActivationType) -> Conv1DLayer:
    return Conv1DLayer(kernel=flat[:-1], bias=flat[-1], activation=activation)


def test_conv1d_backward_matches_numerical_gradient() -> None:
    layer = create_conv1d_layer(3, ActivationType.TANH, seed=1)
    x = (0.5, -0.2, 0.9, 0.1, -0.4, 0.7)
    cache = forward_conv1d(layer, x)
    grad_output = tuple(1.0 for _ in cache.output)
    grads = backward_conv1d(layer, cache, grad_output)

    def loss_fn(flat_params: tuple[float, ...]) -> float:
        test_layer = _unflatten_conv(flat_params, ActivationType.TANH)
        return sum(forward_conv1d(test_layer, x).output)

    numerical = numerical_gradient(loss_fn, _flatten_conv(layer))
    analytical = (*grads.kernel_gradients, grads.bias_gradient)
    assert gradients_match(analytical, numerical)


def test_conv1d_input_gradients_match_numerical() -> None:
    layer = create_conv1d_layer(3, ActivationType.TANH, seed=1)
    x = (0.5, -0.2, 0.9, 0.1, -0.4, 0.7)
    cache = forward_conv1d(layer, x)
    grad_output = tuple(1.0 for _ in cache.output)
    grads = backward_conv1d(layer, cache, grad_output)

    def loss_fn(flat_x: tuple[float, ...]) -> float:
        return sum(forward_conv1d(layer, flat_x).output)

    numerical = numerical_gradient(loss_fn, x)
    assert gradients_match(grads.input_gradients, numerical)


def test_forward_conv1d_output_length() -> None:
    layer = create_conv1d_layer(3, ActivationType.RELU, seed=1)
    cache = forward_conv1d(layer, (1.0, 2.0, 3.0, 4.0, 5.0))
    assert len(cache.output) == 5 - 3 + 1


def test_forward_conv1d_raises_when_input_shorter_than_kernel() -> None:
    layer = create_conv1d_layer(5, ActivationType.RELU, seed=1)
    with pytest.raises(DLInputError):
        forward_conv1d(layer, (1.0, 2.0))


def test_apply_conv1d_gradients_returns_new_layer() -> None:
    layer = create_conv1d_layer(3, ActivationType.LINEAR, seed=1)
    cache = forward_conv1d(layer, (1.0, 2.0, 3.0, 4.0))
    grads = backward_conv1d(layer, cache, tuple(1.0 for _ in cache.output))
    updated = apply_conv1d_gradients(layer, grads, learning_rate=0.1)
    assert updated.kernel != layer.kernel


# --------------------------------------------------------------------------- #
# LSTM: forward pass verified against a hand-computed example
# --------------------------------------------------------------------------- #


def test_lstm_forward_step_matches_hand_computed_gate_equations() -> None:
    """hidden_size=1, input_size=1, all gate weights=1, bias=0 -- fully hand-tractable."""

    def unit_gate(activation: ActivationType) -> DenseLayer:
        return DenseLayer(weights=((1.0,), (1.0,)), bias=(0.0,), activation=activation)

    cell = LSTMCell(
        forget_gate=unit_gate(ActivationType.SIGMOID),
        input_gate=unit_gate(ActivationType.SIGMOID),
        candidate_gate=unit_gate(ActivationType.TANH),
        output_gate=unit_gate(ActivationType.SIGMOID),
        hidden_size=1,
    )
    prev_state = LSTMState(hidden=(0.0,), cell=(0.0,))
    x = (1.0,)

    result = lstm_forward_step(cell, x, prev_state)

    z = 1.0 * 0.0 + 1.0 * 1.0 + 0.0
    gate_value = 1 / (1 + math.exp(-z))
    candidate_value = math.tanh(z)
    expected_cell = gate_value * 0.0 + gate_value * candidate_value
    expected_hidden = gate_value * math.tanh(expected_cell)

    assert result.cell[0] == pytest.approx(expected_cell)
    assert result.hidden[0] == pytest.approx(expected_hidden)


def test_lstm_forward_sequence_length_matches_input() -> None:
    cell = create_lstm_cell(input_size=2, hidden_size=3, seed=1)
    sequence = ((1.0, 0.5), (0.2, -0.3), (0.0, 1.0))
    states = lstm_forward_sequence(cell, sequence)
    assert len(states) == 3
    assert all(len(s.hidden) == 3 for s in states)


def test_lstm_forward_sequence_uses_zero_initial_state_by_default() -> None:
    cell = create_lstm_cell(input_size=1, hidden_size=2, seed=1)
    states = lstm_forward_sequence(cell, ((1.0,),))
    manual = lstm_forward_step(cell, (1.0,), initial_lstm_state(2))
    assert states[0] == manual


def test_lstm_forward_sequence_rejects_empty_sequence() -> None:
    cell = create_lstm_cell(input_size=1, hidden_size=2, seed=1)
    with pytest.raises(DLInputError):
        lstm_forward_sequence(cell, ())


def test_create_lstm_cell_gates_are_not_identical() -> None:
    """Confirms the seed-offset design actually breaks symmetry across gates."""
    cell = create_lstm_cell(input_size=2, hidden_size=2, seed=1)
    assert cell.forget_gate.weights != cell.input_gate.weights
    assert cell.input_gate.weights != cell.candidate_gate.weights


def test_lstm_forward_step_rejects_mismatched_state_dimensions() -> None:
    cell = create_lstm_cell(input_size=1, hidden_size=2, seed=1)
    bad_state = LSTMState(hidden=(0.0,), cell=(0.0,))
    with pytest.raises(DLInputError):
        lstm_forward_step(cell, (1.0,), bad_state)


# --------------------------------------------------------------------------- #
# Attention: verified against a hand-computed example
# --------------------------------------------------------------------------- #


def test_attention_matches_hand_computed_example() -> None:
    queries = ((1.0, 0.0),)
    keys = ((1.0, 0.0), (0.0, 1.0))
    values = ((10.0,), (20.0,))

    result = scaled_dot_product_attention(queries, keys, values)

    scale = math.sqrt(2)
    score_0 = (1 * 1 + 0 * 0) / scale
    score_1 = (1 * 0 + 0 * 1) / scale
    exp_0, exp_1 = math.exp(score_0), math.exp(score_1)
    weight_0, weight_1 = exp_0 / (exp_0 + exp_1), exp_1 / (exp_0 + exp_1)
    expected_context = weight_0 * 10.0 + weight_1 * 20.0

    assert result.weights[0][0] == pytest.approx(weight_0)
    assert result.weights[0][1] == pytest.approx(weight_1)
    assert result.context[0][0] == pytest.approx(expected_context)


def test_attention_weights_sum_to_one() -> None:
    queries = ((1.0, 2.0), (0.5, -0.5))
    keys = ((1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    values = ((1.0,), (2.0,), (3.0,))
    result = scaled_dot_product_attention(queries, keys, values)
    for row in result.weights:
        assert sum(row) == pytest.approx(1.0)


def test_attention_identical_query_and_key_gives_higher_self_weight() -> None:
    """A query identical to one key should attend to it more than to dissimilar keys."""
    queries = ((1.0, 0.0),)
    keys = ((1.0, 0.0), (-1.0, 0.0))
    values = ((1.0,), (0.0,))
    result = scaled_dot_product_attention(queries, keys, values)
    assert result.weights[0][0] > result.weights[0][1]


def test_attention_rejects_empty_queries() -> None:
    with pytest.raises(DLInputError):
        scaled_dot_product_attention((), ((1.0,),), ((1.0,),))


def test_attention_rejects_mismatched_keys_and_values_count() -> None:
    with pytest.raises(DLInputError):
        scaled_dot_product_attention(((1.0,),), ((1.0,), (2.0,)), ((1.0,),))


def test_attention_rejects_mismatched_query_key_dimensionality() -> None:
    with pytest.raises(DLInputError):
        scaled_dot_product_attention(((1.0, 2.0),), ((1.0,),), ((1.0,),))
