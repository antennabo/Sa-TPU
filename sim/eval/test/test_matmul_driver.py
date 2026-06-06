"""MatMul 驱动 / 序列器测试：run_matmul 切块算出 == A@B，覆盖多输出块 × 多 K-chunk。"""
import numpy as np
import pytest

from sim.eval.analyzer.sim_model.matmul_driver import run_matmul


# (M, N, K, Gm, Gn, Gk)：含纯 K-chunk、纯行/列分块、行×列×K 全分块、矩形阵列
CASES = [
    (2, 2, 2,  2, 2, 2),   # 单块单 chunk
    (2, 2, 2,  2, 2, 4),   # 仅 K-chunk（cpb=2）
    (2, 2, 2,  4, 2, 2),   # 仅行分块
    (2, 2, 2,  2, 4, 2),   # 仅列分块
    (2, 2, 2,  4, 4, 4),   # 行×列×K 全分块（nblocks=4, cpb=2）
    (3, 3, 3,  6, 6, 6),   # 全分块（nblocks=4, cpb=2）
    (3, 5, 2,  6, 10, 4),  # 矩形阵列 + 全分块
    (5, 3, 4,  5, 3, 8),   # 矩形阵列 + 仅 K-chunk（cpb=2）
]


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K,Gm,Gn,Gk", CASES)
def test_run_matmul(M, N, K, Gm, Gn, Gk, latency):
    rng = np.random.default_rng(hash((M, N, K, Gm, Gn, Gk, latency)) & 0xffff)
    A = rng.integers(-3, 4, (Gm, Gk)).astype(np.int8)
    B = rng.integers(-3, 4, (Gk, Gn)).astype(np.int8)
    C_ref = A.astype(np.int32) @ B.astype(np.int32)

    C, n_cycles = run_matmul(A, B, M, N, K, latency=latency)
    assert np.array_equal(C, C_ref)
    assert n_cycles > 0


def test_non_divisible_raises():
    A = np.zeros((3, 4), dtype=np.int8)
    B = np.zeros((4, 3), dtype=np.int8)
    with pytest.raises(AssertionError):
        run_matmul(A, B, 2, 2, 2)
