import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from tiling import Tiler


def test_t1_divisible_weight_tiles():
    N = 2
    tiler = Tiler(N)
    W = np.arange(16, dtype=np.int8).reshape(4, 4)  # (K=4, N_w=4)
    tiles = list(tiler.weight_tiles(W))
    assert len(tiles) == 4  # 2 k_blocks × 2 j_blocks

    expected = [
        (W[0:2, 0:2], 0, 0),
        (W[0:2, 2:4], 0, 1),
        (W[2:4, 0:2], 1, 0),
        (W[2:4, 2:4], 1, 1),
    ]
    for (tile, kb, jb), (exp_tile, exp_kb, exp_jb) in zip(tiles, expected):
        assert kb == exp_kb and jb == exp_jb
        np.testing.assert_array_equal(tile, exp_tile)


def test_t2_nondivsible_padding():
    N = 2
    tiler = Tiler(N)
    W = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.int8)  # (K=3, N_w=3)
    tiles = list(tiler.weight_tiles(W))
    assert len(tiles) == 4  # ceil(3/2)=2 in each dim

    tile, kb, jb = tiles[1]  # k_block=0, j_block=1: W[0:2, 2:3] padded
    assert kb == 0 and jb == 1
    np.testing.assert_array_equal(tile, np.array([[3, 0], [6, 0]], dtype=np.int8))

    tile, kb, jb = tiles[2]  # k_block=1, j_block=0: W[2:3, 0:2] padded
    assert kb == 1 and jb == 0
    np.testing.assert_array_equal(tile, np.array([[7, 8], [0, 0]], dtype=np.int8))

    tile, kb, jb = tiles[3]  # k_block=1, j_block=1: corner, single cell padded
    assert kb == 1 and jb == 1
    np.testing.assert_array_equal(tile, np.array([[9, 0], [0, 0]], dtype=np.int8))

    # All padding cells are zero
    for tile, _, _ in tiles:
        assert tile.dtype == np.int8


def test_t2_act_chunk_padding():
    N = 2
    tiler = Tiler(N)
    A = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int8)  # (M=2, K=3)

    chunk0 = tiler.act_chunk(A, 0)
    np.testing.assert_array_equal(chunk0, A[:, 0:2])

    chunk1 = tiler.act_chunk(A, 1)  # K=2 only has 1 element left → pad
    np.testing.assert_array_equal(chunk1, np.array([[3, 0], [6, 0]], dtype=np.int8))


def test_t3_round_trip_divisible():
    rng = np.random.default_rng(42)
    N = 2
    A = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    W = rng.integers(-10, 10, (4, 4), dtype=np.int8)
    tiler = Tiler(N)
    C = tiler.alloc_C(4, 4)
    for W_tile, k_blk, j_blk in tiler.weight_tiles(W):
        C_tile = tiler.act_chunk(A, k_blk).astype(np.int32) @ W_tile.astype(np.int32)
        tiler.accumulate(C, C_tile, j_blk)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))


def test_t3_round_trip_nondivsible():
    rng = np.random.default_rng(7)
    N = 2
    M, K, N_w = 5, 3, 7
    A = rng.integers(-10, 10, (M, K), dtype=np.int8)
    W = rng.integers(-10, 10, (K, N_w), dtype=np.int8)
    tiler = Tiler(N)
    C = tiler.alloc_C(M, N_w)
    for W_tile, k_blk, j_blk in tiler.weight_tiles(W):
        C_tile = tiler.act_chunk(A, k_blk).astype(np.int32) @ W_tile.astype(np.int32)
        tiler.accumulate(C, C_tile, j_blk)
    np.testing.assert_array_equal(C, A.astype(np.int32) @ W.astype(np.int32))
