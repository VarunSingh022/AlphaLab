"""High-performance benchmark suite for the Machine Learning Engine."""

import time

from alphalab.ml import (
    k_fold_split,
    matrix_inverse,
    mean_squared_error,
    predict_linear,
    train_linear_regression,
    train_logistic_regression,
    walk_forward_split,
)


def run_benchmark() -> None:
    x = tuple((float(i), float(i % 7)) for i in range(200))
    y = tuple(2.0 * row[0] + 0.5 * row[1] + 1.0 for row in x)
    binary_y = tuple(1 if row[0] > 100 else 0 for row in x)

    N = 1_000
    print(f"Starting Machine Learning Engine Benchmark: {N} iterations per operation...")

    start = time.perf_counter()
    for _ in range(N):
        model = train_linear_regression(("x1", "x2"), x, y)
    duration = time.perf_counter() - start
    print(f"  train_linear_regression: {duration:.4f}s, {N / duration:.2f} ops/sec")

    predictions = predict_linear(model, x)
    start = time.perf_counter()
    for _ in range(N):
        mean_squared_error(y, predictions)
    duration = time.perf_counter() - start
    print(f"  mean_squared_error     : {duration:.4f}s, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        matrix_inverse(((4.0, 7.0, 2.0), (2.0, 6.0, 1.0), (1.0, 3.0, 5.0)))
    duration = time.perf_counter() - start
    print(f"  matrix_inverse (3x3)   : {duration:.4f}s, {N / duration:.2f} ops/sec")

    N_LOGISTIC = 20
    start = time.perf_counter()
    for _ in range(N_LOGISTIC):
        train_logistic_regression(("x1", "x2"), x, binary_y, iterations=500)
    duration = time.perf_counter() - start
    print(
        f"  train_logistic_regression (200 samples, 500 iters): {duration:.4f}s, "
        f"{N_LOGISTIC / duration:.2f} ops/sec"
    )

    start = time.perf_counter()
    for _ in range(N):
        k_fold_split(200, 5)
    duration = time.perf_counter() - start
    print(f"  k_fold_split           : {duration:.4f}s, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        walk_forward_split(200, n_splits=5, min_train_size=50)
    duration = time.perf_counter() - start
    print(f"  walk_forward_split     : {duration:.4f}s, {N / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
