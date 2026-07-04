"""Pure Python, deterministic analytical portfolio optimization routines."""

from collections.abc import Mapping, Sequence

from alphalab.portfolio_optimizer.exceptions import OptimizationError


def _invert_matrix(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    """Pure Python Gauss-Jordan elimination matrix inversion without external dependencies."""
    n = len(matrix)
    if n == 0:
        return ()

    mat = [list(row) for row in matrix]
    inv = [[float(i == j) for j in range(n)] for i in range(n)]

    for i in range(n):
        pivot = mat[i][i]
        if pivot == 0:
            for k in range(i + 1, n):
                if mat[k][i] != 0:
                    mat[i], mat[k] = mat[k], mat[i]
                    inv[i], inv[k] = inv[k], inv[i]
                    pivot = mat[i][i]
                    break
            if pivot == 0:
                raise OptimizationError("Singular matrix cannot be inverted.")

        for j in range(n):
            mat[i][j] /= pivot
            inv[i][j] /= pivot

        for k in range(n):
            if k != i:
                factor = mat[k][i]
                for j in range(n):
                    mat[k][j] -= factor * mat[i][j]
                    inv[k][j] -= factor * inv[i][j]

    return tuple(tuple(row) for row in inv)


def _matrix_vector_multiply(
    matrix: Sequence[Sequence[float]], vector: Sequence[float]
) -> tuple[float, ...]:
    """Multiplies a 2D matrix by a 1D column vector."""
    return tuple(
        sum(matrix[i][j] * vector[j] for j in range(len(vector))) for i in range(len(matrix))
    )


def optimize_equal_weight(symbols: Sequence[str]) -> dict[str, float]:
    """Generates a perfectly distributed equal-weight allocation."""
    if not symbols:
        return {}
    w = 1.0 / len(symbols)
    return dict.fromkeys(symbols, w)


def optimize_inverse_volatility(
    symbols: Sequence[str], volatilities: Mapping[str, float]
) -> dict[str, float]:
    """Weights assets inversely proportional to their individual volatilities."""
    if not symbols:
        return {}
    inv_vols = []
    for s in symbols:
        vol = volatilities.get(s, 1.0)
        if vol <= 0:
            raise OptimizationError(f"Volatility for {s} must be > 0.")
        inv_vols.append(1.0 / vol)

    total_inv_vol = sum(inv_vols)
    return {s: inv / total_inv_vol for s, inv in zip(symbols, inv_vols, strict=True)}


def optimize_minimum_variance(
    symbols: Sequence[str], covariance_matrix: Sequence[Sequence[float]]
) -> dict[str, float]:
    """Analytical Minimum Variance Portfolio: w = (Sigma^-1 * 1) / (1^T * Sigma^-1 * 1)."""
    if not symbols:
        return {}
    inv_cov = _invert_matrix(covariance_matrix)
    ones = [1.0] * len(symbols)

    unnormalized_weights = _matrix_vector_multiply(inv_cov, ones)
    total_weight = sum(unnormalized_weights)

    if total_weight == 0:
        raise OptimizationError("Total weight sum is zero, cannot normalize.")

    return {s: w / total_weight for s, w in zip(symbols, unnormalized_weights, strict=True)}


def optimize_maximum_sharpe(
    symbols: Sequence[str],
    expected_returns: Sequence[float],
    covariance_matrix: Sequence[Sequence[float]],
) -> dict[str, float]:
    """Analytical Tangency Portfolio: w = (Sigma^-1 * mu) / (1^T * Sigma^-1 * mu)."""
    if not symbols:
        return {}
    inv_cov = _invert_matrix(covariance_matrix)

    unnormalized_weights = _matrix_vector_multiply(inv_cov, expected_returns)
    total_weight = sum(abs(w) for w in unnormalized_weights)  # Abs sum allows L/S normalization

    if total_weight == 0:
        raise OptimizationError("Total weight sum is zero, cannot normalize.")

    return {s: w / total_weight for s, w in zip(symbols, unnormalized_weights, strict=True)}
