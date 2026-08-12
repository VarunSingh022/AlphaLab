"""AlphaLab Deep Learning Engine.

A feedforward network (dense layers, real forward + backward + SGD training,
verified end-to-end by solving XOR), 1D convolution for sequence/time-series data,
an LSTM cell, and scaled dot-product attention -- the primitives the roadmap's
LSTM/Transformer/CNN/sequence-model bullets are built from.

Zero runtime dependencies -- no numpy, no autodiff engine. Every learnable
component's backward pass is verified against `gradient_check.numerical_gradient`
in its own tests, not just asserted. LSTM and attention are deliberately
forward-pass-only: full backpropagation-through-time and multi-block Transformer
training are substantially larger undertakings this pass does not attempt to
implement and verify to the same standard -- see their module docstrings.
"""

from alphalab.deep_learning.activations import (
    ActivationType,
    activation_derivative,
    apply_activation,
    relu,
    relu_derivative,
    sigmoid,
    sigmoid_derivative,
    softmax,
    tanh_activation,
    tanh_derivative,
)
from alphalab.deep_learning.attention import AttentionOutput, scaled_dot_product_attention
from alphalab.deep_learning.conv1d import (
    Conv1DForwardCache,
    Conv1DGradients,
    Conv1DLayer,
    apply_conv1d_gradients,
    backward_conv1d,
    create_conv1d_layer,
    forward_conv1d,
)
from alphalab.deep_learning.dense import (
    DenseForwardCache,
    DenseGradients,
    DenseLayer,
    apply_gradients,
    backward_dense,
    create_dense_layer,
    forward_dense,
)
from alphalab.deep_learning.exceptions import DeepLearningError, DLComputationError, DLInputError
from alphalab.deep_learning.gradient_check import gradients_match, numerical_gradient
from alphalab.deep_learning.lstm import (
    LSTMCell,
    LSTMState,
    create_lstm_cell,
    initial_lstm_state,
    lstm_forward_sequence,
    lstm_forward_step,
)
from alphalab.deep_learning.network import (
    Sequential,
    forward_network,
    predict_network,
    train_network,
    train_step,
)

__all__ = [
    "ActivationType",
    "AttentionOutput",
    "Conv1DForwardCache",
    "Conv1DGradients",
    "Conv1DLayer",
    "DLComputationError",
    "DLInputError",
    "DeepLearningError",
    "DenseForwardCache",
    "DenseGradients",
    "DenseLayer",
    "LSTMCell",
    "LSTMState",
    "Sequential",
    "activation_derivative",
    "apply_activation",
    "apply_conv1d_gradients",
    "apply_gradients",
    "backward_conv1d",
    "backward_dense",
    "create_conv1d_layer",
    "create_dense_layer",
    "create_lstm_cell",
    "forward_conv1d",
    "forward_dense",
    "forward_network",
    "gradients_match",
    "initial_lstm_state",
    "lstm_forward_sequence",
    "lstm_forward_step",
    "numerical_gradient",
    "predict_network",
    "relu",
    "relu_derivative",
    "scaled_dot_product_attention",
    "sigmoid",
    "sigmoid_derivative",
    "softmax",
    "tanh_activation",
    "tanh_derivative",
    "train_network",
    "train_step",
]
