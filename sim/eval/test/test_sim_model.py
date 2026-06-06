"""sim_model 周期级数值正确性测试（OS dataflow）。

验证 controller + commonfifo(wb/ab) + spatial_array/pe + accumulator 拼成的
端到端循环算出 accum == A @ B，覆盖：
  - 单 tensor（单 / 无缝多 K-chunk）
  - gap / IDLE 重启的 spill+restore（气泡 + 驱逐）
  - 顺序多输出块（per-cell tile_id + overwrite）

时序补偿参数：drain_delay = restore_delay = PE latency + 1（restore 默认随 drain，
两者同步以避免流水时新块 overwrite 早于旧块 drain）。
"""
import numpy as np
import pytest

from sim.eval.analyzer.sim_model.controller import Controller, ComputeState
from sim.eval.analyzer.sim_model.commonfifo import CommonFIFO
from sim.eval.analyzer.sim_model.spatial_array import spatial_array
from sim.eval.analyzer.sim_model.accumulator import Accumulator


def _mk(M, N, K, latency):
    ctrl = Controller(M, N, K, drain_delay=latency + 1)  # restore_delay 默认随 drain_delay
    wb, ab = CommonFIFO(N, K), CommonFIFO(M, K)
    sa = spatial_array(M, N, latency=latency)
    accum = Accumulator(num_rows=M, num_cols=N)
    for m in (ctrl, wb, ab, sa, accum):
        m.reset()
    return ctrl, wb, ab, sa, accum


def _push(wb, ab, A, B, k):
    """把第 k 个深度切片写入 buffer：wb lane c=B[k,c]，ab lane r=A[r,k]。"""
    M, N = len(A), len(B[0])
    wb.update([int(B[k][c]) for c in range(N)], [0] * N)
    ab.update([int(A[r][k]) for r in range(M)], [0] * M)
    wb.commit(); ab.commit()


def _step(ctrl, wb, ab, sa, accum, cy, ZEROS, restore_en=False, new_tile=False, ww=None, wa=None):
    ctrl.update(cy, wb.avail, ab.avail, restore_en=restore_en, new_tile=new_tile)
    wb.update(ww, ctrl.read_weight)
    ab.update(wa, ctrl.read_activation)
    accum.update(sa.data, ctrl.acc, ctrl.acc_read)
    sa.update(ab.data, wb.data, 1, 1, 0, 0, accum.restore_data if restore_en else ZEROS, ctrl.acc_read)
    ctrl.commit(); wb.commit(); ab.commit(); accum.commit(); sa.commit()


# --------------------------------------------------------------------------- #
SHAPES = [(2, 2, 2), (3, 3, 3), (4, 4, 4), (5, 5, 5), (3, 5, 2), (5, 3, 4)]


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K", SHAPES)
def test_single_tensor_seamless(M, N, K, latency):
    """单 tensor，Kt=2K 无缝累加 → accum == A@B。"""
    rng = np.random.default_rng(hash((M, N, K, latency)) & 0xffff)
    Kt = 2 * K
    A = rng.integers(-3, 4, (M, Kt)).astype(np.int8)
    B = rng.integers(-3, 4, (Kt, N)).astype(np.int8)
    C = A.astype(np.int32) @ B.astype(np.int32)

    ctrl, wb, ab, sa, accum = _mk(M, N, K, latency)
    ZEROS = [[0] * N for _ in range(M)]
    for kk in range(Kt):
        _push(wb, ab, A, B, kk)
    for cy in range(Kt + M + N + latency + 10):
        _step(ctrl, wb, ab, sa, accum, cy, ZEROS)
    assert np.array_equal(np.array(accum.get_tile(0)), C)


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("evict", [False, True])
def test_gap_restart_restore(latency, evict):
    """单 tensor 2 个 K-chunk，长 gap → IDLE 重启 + restore（气泡 / 驱逐都应对）。"""
    M = N = K = 3
    rng = np.random.default_rng(hash((latency, evict)) & 0xffff)
    A = rng.integers(-3, 4, (M, 2 * K)).astype(np.int8)
    B = rng.integers(-3, 4, (2 * K, N)).astype(np.int8)
    C = A.astype(np.int32) @ B.astype(np.int32)

    ctrl, wb, ab, sa, accum = _mk(M, N, K, latency)
    ZEROS = [[0] * N for _ in range(M)]
    for k in range(K):  # 预载 chunk0
        _push(wb, ab, A, B, k)

    chunk0_started = False
    nonfeed = 0
    c1 = 0
    evicted = False
    gap = 15
    for cy in range(2 * K + M + N + 40):
        re = chunk0_started
        ww = wa = None
        st = ctrl.compute_state
        if chunk0_started and st not in ctrl._FEED_STATES:
            nonfeed += 1
            if nonfeed > gap and c1 < K:
                if evict and not evicted:           # spill 早写完后清 PE，模拟阵列被占用
                    for r in range(M):
                        for cc in range(N):
                            sa.pes[r][cc].state = sa.dtype_state(0)
                            sa.pes[r][cc].state_next = sa.dtype_state(0)
                    evicted = True
                kk = K + c1
                ww = [int(B[kk][cc]) for cc in range(N)]
                wa = [int(A[r][kk]) for r in range(M)]
                c1 += 1
        _step(ctrl, wb, ab, sa, accum, cy, ZEROS, restore_en=re, ww=ww, wa=wa)
        if ctrl.compute_state == ComputeState.COMPUTE:
            chunk0_started = True
    assert np.array_equal(np.array(accum.get_tile(0)), C)


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("nblocks", [2, 3])
def test_sequential_multiblock(latency, nblocks):
    """顺序多输出块：每块独立 A@B → 写进各自 accum tile_id。"""
    M = N = K = 3
    rng = np.random.default_rng(hash((latency, nblocks)) & 0xffff)
    blocks = []
    for _ in range(nblocks):
        A = rng.integers(-3, 4, (M, K)).astype(np.int8)
        B = rng.integers(-3, 4, (K, N)).astype(np.int8)
        blocks.append((A, B, A.astype(np.int32) @ B.astype(np.int32)))

    ctrl, wb, ab, sa, accum = _mk(M, N, K, latency)
    ZEROS = [[0] * N for _ in range(M)]
    cy = 0
    for bi, (A, B, _) in enumerate(blocks):
        for k in range(K):
            _push(wb, ab, A, B, k)
        for ic in range(K + M + N + latency + 8):
            _step(ctrl, wb, ab, sa, accum, cy, ZEROS, new_tile=(bi > 0 and ic == 0))
            cy += 1
    for b in range(nblocks):
        assert np.array_equal(np.array(accum.get_tile(b)), blocks[b][2])


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K,nblocks", [(2, 2, 2, 2), (2, 2, 2, 3), (3, 3, 3, 2),
                                           (4, 4, 4, 3), (3, 5, 2, 2), (5, 3, 4, 2)])
def test_pipelined_multiblock(M, N, K, nblocks, latency):
    """流水多输出块：所有块背靠背喂，块 n+1 喂时块 n 仍在 drain（drain 波前重叠）。
    drain 与 overwrite 同步到 latency+1 拍，靠 per-cell tile_id 把重叠结果写对块。"""
    rng = np.random.default_rng(hash((M, N, K, nblocks, latency)) & 0xffff)
    blocks = []
    for _ in range(nblocks):
        A = rng.integers(-3, 4, (M, K)).astype(np.int8)
        B = rng.integers(-3, 4, (K, N)).astype(np.int8)
        blocks.append((A, B, A.astype(np.int32) @ B.astype(np.int32)))

    ctrl, wb, ab, sa, accum = _mk(M, N, K, latency)
    ZEROS = [[0] * N for _ in range(M)]
    for (A, B, _) in blocks:           # 预载全部块 → avail 跨块保持，背靠背流水
        for k in range(K):
            _push(wb, ab, A, B, k)
    for cy in range(nblocks * K + M + N + latency + 10):
        _step(ctrl, wb, ab, sa, accum, cy, ZEROS, new_tile=(cy != 0))
    for b in range(nblocks):
        assert np.array_equal(np.array(accum.get_tile(b)), blocks[b][2])
