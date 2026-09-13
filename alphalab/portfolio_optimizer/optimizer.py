"""Pure Python, deterministic analytical portfolio optimization routines.

**Every input is checked against the symbol list before any arithmetic runs.**
Until v2.16 these routines trusted their arguments, and three ways of passing
inconsistent ones produced a portfolio rather than a refusal:

* ``optimize_maximum_sharpe(("A", "B", "C"), (0.1, 0.2), cov_3x3)`` returned
  weights for three assets. ``_matrix_vector_multiply`` iterates the *vector*,
  so the third expected return and the third covariance column were silently
  dropped and C was weighted at zero -- a real allocation decision, made by a
  length mismatch nobody was told about.
* ``optimize_inverse_volatility`` read ``volatilities.get(symbol, 1.0)``, so an
  asset missing from the mapping was sized as though its volatility were 1.0.
  Against a book of 10%-vol assets that is a tenth of the weight it should
  carry, and nothing distinguished it from a deliberate input.
* A covariance matrix of the wrong shape, or a ragged one, raised ``IndexError``
  out of the inversion -- not this package's
  :class:`~alphalab.portfolio_optimizer.exceptions.OptimizationError`, so a
  caller handling the documented error type did not catch it.

A wrong portfolio that looks like a right one is the worst failure this module
can have, so every one of those is now a refusal that names the mismatch.

The inversion itself is unchanged. It is Gauss-Jordan elimination without
partial pivoting, which is the standard, backward-stable choice for the
symmetric positive-definite matrices a covariance matrix is; a singular one is
refused rather than inverted.
"""

from collections.abc import Mapping, Sequence

from alphalab.portfolio_optimizer.exceptions import OptimizationError


def _validate_covariance(
    symbols: Sequence[str], covariance_matrix: Sequence[Sequence[float]]
) -> None:
    """Refuse a covariance matrix that is not square and sized to ``symbols``."""

    if len(covariance_matrix) != len(symbols):
        raise OptimizationError(
            f"Covariance matrix has {len(covariance_matrix)} rows for "
            f"{len(symbols)} symbols; they must match."
        )
    for index, row in enumerate(covariance_matrix):
        if len(row) != len(symbols):
            raise OptimizationError(
                f"Covariance matrix row {index} has {len(row)} entries for "
                f"{len(symbols)} symbols; the matrix must be square."
            )


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
    """Weights assets inversely proportional to their individual volatilities.

    Raises:
        OptimizationError: If any symbol has no volatility, or one that is not
            positive. A missing volatility is not a default -- it is a symbol
            this function cannot weight.
    """
    if not symbols:
        return {}
    missing = [s for s in symbols if s not in volatilities]
    if missing:
        raise OptimizationError(f"No volatility supplied for {', '.join(missing)}.")
    inv_vols = []
    for s in symbols:
        vol = volatilities[s]
        if vol <= 0:
            raise OptimizationError(f"Volatility for {s} must be > 0.")
        inv_vols.append(1.0 / vol)

    total_inv_vol = sum(inv_vols)
    return {s: inv / total_inv_vol for s, inv in zip(symbols, inv_vols, strict=True)}


def optimize_minimum_variance(
    symbols: Sequence[str], covariance_matrix: Sequence[Sequence[float]]
) -> dict[str, float]:
    """Analytical Minimum Variance Portfolio: w = (Sigma^-1 * 1) / (1^T * Sigma^-1 * 1).

    Raises:
        OptimizationError: If the covariance matrix is not square and sized to
            ``symbols``, or is singular.
    """
    if not symbols:
        return {}
    _validate_covariance(symbols, covariance_matrix)
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
    """Analytical Tangency Portfolio: w = (Sigma^-1 * mu) / (1^T * Sigma^-1 * mu).

    Raises:
        OptimizationError: If ``expected_returns`` or the covariance matrix is
            not sized to ``symbols``, or the matrix is singular.
    """
    if not symbols:
        return {}
    if len(expected_returns) != len(symbols):
        raise OptimizationError(
            f"{len(expected_returns)} expected returns for {len(symbols)} symbols; they must match."
        )
    _validate_covariance(symbols, covariance_matrix)
    inv_cov = _invert_matrix(covariance_matrix)

    unnormalized_weights = _matrix_vector_multiply(inv_cov, expected_returns)
    total_weight = sum(abs(w) for w in unnormalized_weights)  # Abs sum allows L/S normalization

    if total_weight == 0:
        raise OptimizationError("Total weight sum is zero, cannot normalize.")

    return {s: w / total_weight for s, w in zip(symbols, unnormalized_weights, strict=True)}
