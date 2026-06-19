import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array


# ---------------- OS：sa.data == A@B ----------------

def _os_run(M, N, K, latency, seed):
    rng = np.random.default_rng(seed)
    A = rng.integers(-4, 5, size=(M, K))
    B = rng.integers(-4, 5, size=(K, N))
    sa = spatial_array(M, N, latency=latency,
                       is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0)
    # 反对角错位喂：a[m][k] 入行 m 左缘于拍 m+k；b[k][n] 入列 n 上缘于拍 n+k
    T = M + N + K + latency + 4
    for t in range(T):
        a_data = [int(A[m][t - m]) if 0 <= t - m < K else 0 for m in range(M)]
        b_data = [int(B[t - n][n]) if 0 <= t - n < K else 0 for n in range(N)]
        sa.update(a_data, [True] * M, b_data, [True] * N)
        sa.commit()
    return np.array(sa.data), A @ B


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K", [
    (2, 2, 2), (3, 3, 3), (8, 8, 8),
    (2, 4, 3), (4, 2, 3), (3, 5, 2), (5, 3, 7),
])
def test_os_matmul(M, N, K, latency):
    got, want = _os_run(M, N, K, latency, seed=M * 100 + N * 10 + K)
    np.testing.assert_array_equal(got, want)


# ---------------- WS：底行稳态 == a·B（权重驻留 active，psum 下流）----------------

@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,N", [(2, 2), (3, 3), (4, 2), (3, 5), (8, 8)])
def test_ws_compute_preload(K, N, latency):
    rng = np.random.default_rng(K * 10 + N)
    B = rng.integers(-4, 5, size=(K, N))
    a = rng.integers(-4, 5, size=K)
    sa = spatial_array(K, N, latency=latency,
                       is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
    for k in range(K):                       # 预载权重进 active
        for n in range(N):
            sa.pes[k][n].b = int(B[k][n])
    for _ in range(K + N + latency + 6):     # 常量喂激活，等稳态
        sa.update([int(x) for x in a], [True] * K, [0] * N, [False] * N)
        sa.commit()
    bottom = np.array(sa.data)[K - 1]
    np.testing.assert_array_equal(bottom, a @ B)


# ---------------- WS：影子倒序载入 + 翻转 → active == B ----------------

@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,N", [(2, 2), (3, 2), (3, 3), (4, 3)])
def test_ws_backpressure_load_switch_compute(K, N, latency):
    """WS 全流程：反压填 shadow → b_sw 抓进 active → stream 常量激活 → 底行稳态 == a·B。"""
    rng = np.random.default_rng(K * 10 + N + latency)
    B = rng.integers(-4, 5, size=(K, N))
    a = rng.integers(-4, 5, size=K)
    sa = spatial_array(K, N, latency=latency,
                       is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)

    # 1) 反压载入：倒序喂 B[K-1]..B[0]，K 拍填满（每拍沉到底、堆叠，满了反压停）
    for i in range(K):
        sa.update([0] * K, [False] * K, [int(x) for x in B[K - 1 - i]], [True] * N)
        sa.commit()
    for k in range(K):
        for n in range(N):
            assert sa.pes[k][n].b_buf == B[k][n] and sa.pes[k][n].b_buf_vld is True

    # 2) swap：注入一拍 b_sw，右传 N 拍翻完整阵列（反压保持着，等 swap 到）
    sa.update([0] * K, [False] * K, [0] * N, [False] * N, b_sw=[True] * K)
    sa.commit()
    for _ in range(N):
        sa.update([0] * K, [False] * K, [0] * N, [False] * N)
        sa.commit()
    for k in range(K):
        for n in range(N):
            assert sa.pes[k][n].b == B[k][n]

    # 3) stream 常量激活，等稳态，底行 == a·B（psum 下流）
    for _ in range(K + N + latency + 6):
        sa.update([int(x) for x in a], [True] * K, [0] * N, [False] * N)
        sa.commit()
    np.testing.assert_array_equal(np.array(sa.data)[K - 1], a @ B)
