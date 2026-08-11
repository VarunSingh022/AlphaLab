"""Minimal pure-Python linear algebra.

AlphaLab has zero runtime dependencies -- no numpy. This is deliberately just
enough linear algebra for `alphalab.ml.linear_regression`'s closed-form normal
equation solve: transpose, matrix multiply, and inverse via Gauss-Jordan
elimination with partial pivoting. Not a general-purpose linear algebra library --
correctness and clarity for small-to-moderate feature counts, not performance at
scale. Matrix inversion here is O(n^3) in the number of features, fine for tens of
features, not thousands.
"""

from alphalab.ml.exceptions import MLComputationError, MLInputError

Matrix = tuple[tuple[float, ...], ...]

_SINGULAR_THRESHOLD = 1e-12


def transpose(matrix: Matrix) -> Matrix:
    """Returns the transpose of a matrix."""
    if not matrix:
        return ()
    return tuple(zip(*matrix, strict=True))


def matmul(a: Matrix, b: Matrix) -> Matrix:
    """Multiplies two matrices.

    Raises:
        MLInputError: If a's column count does not match b's row count.
    """
    if not a or not b:
        raise MLInputError("matmul requires non-empty matrices.")

    rows_a, cols_a = len(a), len(a[0])
    rows_b, cols_b = len(b), len(b[0])
    if cols_a != rows_b:
        raise MLInputError(
            f"Cannot multiply a {rows_a}x{cols_a} matrix by a {rows_b}x{cols_b} matrix."
        )

    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(cols_a)) for j in range(cols_b))
        for i in range(rows_a)
    )


def identity(n: int) -> Matrix:
    """Returns the n x n identity matrix.

    Raises:
        MLInputError: If n is not positive.
    """
    if n <= 0:
        raise MLInputError(f"n must be positive, got {n}.")
    return tuple(tuple(1.0 if i == j else 0.0 for j in range(n)) for i in range(n))


def matrix_inverse(matrix: Matrix) -> Matrix:
    """Inverts a square matrix via Gauss-Jordan elimination with partial pivoting.

    Raises:
        MLInputError: If the matrix is not square, or is empty.
        MLComputationError: If the matrix is singular or numerically near-singular.
    """
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise MLInputError("matrix_inverse requires a non-empty square matrix.")

    augmented = [
        list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)
    ]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < _SINGULAR_THRESHOLD:
            raise MLComputationError(
                "Matrix is singular or numerically near-singular; cannot invert. "
                "For regression, this often means two features are collinear -- "
                "consider ridge regularization (l2_penalty > 0)."
            )
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]

        pivot_value = augmented[col][col]
        augmented[col] = [value / pivot_value for value in augmented[col]]

        for row in range(n):
            if row != col:
                factor = augmented[row][col]
                augmented[row] = [
                    augmented[row][k] - factor * augmented[col][k] for k in range(2 * n)
                ]

    return tuple(tuple(row[n:]) for row in augmented)
