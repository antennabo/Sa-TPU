import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonfifo import CommonFIFO
from simulator.cycle.sim_model.controller import Controller
from simulator.cycle.sim_model.accumulator import Accumulator


def _run_block(A_blk, B_blk, M, N, K, L, slot, accum):
    """单个输出块（M×N）：复用已验证的单 tile 路径（os1），块内 cpb 个 K-chunk 无缝累加在 PE psum，
    drain mux 出结果写 accum 的 slot 槽。每块用全新阵列 = acc_clr 把上一块 psum 彻底清空（步 1a：
    相邻输出 tile 不重叠，独立算完→drain 干净→写回→下一个）。A_blk[M,Gk]、B_blk[Gk,N]，cpb=Gk//K。"""
    Gk = A_blk.shape[1]
    sa = spatial_array(M, N, latency=L,
                       is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0)
    ab = CommonFIFO(N=M, K=K)                  # 激活：M lane(行)
    wb = CommonFIFO(N=N, K=K)                  # 权重：N lane(列)
    ctrl = Controller(M, N, K, mode="OS", latency=L)
    for kd in range(Gk):                       # 预载本块全部 K-chunk（feed 序，各 K 深）
        ab.update([int(A_blk[r, kd]) for r in range(M)], False); ab.commit()
        wb.update([int(B_blk[kd, c]) for c in range(N)], False); wb.commit()

    for cy in range(Gk + M + N + 2 * K + L + 12):
        avail = cy < Gk                        # 喂 cpb*K 拍 → DRAIN
        ctrl.update(cy, weight_available=avail, activ_available=avail,
                    new_tile=(cy == 0), tile_id=slot)
        ab.update(None, ctrl.feed); wb.update(None, ctrl.feed)
        sa.update(list(ab.data), [True] * M, list(wb.data), [True] * N,
                  output_sel=list(ctrl.output_sel))
        sel = [r for r in range(M) if ctrl.output_sel[r]]   # output_sel ≤1 True → 标量行
        vld = len(sel) == 1
        accum.update(list(sa.output), vld, sel[0] if vld else 0, slot, False)
        ctrl.commit(); ab.commit(); wb.commit(); sa.commit(); accum.commit()


def _run_os(A, B, M, N, K, L):
    """OS 多 tile 端到端（新标量接口，步 1a 非重叠）：把 A[Gm,Gk]@B[Gk,Gn] 按 M×N 阵列切块，
    每个输出块 (mi,nj)→tile_id=mi*cols+nj 独立跑（_run_block），共享 accumulator 写各自槽。返回 C[Gm,Gn]。
    （步 1b 的相邻 tile 无缝流水 / 在阵列内 acc_clr 复用，需对新数据通路重新标定 drain/clr 波前——另算。）"""
    A, B = np.asarray(A), np.asarray(B)
    Gm, Gk = A.shape
    Gk2, Gn = B.shape
    assert Gk == Gk2 and Gm % M == 0 and Gn % N == 0 and Gk % K == 0
    rows, cols = Gm // M, Gn // N
    assert rows * cols <= Accumulator.NUM_TILES
    accum = Accumulator(M, N, cap_delay=1)
    for mi in range(rows):
        for nj in range(cols):
            slot = mi * cols + nj
            A_blk = A[mi * M:(mi + 1) * M, :]
            B_blk = B[:, nj * N:(nj + 1) * N]
            _run_block(A_blk, B_blk, M, N, K, L, slot, accum)
    C = np.zeros((Gm, Gn), dtype=np.int64)
    for mi in range(rows):
        for nj in range(cols):
            C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N] = np.array(accum.get_tile(mi * cols + nj))
    return C


# (M,N,K, Gm,Gn,Gk)：纯 K-chunk / 纯行 / 纯列 / 行×列×K 全分块 / 矩形阵列
CASES = [
    (2, 2, 2, 2, 2, 4),    # 仅 K-chunk（cpb=2, nblocks=1）
    (2, 2, 2, 4, 2, 2),    # 仅行分块（nblocks=2）
    (2, 2, 2, 2, 4, 2),    # 仅列分块（nblocks=2）
    (2, 2, 2, 4, 4, 4),    # 行×列×K 全分块（nblocks=4, cpb=2）
    (3, 3, 3, 6, 6, 6),    # 全分块（nblocks=4, cpb=2）
    (3, 2, 2, 6, 4, 4),    # 矩形阵列 + 全分块（nblocks=6, cpb=2）
    (4, 3, 3, 4, 6, 3),    # 仅列分块（矩形阵列, nblocks=2）
    (5, 3, 4, 5, 3, 8),    # 矩形阵列 + 仅 K-chunk（cpb=2, nblocks=1）
]


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K,Gm,Gn,Gk", CASES)
def test_os_multitile_end_to_end(M, N, K, Gm, Gn, Gk, latency):
    rng = np.random.default_rng((M, N, K, Gm, Gn, Gk, latency).__hash__() & 0xffff)
    A = rng.integers(-3, 4, (Gm, Gk))
    B = rng.integers(-3, 4, (Gk, Gn))
    got = _run_os(A, B, M, N, K, latency)
    np.testing.assert_array_equal(got, A @ B)
