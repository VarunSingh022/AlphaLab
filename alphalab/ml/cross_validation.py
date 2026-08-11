"""Cross-validation splitting: standard k-fold and time-respecting walk-forward.

Both are provided deliberately, not just k-fold: random (or even sequential-but-
non-time-aware) k-fold assigns samples to folds without regard to their order in
time. For time-ordered financial features, this lets a training fold contain
samples from *after* a test fold's samples -- future data leaking into training,
producing an artificially inflated validation score that will not hold up
out-of-sample. This is the same class of look-ahead bias this project's own
research package (walk-forward validation, bias auditing) was built to catch in
backtests; it applies equally to ML model validation. `walk_forward_split` is the
correct choice whenever samples have a genuine time order -- which, for
market/factor/macro/alt-data features, is effectively always.
"""

from alphalab.ml.exceptions import MLInputError

Split = tuple[tuple[int, ...], tuple[int, ...]]


def k_fold_split(n_samples: int, k: int) -> tuple[Split, ...]:
    """Splits sample indices into k sequential, contiguous folds (no shuffling).

    Each fold takes a turn as the test set; the remaining folds form the training
    set. Folds are contiguous index ranges, not randomly assigned -- this package
    never shuffles by default, so results are always deterministic given the same
    input order. This is appropriate for cross-sectional data with no meaningful
    order; for time-ordered data, use `walk_forward_split` instead.

    Raises:
        MLInputError: If k is less than 2, or k exceeds n_samples.
    """
    if k < 2:
        raise MLInputError(f"k must be at least 2, got {k}.")
    if k > n_samples:
        raise MLInputError(f"k ({k}) cannot exceed n_samples ({n_samples}).")

    fold_boundaries = [round(i * n_samples / k) for i in range(k + 1)]
    folds = [tuple(range(fold_boundaries[i], fold_boundaries[i + 1])) for i in range(k)]

    splits = []
    for i in range(k):
        test_indices = folds[i]
        train_indices = tuple(idx for j, fold in enumerate(folds) if j != i for idx in fold)
        splits.append((train_indices, test_indices))
    return tuple(splits)


def walk_forward_split(
    n_samples: int, n_splits: int, min_train_size: int, expanding: bool = True
) -> tuple[Split, ...]:
    """Splits time-ordered sample indices so every training fold only contains
    samples strictly before its corresponding test fold.

    With `expanding=True` (default), each successive training set grows to include
    all prior data (the standard "expanding window" walk-forward). With
    `expanding=False`, each training set is a fixed-size rolling window of the most
    recent `min_train_size` samples immediately preceding the test fold instead.

    Raises:
        MLInputError: If n_splits is not positive, min_train_size is not positive,
            or there is not enough data to produce n_splits non-empty test folds
            after reserving min_train_size samples for the first training set.
    """
    if n_splits <= 0:
        raise MLInputError(f"n_splits must be positive, got {n_splits}.")
    if min_train_size <= 0:
        raise MLInputError(f"min_train_size must be positive, got {min_train_size}.")

    remaining = n_samples - min_train_size
    if remaining < n_splits:
        raise MLInputError(
            f"Not enough samples: {n_samples} total, {min_train_size} reserved for the "
            f"first training set leaves only {remaining} for {n_splits} test folds."
        )

    test_fold_size = remaining // n_splits
    splits = []
    for i in range(n_splits):
        test_start = min_train_size + i * test_fold_size
        test_end = n_samples if i == n_splits - 1 else test_start + test_fold_size
        test_indices = tuple(range(test_start, test_end))

        train_start = 0 if expanding else max(0, test_start - min_train_size)
        train_indices = tuple(range(train_start, test_start))

        splits.append((train_indices, test_indices))
    return tuple(splits)
