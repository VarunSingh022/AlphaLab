"""Scaled dot-product attention: forward pass only.

Same scope decision as `alphalab.deep_learning.lstm`: this implements and verifies
the forward computation Transformers are built on (Attention(Q,K,V) =
softmax(QK^T / sqrt(d_k))V), not a full multi-head, multi-block Transformer with
trained projection matrices and backpropagation through the attention weights
themselves. The Q/K/V projections a real Transformer would use are ordinary
`alphalab.deep_learning.dense.DenseLayer`s (LINEAR activation) applied before
calling this function -- already gradient-checked elsewhere in this package.
"""

import math
from dataclasses import dataclass

from alphalab.deep_learning.activations import softmax
from alphalab.deep_learning.exceptions import DLInputError


@dataclass(frozen=True, slots=True)
class AttentionOutput:
    """Result of a scaled dot-product attention forward pass.

    Attributes:
        context: The attention-weighted combination of values, one vector per
            query, each of dimension d_v (the value dimension).
        weights: The attention weight matrix, shape (n_queries, n_keys), each row
            summing to 1 -- kept for inspection/interpretability, not just the
            final output.
    """

    context: tuple[tuple[float, ...], ...]
    weights: tuple[tuple[float, ...], ...]


def scaled_dot_product_attention(
    queries: tuple[tuple[float, ...], ...],
    keys: tuple[tuple[float, ...], ...],
    values: tuple[tuple[float, ...], ...],
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
