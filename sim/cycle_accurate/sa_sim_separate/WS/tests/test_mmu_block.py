import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from systolic_array import SystolicArray
from weight_fifo import WeightFIFO
from data_setup import DataSetup
from accumulators import Accumulators


def run_tile(A, W, N):
    sa = SystolicArray(N)
    wf = WeightFIFO(N)
    ds = DataSetup(N)
    ac = Accumulators(N)
    M = A.shape[0]
    # z32 = np.zeros(N, dtype=np.int32)
    z8  = np.zeros(N, dtype=np.int8)

    wf.push_tile(W)
    ds.load_chunk(A)
    ac.start_collect(M)

    for _ in range(N):
        w_top, le = wf.step()
        sa.tick(weight_in_top=w_top, load_enable=le, act_in_left=z8)

    for _ in range(ac.cycles_needed(M)):
        w_top, le = wf.step()
        act_left = ds.step()
        out = sa.tick(weight_in_top=w_top, load_enable=le,
                      act_in_left=act_left)
        ac.step(out['psum_out_bottom'])

    return ac.finish()


def test_t1_n2_known():
    A = np.array([[5, 6], [7, 8]], dtype=np.int8)
    W = np.array([[1, 2], [3, 4]], dtype=np.int8)
    C = run_tile(A, W, N=2)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))


def test_t2_n4_random():
    rng = np.random.default_rng(42)
    N, M = 4, 4
    A = rng.integers(-10, 10, (M, N), dtype=np.int8)
    W = rng.integers(-10, 10, (N, N), dtype=np.int8)
    C = run_tile(A, W, N)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))


def test_t3_m1():
    rng = np.random.default_rng(7)
    N = 4
    A = rng.integers(-10, 10, (1, N), dtype=np.int8)
    W = rng.integers(-10, 10, (N, N), dtype=np.int8)
    C = run_tile(A, W, N)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))


def test_t4_m_large():
    rng = np.random.default_rng(99)
    N, M = 4, 10
    A = rng.integers(-10, 10, (M, N), dtype=np.int8)
    W = rng.integers(-10, 10, (N, N), dtype=np.int8)
    C = run_tile(A, W, N)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))
