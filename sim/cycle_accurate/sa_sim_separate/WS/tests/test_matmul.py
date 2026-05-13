import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from matmul import matmul


def ref(A, W):
    return A.astype(np.int32) @ W.astype(np.int32)


def test_t1_divisible_n2():
    rng = np.random.default_rng(1)
    A = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    W = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    np.testing.assert_array_equal(matmul(A, W, N=2), ref(A, W))


def test_t2_nondivsible_n2():
    rng = np.random.default_rng(2)
    A = rng.integers(-10, 10, (5, 3), dtype=np.int8)
    W = rng.integers(-10, 10, (3, 7), dtype=np.int8)
    np.testing.assert_array_equal(matmul(A, W, N=2), ref(A, W))


def test_t3_single_tile():
    rng = np.random.default_rng(3)
    A = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    W = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    np.testing.assert_array_equal(matmul(A, W, N=4), ref(A, W))


def test_t4_k_much_larger_than_n():
    rng = np.random.default_rng(4)
    A = rng.integers(-5, 5, (4, 16), dtype=np.int8)
    W = rng.integers(-5, 5, (16, 4), dtype=np.int8)
    np.testing.assert_array_equal(matmul(A, W, N=4), ref(A, W))


def test_t5_n8_large():
    rng = np.random.default_rng(5)
    A = rng.integers(-5, 5, (16, 24), dtype=np.int8)
    W = rng.integers(-5, 5, (24, 12), dtype=np.int8)
    np.testing.assert_array_equal(matmul(A, W, N=8), ref(A, W))
