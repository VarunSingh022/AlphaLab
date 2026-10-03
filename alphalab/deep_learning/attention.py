"""Scaled dot-product attention: forward pass, backward pass, and a trainable layer.

Until v3.12 this module was forward-only (ledger SCF-004). It now carries the
backward pass of ``Attention(Q, K, V) = softmax(QK^T / sqrt(d_k)) V`` and a
single-head self-attention layer whose query, key and value projections are
trained through it -- the block a Transformer stacks. Every gradient is checked
against central differences in ``tests/unit/deep_learning/test_sequence_backprop.py``.

The backward pass
-----------------

With ``S = QK^T / sqrt(d_k)``, ``A = softmax(S)`` row by row and ``C = AV``, and
``dC`` the gradient arriving at the context:

.. code-block:: text

    dV = A^T dC
    dA = dC V^T
    dS = A * (dA - rowsum(A * dA))      -- the softmax Jacobian, one row at a time
    dQ = dS K / sqrt(d_k)
    dK = dS^T Q / sqrt(d_k)

What is not here: masking, several heads, layer normalisation and the
feed-forward sub-block. Each is composed from this layer and
:mod:`alphalab.deep_learning.dense` by a caller that wants one; none needs a
gradient this package does not already check.
"""

import math
from dataclasses import dataclass

from alphalab.deep_learning.activations import ActivationType, softmax
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

Vectors = tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class AttentionOutput:
    """Result of a scaled dot-product attention forward pass.

    Attributes:
        context: The attention-weighted combination of values, one vector per
            query, each of dimension d_v (the value dimension).
        weights: The attention weight matrix, shape (n_queries, n_keys), each row
            summing to 1 -- kept for inspection/interpretability, and read by the
            backward pass.
    """

    context: Vectors
    weights: Vectors


@dataclass(frozen=True, slots=True)
class AttentionGradients:
    """The gradient of a loss with respect to an attention call's three inputs."""

    queries: Vectors
    keys: Vectors
    values: Vectors


def scaled_dot_product_attention(
    queries: Vectors,
    keys: Vectors,
    values: Vectors,
) -> AttentionOutput:
    """Computes Attention(Q, K, V) = softmax(QK^T / sqrt(d_k)) V.

    Args:
        queries: n_queries vectors of dimension d_k.
        keys: n_keys vectors of dimension d_k (must match queries' dimension).
        values: n_keys vectors of dimension d_v (one value per key, d_v need not
            equal d_k).

    Raises:
        DLInputError: If queries, keys, or values are empty; if keys and values
            have different counts; or if queries and keys have mismatched
            dimensionality.
    """
    if not queries or not keys or not values:
        raise DLInputError("queries, keys, and values cannot be empty.")
    if len(keys) != len(values):
        raise DLInputError(f"keys has {len(keys)} entries but values has {len(values)}.")

    d_k = len(queries[0])
    if any(len(q) != d_k for q in queries) or any(len(k) != d_k for k in keys):
        raise DLInputError("Every query and key must have the same dimensionality.")

    scale = math.sqrt(d_k)
    scores = tuple(
        tuple(sum(q[i] * k[i] for i in range(d_k)) / scale for k in keys) for q in queries
    )
    weights = tuple(softmax(row) for row in scores)

    d_v = len(values[0])
    context = tuple(
        tuple(sum(weight_row[j] * values[j][d] for j in range(len(values))) for d in range(d_v))
        for weight_row in weights
    )

    return AttentionOutput(context=context, weights=weights)


def scaled_dot_product_attention_backward(
    queries: Vectors,
    keys: Vectors,
    values: Vectors,
    output: AttentionOutput,
    grad_context: Vectors,
) -> AttentionGradients:
    """The gradients of a loss with respect to Q, K and V, given ``dL/dC``.

    Args:
        queries: The queries the forward pass was given.
        keys: Its keys.
        values: Its values.
        output: What :func:`scaled_dot_product_attention` returned for them.
        grad_context: ``dL/dC``, one vector per query, each of dimension d_v.

    Raises:
        DLInputError: If ``grad_context`` does not have the context's shape.
    """
    n_queries, n_keys = len(queries), len(keys)
    d_k, d_v = len(queries[0]), len(values[0])
    if len(grad_context) != n_queries or any(len(row) != d_v for row in grad_context):
        raise DLInputError(f"grad_context must be {n_queries} vectors of {d_v} entries.")
    weights = output.weights
    scale = math.sqrt(d_k)

    grad_values = tuple(
        tuple(sum(weights[q][j] * grad_context[q][d] for q in range(n_queries)) for d in range(d_v))
        for j in range(n_keys)
    )
    grad_scores: list[tuple[float, ...]] = []
    for q in range(n_queries):
        grad_weights = [
            sum(grad_context[q][d] * values[j][d] for d in range(d_v)) for j in range(n_keys)
        ]
        through = sum(weights[q][j] * grad_weights[j] for j in range(n_keys))
        grad_scores.append(
            tuple(weights[q][j] * (grad_weights[j] - through) for j in range(n_keys))
        )
    grad_queries = tuple(
        tuple(
            sum(grad_scores[q][j] * keys[j][i] for j in range(n_keys)) / scale for i in range(d_k)
        )
        for q in range(n_queries)
    )
    grad_keys = tuple(
        tuple(
            sum(grad_scores[q][j] * queries[q][i] for q in range(n_queries)) / scale
            for i in range(d_k)
        )
        for j in range(n_keys)
    )
    return AttentionGradients(queries=grad_queries, keys=grad_keys, values=grad_values)


# --------------------------------------------------------------------------- #
# A trainable self-attention layer
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelfAttentionLayer:
    """Single-head self-attention: a sequence attends over itself.

    Each projection is a linear :class:`~alphalab.deep_learning.dense.DenseLayer`
    applied to every element of the sequence.

    Attributes:
        query: Projects an element to its query.
        key: Projects an element to its key.
        value: Projects an element to its value.
    """

    query: DenseLayer
    key: DenseLayer
    value: DenseLayer


@dataclass(frozen=True, slots=True)
class SelfAttentionCache:
    """A forward pass's values, kept for the backward pass."""

    query: tuple[DenseForwardCache, ...]
    key: tuple[DenseForwardCache, ...]
    value: tuple[DenseForwardCache, ...]
    attention: AttentionOutput


@dataclass(frozen=True, slots=True)
class SelfAttentionGradients:
    """The gradient of a loss with respect to a self-attention layer and its input.

    Attributes:
        query: The query projection's weight and bias gradients, summed over the
            sequence; ``input_gradients`` is empty.
        key: Likewise for the key projection.
        value: Likewise for the value projection.
        input_gradients: ``dL/dx`` for every element of the sequence, in order.
    """

    query: DenseGradients
    key: DenseGradients
    value: DenseGradients
    input_gradients: Vectors


def create_self_attention_layer(
    input_size: int, key_size: int, value_size: int, seed: int
) -> SelfAttentionLayer:
    """A seeded layer whose three projections are initialised differently.

    Raises:
        DLInputError: If a size is not positive.
    """
    return SelfAttentionLayer(
        query=create_dense_layer(input_size, key_size, ActivationType.LINEAR, seed=seed),
        key=create_dense_layer(input_size, key_size, ActivationType.LINEAR, seed=seed + 1),
        value=create_dense_layer(input_size, value_size, ActivationType.LINEAR, seed=seed + 2),
    )


def forward_self_attention(layer: SelfAttentionLayer, sequence: Vectors) -> SelfAttentionCache:
    """Projects every element, then attends: one context vector per element.

    Raises:
        DLInputError: If the sequence is empty or an element is mis-sized.
    """
    if not sequence:
        raise DLInputError("sequence cannot be empty.")
    query = tuple(forward_dense(layer.query, x) for x in sequence)
    key = tuple(forward_dense(layer.key, x) for x in sequence)
    value = tuple(forward_dense(layer.value, x) for x in sequence)
    attention = scaled_dot_product_attention(
        tuple(cache.output for cache in query),
        tuple(cache.output for cache in key),
        tuple(cache.output for cache in value),
    )
    return SelfAttentionCache(query=query, key=key, value=value, attention=attention)


def _sum_over_sequence(
    layer: DenseLayer,
    caches: tuple[DenseForwardCache, ...],
    gradients: Vectors,
    input_gradients: list[list[float]],
) -> DenseGradients:
    n_inputs, n_outputs = len(layer.weights), len(layer.bias)
    weights = [[0.0] * n_outputs for _ in range(n_inputs)]
    bias = [0.0] * n_outputs
    for position, (cache, gradient) in enumerate(zip(caches, gradients, strict=True)):
        step = backward_dense(layer, cache, gradient)
        for i in range(n_inputs):
            row = weights[i]
            for j in range(n_outputs):
                row[j] += step.weight_gradients[i][j]
            input_gradients[position][i] += step.input_gradients[i]
        for j in range(n_outputs):
            bias[j] += step.bias_gradients[j]
    return DenseGradients(
        weight_gradients=tuple(tuple(row) for row in weights),
        bias_gradients=tuple(bias),
        input_gradients=(),
    )


def backward_self_attention(
    layer: SelfAttentionLayer, cache: SelfAttentionCache, grad_context: Vectors
) -> SelfAttentionGradients:
    """The gradients of a loss with respect to the layer and its input, given ``dL/dC``.

    Raises:
        DLInputError: If ``grad_context`` does not have the context's shape.
    """
    through = scaled_dot_product_attention_backward(
        tuple(item.output for item in cache.query),
        tuple(item.output for item in cache.key),
        tuple(item.output for item in cache.value),
        cache.attention,
        grad_context,
    )
    size = len(layer.query.weights)
    input_gradients = [[0.0] * size for _ in cache.query]
    return SelfAttentionGradients(
        query=_sum_over_sequence(layer.query, cache.query, through.queries, input_gradients),
        key=_sum_over_sequence(layer.key, cache.key, through.keys, input_gradients),
        value=_sum_over_sequence(layer.value, cache.value, through.values, input_gradients),
        input_gradients=tuple(tuple(row) for row in input_gradients),
    )


def apply_self_attention_gradients(
    layer: SelfAttentionLayer, gradients: SelfAttentionGradients, learning_rate: float
) -> SelfAttentionLayer:
    """Returns a new layer with every projection moved by one SGD step.

    Raises:
        DLInputError: If learning_rate is not positive.
    """
    return SelfAttentionLayer(
        query=apply_gradients(layer.query, gradients.query, learning_rate),
        key=apply_gradients(layer.key, gradients.key, learning_rate),
        value=apply_gradients(layer.value, gradients.value, learning_rate),
    )
