import numpy as np
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from systolic_array import SystolicArray


def _preload(sa, W):
    """Drive weight_in_top in reversed-row order for N ticks."""
    N = sa.N    
    z8  = np.zeros(N, dtype=np.int8)
    for step in range(N):
        row_idx = N - 1 - step
        sa.tick(weight_in_top=W[row_idx].astype(np.int8),
                load_enable=True,                
                act_in_left=z8)


def _compute(sa, A_chunk):
    """Run compute phase; return C_tile[M, N] matching A_chunk @ W."""
    M, N = A_chunk.shape
    assert N == sa.N
    # z32 = np.zeros(N, dtype=np.int32)
    z8  = np.zeros(N, dtype=np.int8)
    compute_ticks = M + 2 * N - 2
    C = np.zeros((M, N), dtype=np.int32)

    for t in range(compute_ticks):
        act_left = np.zeros(N, dtype=np.int8)
        for i in range(N):
            m = t - i
            if 0 <= m < M:
                act_left[i] = A_chunk[m, i]

        out = sa.tick(weight_in_top=z8, load_enable=False,
                      act_in_left=act_left)

        for j in range(N):
            m = t - (N - 1) - j
            if 0 <= m < M:
                C[m, j] = out['psum_out_bottom'][j]

    return C


# T1: manual preload, snapshot weights match W
def test_t1_preload_n2():
    W = np.array([[1, 2], [3, 4]], dtype=np.int8)
    sa = SystolicArray(2)
    # Drive exactly as spec: cycle 0 → W[1], cycle 1 → W[0]
    # z = np.zeros(2, dtype=np.int32)
    z8 = np.zeros(2, dtype=np.int8)
    sa.tick(weight_in_top=W[1], load_enable=True, act_in_left=z8)
    sa.tick(weight_in_top=W[0], load_enable=True, act_in_left=z8)
    snap = sa.snapshot()
    np.testing.assert_array_equal(snap['weight'], W)


# T2: N=2 known values, verify output cycle-by-cycle
def test_t2_compute_n2_known():
    W = np.array([[1, 2], [3, 4]], dtype=np.int8)
    A = np.array([[5, 6], [7, 8]], dtype=np.int8)
    sa = SystolicArray(2)
    _preload(sa, W)
    C = _compute(sa, A)
    expected = A.astype(np.int32) @ W.astype(np.int32)  # [[23,34],[31,46]]
    np.testing.assert_array_equal(C, expected)


# T3: N=4 random int8 — matches numpy
def test_t3_compute_n4_random():
    rng = np.random.default_rng(42)
    N, M = 4, 4
    W = rng.integers(-10, 10, (N, N), dtype=np.int8)
    A = rng.integers(-10, 10, (M, N), dtype=np.int8)
    sa = SystolicArray(N)
    _preload(sa, W)
    C = _compute(sa, A)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))


# T4: all-zero weights → psum always 0
def test_t4_zero_weights():
    N, M = 4, 4
    rng = np.random.default_rng(0)
    A = rng.integers(-128, 127, (M, N), dtype=np.int8)
    sa = SystolicArray(N)   # weights never loaded, all zero
    z8  = np.zeros(N, dtype=np.int8)
    for t in range(M + 2 * N - 2):
        act_left = np.zeros(N, dtype=np.int8)
        for i in range(N):
            m = t - i
            if 0 <= m < M:
                act_left[i] = A[m, i]
        out = sa.tick(z8, False, act_left)
        assert np.all(out['psum_out_bottom'] == 0)


# T5: trace=True returns correct shapes and dtypes
def test_t5_trace_shape():
    N = 3
    sa = SystolicArray(N)
    out = sa.tick(weight_in_top=np.zeros(N, dtype=np.int8),
                  load_enable=False,                
                  act_in_left=np.zeros(N, dtype=np.int8),
                  trace=True)
    assert 'trace' in out
    tr = out['trace']
    for key, dtype in [('weight', np.int8), ('act_out', np.int8), ('psum_out', np.int32)]:
        assert tr[key].shape == (N, N), f"{key} shape wrong"
        assert tr[key].dtype == dtype, f"{key} dtype wrong"
