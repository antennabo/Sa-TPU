import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from data_setup import DataSetup


def test_skewed_feed_m3_n2():
    # A_chunk rows are batch items, columns are K-elements
    A = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.int8)  # M=3, N=2
    ds = DataSetup(2)
    ds.load_chunk(A)

    expected = [
        [A[0, 0], 0],       # T=0: row0=A[0,0], row1=bubble
        [A[1, 0], A[0, 1]], # T=1
        [A[2, 0], A[1, 1]], # T=2
        [0,       A[2, 1]], # T=3
    ]
    for t, exp in enumerate(expected):
        out = ds.step()
        np.testing.assert_array_equal(out, np.array(exp, dtype=np.int8), err_msg=f"T={t}")


def test_cycles_needed():
    ds = DataSetup(4)
    assert ds.cycles_needed(1) == 4
    assert ds.cycles_needed(4) == 7
    assert ds.cycles_needed(10) == 13


def test_reset():
    ds = DataSetup(2)
    ds.load_chunk(np.ones((2, 2), dtype=np.int8))
    ds.step()
    ds.reset()
    ds.load_chunk(np.array([[7, 8], [9, 10]], dtype=np.int8))
    out = ds.step()
    assert out[0] == 7
