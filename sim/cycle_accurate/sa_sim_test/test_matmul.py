import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import numpy as np
import pytest
from sim.cycle_accurate.sa_sim.matmul_api import matmul
from sim.cycle_accurate.sa_sim_test.reference import ref_matmul

# (M, K, P) — covers divisible or not divisble by N=8
SHAPES = [
    (8, 8, 8),      
    (16, 16, 16),   
    (10, 10, 10),   
    (7, 13, 5),     
    (1, 8, 1),      
    (32, 7, 15),    
    (64, 64, 64),   
]

SEEDS = [0, 1, 42]


def random_int8(shape, rng):
    return rng.integers(-128, 128, size=shape, dtype=np.int8)


def diff_report(W, X, got, expected):
    mask = got != expected
    n_diff = mask.sum()
    lines = [
        f"  shape W={W.shape} X={X.shape} out={got.shape}",
        f"  mismatched elements: {n_diff}/{got.size}",
        f"  max abs error: {np.abs(got.astype(np.int64) - expected.astype(np.int64)).max()}",
    ]
    if n_diff <= 5:
        idxs = list(zip(*np.where(mask)))
        for idx in idxs:
            lines.append(f"  [{idx}] got={got[idx]}  expected={expected[idx]}")
    return "\n".join(lines)


@pytest.mark.parametrize("M,K,P", SHAPES)
@pytest.mark.parametrize("seed", SEEDS)
def test_bit_exact(M, K, P, seed):
    rng = np.random.default_rng(seed)
    W = random_int8((M, K), rng)
    X = random_int8((K, P), rng)

    got = matmul(W, X, N=8)
    expected = ref_matmul(W, X)

    assert got.dtype == np.int32, f"output dtype should be int32, got {got.dtype}"
    assert got.shape == expected.shape, f"shape mismatch: {got.shape} vs {expected.shape}"
    assert np.array_equal(got, expected), \
        f"bit-exact check FAILED:\n{diff_report(W, X, got, expected)}"
