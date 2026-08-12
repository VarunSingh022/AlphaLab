"""LSTM (Long Short-Term Memory) cell: forward pass only.

Scope is deliberately limited to the forward pass. Full backpropagation-through-time
for an LSTM -- gradients flowing backward through the cell state across every
timestep in a sequence -- is a substantially larger undertaking than the
single-timestep backward passes verified elsewhere in this package (dense, conv1d),
and one this pass does not attempt to implement and verify to the same standard.
Building an unverified BPTT implementation would be worse than not having one: a
network that trains but has a subtly wrong gradient is a much harder bug to catch
than a clearly-labeled missing feature.

Each gate reuses `alphalab.deep_learning.dense.DenseLayer`, whose forward pass is
already gradient-checked -- the LSTM equations only need to be verified for the
cell/hidden state update itself, not re-derive dense-layer correctness.
"""

from dataclasses import dataclass

from alphalab.deep_learning.activations import ActivationType, tanh_activation
from alphalab.deep_learning.dense import DenseLayer, create_dense_layer, forward_dense
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


def create_lstm_cell(input_size: int, hidden_size: int, seed: int = 42) -> LSTMCell:
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


def lstm_forward_step(cell: LSTMCell, x: tuple[float, ...], prev_state: LSTMState) -> LSTMState:
    """Computes one LSTM timestep's new hidden and cell state.

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
    forget = forward_dense(cell.forget_gate, combined).output
    input_ = forward_dense(cell.input_gate, combined).output
    candidate = forward_dense(cell.candidate_gate, combined).output
    output = forward_dense(cell.output_gate, combined).output

    new_cell = tuple(
        forget[j] * prev_state.cell[j] + input_[j] * candidate[j] for j in range(cell.hidden_size)
    )
    new_hidden = tuple(output[j] * tanh_activation(new_cell[j]) for j in range(cell.hidden_size))

    return LSTMState(hidden=new_hidden, cell=new_cell)


def lstm_forward_sequence(
    cell: LSTMCell,
    x_sequence: tuple[tuple[float, ...], ...],
    initial_state: LSTMState | None = None,
) -> tuple[LSTMState, ...]:
    """Runs an LSTM cell over a full sequence, returning the state at every timestep.

    Raises:
        DLInputError: If x_sequence is empty.
    """
    if not x_sequence:
        raise DLInputError("x_sequence cannot be empty.")

    state = initial_state if initial_state is not None else initial_lstm_state(cell.hidden_size)
    states = []
    for x_t in x_sequence:
        state = lstm_forward_step(cell, x_t, state)
        states.append(state)
    return tuple(states)
