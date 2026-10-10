"""LSTM (Long Short-Term Memory): forward pass, backpropagation through time, training.

Until v3.12 this module was forward-only, and said why: an unverified
backpropagation-through-time would be worse than none, because a network that
trains on a subtly wrong gradient is a much harder bug to find than a missing
feature (ledger SCF-004). The backward pass is now here, and it is held to the
standard that argument set -- every gradient it returns, for every weight, every
bias, every input and the initial state, is checked against central differences
in the test suite (``tests/unit/deep_learning/test_sequence_backprop.py``).

How the gradient is computed
----------------------------

Each gate is a :class:`~alphalab.deep_learning.dense.DenseLayer` over the
concatenation ``[h_{t-1}, x_t]``, and its backward pass is
:func:`~alphalab.deep_learning.dense.backward_dense`, which is gradient-checked
on its own. What this module adds is only the cell's own algebra, run backwards
from the last step to the first:

.. code-block:: text

    dh   = dL/dh_t + (dL/dh_t through step t+1)
    do   = dh * tanh(c_t)                      -- into the output gate
    dc   = (dL/dc_t through step t+1) + dh * o_t * (1 - tanh(c_t)^2)
    df   = dc * c_{t-1}     di = dc * g_t     dg = dc * i_t
    dc_{t-1} = dc * f_t
    [dh_{t-1}, dx_t] = sum over the four gates of their input gradients

Weight and bias gradients are summed over the steps, because one set of
parameters is applied at every step. Nothing is truncated: the gradient flows
through the whole sequence it was given.
"""

from dataclasses import dataclass

from alphalab.deep_learning.activations import ActivationType, tanh_activation
from alphalab.deep_learning.dense import (
    DenseForwardCache,
    DenseGradients,
    DenseLayer,
    apply_gradients,
    backward_dense,
    create_dense_layer,
    forward_dense,
)
from alphalab.deep_learning.exceptions import DLInputError


@dataclass(frozen=True, slots=True)
class LSTMCell:
    """An LSTM cell's learnable parameters: one dense sub-layer per gate.

    Each gate takes the concatenation of the previous hidden state and the current
    input, and outputs a vector of size hidden_size.

    Attributes:
        forget_gate: Sigmoid gate controlling how much prior cell state to retain.
        input_gate: Sigmoid gate controlling how much new candidate to admit.
        candidate_gate: Tanh gate producing the new candidate cell content.
        output_gate: Sigmoid gate controlling how much cell state reaches the
            hidden state.
        hidden_size: Dimensionality of the hidden and cell states.
    """

    forget_gate: DenseLayer
    input_gate: DenseLayer
    candidate_gate: DenseLayer
    output_gate: DenseLayer
    hidden_size: int


@dataclass(frozen=True, slots=True)
class LSTMState:
    """Hidden and cell state at a single timestep."""

    hidden: tuple[float, ...]
    cell: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LSTMStepCache:
    """One step's forward values, kept for the backward pass.

    Attributes:
        previous: The state the step started from.
        forget: The forget gate's forward cache.
        input: The input gate's forward cache.
        candidate: The candidate gate's forward cache.
        output: The output gate's forward cache.
        state: The state the step produced.
        cell_tanh: ``tanh`` of the new cell state, per unit.
    """

    previous: LSTMState
    forget: DenseForwardCache
    input: DenseForwardCache
    candidate: DenseForwardCache
    output: DenseForwardCache
    state: LSTMState
    cell_tanh: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LSTMGradients:
    """The gradient of a loss with respect to everything an LSTM sequence read.

    Attributes:
        forget_gate: The forget gate's weight and bias gradients, summed over
            every step. ``input_gradients`` is empty: what flows into the inputs
            is on :attr:`input_gradients` below, per step.
        input_gate: Likewise for the input gate.
        candidate_gate: Likewise for the candidate gate.
        output_gate: Likewise for the output gate.
        input_gradients: ``dL/dx_t`` for every step, in order.
        initial_hidden: ``dL/dh_0`` -- the gradient into the state the sequence
            started from.
        initial_cell: ``dL/dc_0``.
    """

    forget_gate: DenseGradients
    input_gate: DenseGradients
    candidate_gate: DenseGradients
    output_gate: DenseGradients
    input_gradients: tuple[tuple[float, ...], ...]
    initial_hidden: tuple[float, ...]
    initial_cell: tuple[float, ...]


def create_lstm_cell(input_size: int, hidden_size: int, seed: int) -> LSTMCell:
    """Creates an LSTMCell with deterministic, seeded weight initialization.

    Each gate gets a distinct seed offset so the four gates are not initialized
    identically, avoiding the same symmetry problem `create_dense_layer` avoids.

    Raises:
        DLInputError: If input_size or hidden_size are not positive.
    """
    if input_size <= 0 or hidden_size <= 0:
        raise DLInputError(
            f"input_size and hidden_size must be positive, got {input_size}, {hidden_size}."
        )

    combined_size = input_size + hidden_size
    return LSTMCell(
        forget_gate=create_dense_layer(
            combined_size, hidden_size, ActivationType.SIGMOID, seed=seed
        ),
        input_gate=create_dense_layer(
            combined_size, hidden_size, ActivationType.SIGMOID, seed=seed + 1
        ),
        candidate_gate=create_dense_layer(
            combined_size, hidden_size, ActivationType.TANH, seed=seed + 2
        ),
        output_gate=create_dense_layer(
            combined_size, hidden_size, ActivationType.SIGMOID, seed=seed + 3
        ),
        hidden_size=hidden_size,
    )


def initial_lstm_state(hidden_size: int) -> LSTMState:
    """Returns a zero-initialized LSTMState, the standard starting point for a sequence."""
    zeros = tuple(0.0 for _ in range(hidden_size))
    return LSTMState(hidden=zeros, cell=zeros)


def lstm_step(cell: LSTMCell, x: tuple[float, ...], prev_state: LSTMState) -> LSTMStepCache:
    """One LSTM timestep, with everything the backward pass needs.

    f_t = sigmoid(W_f . [h_{t-1}, x_t] + b_f)   -- forget gate
    i_t = sigmoid(W_i . [h_{t-1}, x_t] + b_i)   -- input gate
    g_t = tanh(W_g . [h_{t-1}, x_t] + b_g)      -- candidate cell content
    o_t = sigmoid(W_o . [h_{t-1}, x_t] + b_o)   -- output gate
    c_t = f_t * c_{t-1} + i_t * g_t             -- new cell state
    h_t = o_t * tanh(c_t)                       -- new hidden state

    Raises:
        DLInputError: If x's length doesn't match the cell's expected input size,
            or prev_state's dimensions don't match hidden_size.
    """
    if len(prev_state.hidden) != cell.hidden_size or len(prev_state.cell) != cell.hidden_size:
        raise DLInputError(f"prev_state dimensions must match hidden_size ({cell.hidden_size}).")

    combined = prev_state.hidden + x
    forget = forward_dense(cell.forget_gate, combined)
    input_ = forward_dense(cell.input_gate, combined)
    candidate = forward_dense(cell.candidate_gate, combined)
    output = forward_dense(cell.output_gate, combined)

    new_cell = tuple(
        forget.output[j] * prev_state.cell[j] + input_.output[j] * candidate.output[j]
        for j in range(cell.hidden_size)
    )
    cell_tanh = tuple(tanh_activation(value) for value in new_cell)
    new_hidden = tuple(output.output[j] * cell_tanh[j] for j in range(cell.hidden_size))

    return LSTMStepCache(
        previous=prev_state,
        forget=forget,
        input=input_,
        candidate=candidate,
        output=output,
        state=LSTMState(hidden=new_hidden, cell=new_cell),
        cell_tanh=cell_tanh,
    )


def lstm_forward_step(cell: LSTMCell, x: tuple[float, ...], prev_state: LSTMState) -> LSTMState:
    """Computes one LSTM timestep's new hidden and cell state; see :func:`lstm_step`.

    Raises:
        DLInputError: If x's length doesn't match the cell's expected input size,
            or prev_state's dimensions don't match hidden_size.
    """
    return lstm_step(cell, x, prev_state).state


def lstm_sequence(
    cell: LSTMCell,
    x_sequence: tuple[tuple[float, ...], ...],
    initial_state: LSTMState | None = None,
) -> tuple[LSTMStepCache, ...]:
    """Runs an LSTM cell over a full sequence, keeping every step for the backward pass.

    Raises:
        DLInputError: If x_sequence is empty.
    """
    if not x_sequence:
        raise DLInputError("x_sequence cannot be empty.")

    state = initial_state if initial_state is not None else initial_lstm_state(cell.hidden_size)
    steps = []
    for x_t in x_sequence:
        step = lstm_step(cell, x_t, state)
        steps.append(step)
        state = step.state
    return tuple(steps)


def lstm_forward_sequence(
    cell: LSTMCell,
    x_sequence: tuple[tuple[float, ...], ...],
    initial_state: LSTMState | None = None,
) -> tuple[LSTMState, ...]:
    """Runs an LSTM cell over a full sequence, returning the state at every timestep.

    Raises:
        DLInputError: If x_sequence is empty.
    """
    return tuple(step.state for step in lstm_sequence(cell, x_sequence, initial_state))


def _zeros(rows: int, columns: int) -> list[list[float]]:
    return [[0.0] * columns for _ in range(rows)]


def _accumulate(weights: list[list[float]], bias: list[float], gradients: DenseGradients) -> None:
    for i, row in enumerate(gradients.weight_gradients):
        target = weights[i]
        for j, value in enumerate(row):
            target[j] += value
    for j, value in enumerate(gradients.bias_gradients):
        bias[j] += value


def _summed(weights: list[list[float]], bias: list[float]) -> DenseGradients:
    return DenseGradients(
        weight_gradients=tuple(tuple(row) for row in weights),
        bias_gradients=tuple(bias),
        input_gradients=(),
    )


def lstm_backward(
    cell: LSTMCell,
    steps: tuple[LSTMStepCache, ...],
    grad_hidden: tuple[tuple[float, ...], ...],
    grad_final_cell: tuple[float, ...] | None = None,
) -> LSTMGradients:
    """Backpropagation through time over a sequence :func:`lstm_sequence` ran.

    Args:
        cell: The cell the sequence was run with.
        steps: Its steps, in order.
        grad_hidden: ``dL/dh_t`` for every step, in order -- zeros for a step
            the loss does not read directly. A loss on the last hidden state
            alone is zeros everywhere but the last entry.
        grad_final_cell: ``dL/dc_T`` when the loss reads the final cell state;
            ``None`` when it does not.

    Raises:
        DLInputError: If ``steps`` is empty, or a gradient's shape does not match
            the sequence or the cell.
    """
    if not steps:
        raise DLInputError("steps cannot be empty.")
    if len(grad_hidden) != len(steps):
        raise DLInputError(
            f"grad_hidden has {len(grad_hidden)} entries for a sequence of {len(steps)} steps."
        )
    size = cell.hidden_size
    if any(len(gradient) != size for gradient in grad_hidden):
        raise DLInputError(f"Every hidden-state gradient must have {size} entries.")
    if grad_final_cell is not None and len(grad_final_cell) != size:
        raise DLInputError(f"grad_final_cell must have {size} entries.")

    combined = len(cell.forget_gate.weights)
    sums = {
        gate: (_zeros(combined, size), [0.0] * size)
        for gate in ("forget", "input", "candidate", "output")
    }
    input_gradients: list[tuple[float, ...]] = [()] * len(steps)
    dh_next = [0.0] * size
    dc_next = [0.0] * size if grad_final_cell is None else list(grad_final_cell)

    for t in range(len(steps) - 1, -1, -1):
        step = steps[t]
        forget, input_, candidate, output = (
            step.forget.output,
            step.input.output,
            step.candidate.output,
            step.output.output,
        )
        dh = [grad_hidden[t][j] + dh_next[j] for j in range(size)]
        d_output = tuple(dh[j] * step.cell_tanh[j] for j in range(size))
        dc = [
            dc_next[j] + dh[j] * output[j] * (1.0 - step.cell_tanh[j] * step.cell_tanh[j])
            for j in range(size)
        ]
        d_forget = tuple(dc[j] * step.previous.cell[j] for j in range(size))
        d_input = tuple(dc[j] * candidate[j] for j in range(size))
        d_candidate = tuple(dc[j] * input_[j] for j in range(size))

        d_combined = [0.0] * combined
        for name, layer, cache, gradient in (
            ("forget", cell.forget_gate, step.forget, d_forget),
            ("input", cell.input_gate, step.input, d_input),
            ("candidate", cell.candidate_gate, step.candidate, d_candidate),
            ("output", cell.output_gate, step.output, d_output),
        ):
            gate = backward_dense(layer, cache, gradient)
            _accumulate(*sums[name], gate)
            for i, value in enumerate(gate.input_gradients):
                d_combined[i] += value

        dh_next = d_combined[:size]
        input_gradients[t] = tuple(d_combined[size:])
        dc_next = [dc[j] * forget[j] for j in range(size)]

    return LSTMGradients(
        forget_gate=_summed(*sums["forget"]),
        input_gate=_summed(*sums["input"]),
        candidate_gate=_summed(*sums["candidate"]),
        output_gate=_summed(*sums["output"]),
        input_gradients=tuple(input_gradients),
        initial_hidden=tuple(dh_next),
        initial_cell=tuple(dc_next),
    )


def apply_lstm_gradients(
    cell: LSTMCell, gradients: LSTMGradients, learning_rate: float
) -> LSTMCell:
    """Returns a new cell with every gate moved by one SGD step.

    Raises:
        DLInputError: If learning_rate is not positive.
    """
    return LSTMCell(
        forget_gate=apply_gradients(cell.forget_gate, gradients.forget_gate, learning_rate),
        input_gate=apply_gradients(cell.input_gate, gradients.input_gate, learning_rate),
        candidate_gate=apply_gradients(
            cell.candidate_gate, gradients.candidate_gate, learning_rate
        ),
        output_gate=apply_gradients(cell.output_gate, gradients.output_gate, learning_rate),
        hidden_size=cell.hidden_size,
    )


# --------------------------------------------------------------------------- #
# A trainable sequence model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LSTMRegressor:
    """An LSTM read by a dense head on its last hidden state: sequence in, vector out.

    Attributes:
        cell: The recurrent cell.
        head: The dense layer mapping the last hidden state to the prediction.
    """

    cell: LSTMCell
    head: DenseLayer


def create_lstm_regressor(
    input_size: int,
    hidden_size: int,
    output_size: int,
    seed: int,
    head_activation: ActivationType = ActivationType.LINEAR,
) -> LSTMRegressor:
    """A seeded LSTM and dense head -- deterministic for one seed.

    Raises:
        DLInputError: If a size is not positive.
    """
    return LSTMRegressor(
        cell=create_lstm_cell(input_size, hidden_size, seed),
        head=create_dense_layer(hidden_size, output_size, head_activation, seed=seed + 4),
    )


def predict_lstm(
    model: LSTMRegressor, x_sequence: tuple[tuple[float, ...], ...]
) -> tuple[float, ...]:
    """The head's output on the last hidden state of ``x_sequence``.

    Raises:
        DLInputError: If x_sequence is empty or mis-shaped.
    """
    steps = lstm_sequence(model.cell, x_sequence)
    return forward_dense(model.head, steps[-1].state.hidden).output


def train_lstm_step(
    model: LSTMRegressor,
    x_sequence: tuple[tuple[float, ...], ...],
    target: tuple[float, ...],
    learning_rate: float,
) -> tuple[LSTMRegressor, float]:
    """One forward pass, backpropagation through the whole sequence, and an SGD step.

    The loss is the mean squared error of the prediction, as
    :func:`~alphalab.deep_learning.network.train_step` uses.

    Returns:
        The updated model and this sample's loss before the update.

    Raises:
        DLInputError: If shapes mismatch, or learning_rate is not positive.
    """
    if learning_rate <= 0:
        raise DLInputError(f"learning_rate must be positive, got {learning_rate}.")
    steps = lstm_sequence(model.cell, x_sequence)
    head_cache = forward_dense(model.head, steps[-1].state.hidden)
    prediction = head_cache.output
    if len(prediction) != len(target):
        raise DLInputError(f"prediction has {len(prediction)} values but target has {len(target)}.")
    n = len(prediction)
    loss = sum((p - y) ** 2 for p, y in zip(prediction, target, strict=True)) / n
    grad_prediction = tuple(2.0 * (p - y) / n for p, y in zip(prediction, target, strict=True))

    head_gradients = backward_dense(model.head, head_cache, grad_prediction)
    zeros = tuple(0.0 for _ in range(model.cell.hidden_size))
    grad_hidden = (*((zeros,) * (len(steps) - 1)), head_gradients.input_gradients)
    cell_gradients = lstm_backward(model.cell, steps, grad_hidden)

    return (
        LSTMRegressor(
            cell=apply_lstm_gradients(model.cell, cell_gradients, learning_rate),
            head=apply_gradients(model.head, head_gradients, learning_rate),
        ),
        loss,
    )


def train_lstm(
    model: LSTMRegressor,
    sequences: tuple[tuple[tuple[float, ...], ...], ...],
    targets: tuple[tuple[float, ...], ...],
    learning_rate: float,
    epochs: int,
) -> tuple[LSTMRegressor, tuple[float, ...]]:
    """Trains by per-sample SGD over the sequences, in order, for ``epochs`` passes.

    Returns the trained model and each epoch's mean loss, in order.

    Raises:
        DLInputError: If sequences and targets differ in count, are empty, or
            epochs is not positive.
    """
    if not sequences or not targets:
        raise DLInputError("sequences and targets cannot be empty.")
    if len(sequences) != len(targets):
        raise DLInputError(f"{len(sequences)} sequences but {len(targets)} targets.")
    if epochs <= 0:
        raise DLInputError(f"epochs must be positive, got {epochs}.")

    losses: list[float] = []
    for _ in range(epochs):
        epoch: list[float] = []
        for sequence, target in zip(sequences, targets, strict=True):
            model, loss = train_lstm_step(model, sequence, target, learning_rate)
            epoch.append(loss)
        losses.append(sum(epoch) / len(epoch))
    return model, tuple(losses)
