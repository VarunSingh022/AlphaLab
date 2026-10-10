"""AlphaLab Deep Learning Engine.

A feedforward network (dense layers, real forward + backward + SGD training,
verified end-to-end by solving XOR), 1D convolution for sequence/time-series data,
an LSTM cell, and scaled dot-product attention -- the primitives the roadmap's
LSTM/Transformer/CNN/sequence-model bullets are built from.

Zero runtime dependencies -- no numpy, no autodiff engine. Every learnable
component's backward pass is verified against `gradient_check.numerical_gradient`
in its own tests, not just asserted. Since v3.12 that includes the LSTM --
backpropagation through time over a whole sequence, and a trainable sequence
regressor -- and attention: the backward pass of softmax(QK^T/sqrt(d))V and a
self-attention layer with trained query, key and value projections (ledger
SCF-004). Until then both were forward-only, deliberately, because an unchecked
gradient is worse than none.
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
from alphalab.deep_learning.attention import (
    AttentionGradients,
    AttentionOutput,
    SelfAttentionCache,
    SelfAttentionGradients,
    SelfAttentionLayer,
    apply_self_attention_gradients,
    backward_self_attention,
    create_self_attention_layer,
    forward_self_attention,
    scaled_dot_product_attention,
    scaled_dot_product_attention_backward,
)
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
    LSTMGradients,
    LSTMRegressor,
    LSTMState,
    LSTMStepCache,
    apply_lstm_gradients,
    create_lstm_cell,
    create_lstm_regressor,
    initial_lstm_state,
    lstm_backward,
    lstm_forward_sequence,
    lstm_forward_step,
    lstm_sequence,
    lstm_step,
    predict_lstm,
    train_lstm,
    train_lstm_step,
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
    "AttentionGradients",
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
    "LSTMGradients",
    "LSTMRegressor",
    "LSTMState",
    "LSTMStepCache",
    "SelfAttentionCache",
    "SelfAttentionGradients",
    "SelfAttentionLayer",
    "Sequential",
    "activation_derivative",
    "apply_activation",
    "apply_conv1d_gradients",
    "apply_gradients",
    "apply_lstm_gradients",
    "apply_self_attention_gradients",
    "backward_conv1d",
    "backward_dense",
    "backward_self_attention",
    "create_conv1d_layer",
    "create_dense_layer",
    "create_lstm_cell",
    "create_lstm_regressor",
    "create_self_attention_layer",
    "forward_conv1d",
    "forward_dense",
    "forward_network",
    "forward_self_attention",
    "gradients_match",
    "initial_lstm_state",
    "lstm_backward",
    "lstm_forward_sequence",
    "lstm_forward_step",
    "lstm_sequence",
    "lstm_step",
    "numerical_gradient",
    "predict_lstm",
    "predict_network",
    "relu",
    "relu_derivative",
    "scaled_dot_product_attention",
    "scaled_dot_product_attention_backward",
    "sigmoid",
    "sigmoid_derivative",
    "softmax",
    "tanh_activation",
    "tanh_derivative",
    "train_lstm",
    "train_lstm_step",
    "train_network",
    "train_step",
]
