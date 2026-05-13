import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from weight_fifo import WeightFIFO


def test_reversed_row_order_two_tiles():
    N = 2
    W1 = np.array([[1, 2], [3, 4]], dtype=np.int8)
    W2 = np.array([[5, 6], [7, 8]], dtype=np.int8)
    wf = WeightFIFO(N)
    wf.push_tile(W1)
    wf.push_tile(W2)

    # Tile 1: emits W1[1] then W1[0]
    row, le = wf.step()
    assert le is True
    np.testing.assert_array_equal(row, W1[1])  # [3,4]

    row, le = wf.step()
    assert le is True
    np.testing.assert_array_equal(row, W1[0])  # [1,2]

    # Tile 2: emits W2[1] then W2[0]
    row, le = wf.step()
    assert le is True
    np.testing.assert_array_equal(row, W2[1])  # [7,8]

    row, le = wf.step()
    assert le is True
    np.testing.assert_array_equal(row, W2[0])  # [5,6]

    # Idle after both tiles done
    row, le = wf.step()
    assert le is False
    np.testing.assert_array_equal(row, np.zeros(N, dtype=np.int8))


def test_load_enable_false_between_tiles():
    N = 2
    wf = WeightFIFO(N)
    wf.push_tile(np.zeros((N, N), dtype=np.int8))

    _, le = wf.step()
    assert le is True
    _, le = wf.step()
    assert le is True
    # Now idle
    _, le = wf.step()
    assert le is False


def test_depth_limit():
    wf = WeightFIFO(2, depth=1)
    ok = wf.push_tile(np.zeros((2, 2), dtype=np.int8))
    assert ok is True
    ok = wf.push_tile(np.zeros((2, 2), dtype=np.int8))
    assert ok is False


def test_is_idle():
    wf = WeightFIFO(2)
    assert wf.is_idle()
    wf.push_tile(np.zeros((2, 2), dtype=np.int8))
    assert not wf.is_idle()
    wf.step(); wf.step()
    assert wf.is_idle()


def test_reset():
    wf = WeightFIFO(2)
    wf.push_tile(np.ones((2, 2), dtype=np.int8))
    wf.reset()
    assert wf.is_idle()
    _, le = wf.step()
    assert le is False
