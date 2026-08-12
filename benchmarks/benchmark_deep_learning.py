"""High-performance benchmark suite for the Deep Learning Engine."""

import time

from alphalab.deep_learning import (
    ActivationType,
    Sequential,
    create_conv1d_layer,
    create_dense_layer,
    create_lstm_cell,
    forward_conv1d,
    lstm_forward_sequence,
    scaled_dot_product_attention,
    train_network,
)


def run_benchmark() -> None:
    hidden = create_dense_layer(2, 4, ActivationType.TANH, seed=1)
    output = create_dense_layer(4, 1, ActivationType.SIGMOID, seed=2)
    network = Sequential(layers=(hidden, output))
    x = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    y = ((0.0,), (1.0,), (1.0,), (0.0,))

    N_EPOCHS = 1000
    print(f"Starting Deep Learning Engine Benchmark: {N_EPOCHS} training epochs...")

    start = time.perf_counter()
    train_network(network, x, y, learning_rate=0.5, epochs=N_EPOCHS)
    duration = time.perf_counter() - start
    print(f"  train_network (XOR): {duration:.4f}s, {N_EPOCHS / duration:.2f} epochs/sec")

    conv_layer = create_conv1d_layer(5, ActivationType.RELU, seed=1)
    sequence = tuple(float(i % 7) for i in range(100))
    N_CONV = 10_000
    start = time.perf_counter()
    for _ in range(N_CONV):
        forward_conv1d(conv_layer, sequence)
    duration = time.perf_counter() - start
    print(f"  forward_conv1d       : {duration:.4f}s, {N_CONV / duration:.2f} ops/sec")

    lstm_cell = create_lstm_cell(input_size=4, hidden_size=8, seed=1)
    lstm_sequence = tuple((float(i), float(i + 1), float(i + 2), float(i + 3)) for i in range(20))
    N_LSTM = 1_000
    start = time.perf_counter()
    for _ in range(N_LSTM):
        lstm_forward_sequence(lstm_cell, lstm_sequence)
    duration = time.perf_counter() - start
    print(f"  lstm_forward_sequence: {duration:.4f}s, {N_LSTM / duration:.2f} ops/sec")

    queries = tuple((float(i), float(i + 1)) for i in range(10))
    keys = tuple((float(i), float(i + 1)) for i in range(10))
    values = tuple((float(i),) for i in range(10))
    N_ATTENTION = 10_000
    start = time.perf_counter()
    for _ in range(N_ATTENTION):
        scaled_dot_product_attention(queries, keys, values)
    duration = time.perf_counter() - start
    print(f"  scaled_dot_product_attention: {duration:.4f}s, {N_ATTENTION / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
