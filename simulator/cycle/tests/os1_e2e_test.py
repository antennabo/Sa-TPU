import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonfifo import CommonFIFO
from simulator.cycle.sim_model.controller import Controller
from simulator.cycle.sim_model.accumulator import Accumulator


def _os1(M, N, K, L, seed):
    """OS-1 单 tile 端到端：ab+wb fifo 喂（标量 feed + 内部 skew，气泡 0）→ sa OS 原地累加
    → drain mux 出 sa.output[N] → accum 统一 update（controller output_sel 抽标量行 + 内部 SR 传播）。
    返回 (accum tile0, A@B)。
    cap_delay = 1：sa 的 drain mux 已把 per-column 对齐做掉，accum 只补 1 拍驱动循环偏移
    （对比 WS 的 (AR-1)+L+2——WS 的下流传播在数据通路里，不在 sa）。
    """
    rng = np.random.default_rng(seed)
    A = rng.integers(-3, 4, size=(M, K))
    B = rng.integers(-3, 4, size=(K, N))

    sa = spatial_array(M, N, latency=L,
                       is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0)
    ab = CommonFIFO(N=M, K=K)             # 激活：M lane(行)，lane m = A[m][:]
    wb = CommonFIFO(N=N, K=K)             # 权重：N lane(列)，lane n = B[:,n]
    ctrl = Controller(M, N, K, mode="OS", latency=L)
    accum = Accumulator(M, N, cap_delay=1)
    for d in range(K):
        ab.update([int(A[m][d]) for m in range(M)], False); ab.commit()   # push A[:,d]
        wb.update([int(B[d][n]) for n in range(N)], False); wb.commit()   # push B[d][:]

    for cy in range(M + N + 2 * K + L + 16):
        avail = cy < K                     # COMPUTE K 拍 → DRAIN
        ctrl.update(cy, weight_available=avail, activ_available=avail, new_tile=(cy == 0), tile_id=0)
        ab.update(None, ctrl.feed); wb.update(None, ctrl.feed)
        # OS：a_vld/b_vld 恒真（气泡是 0，不 hold 旧值污染原地累加）；output_sel 驱动 drain mux
        sa.update(list(ab.data), [True] * M, list(wb.data), [True] * N,
                  output_sel=list(ctrl.output_sel))
        sel = [r for r in range(M) if ctrl.output_sel[r]]      # output_sel ≤1 True → 标量行
        vld = len(sel) == 1
        accum.update(list(sa.output), vld, sel[0] if vld else 0, ctrl.wr_tile, False)
        ctrl.commit(); ab.commit(); wb.commit(); sa.commit(); accum.commit()

    return np.array(accum.get_tile(0)), A @ B


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("M,N,K", [
    (2, 2, 2), (3, 3, 3), (3, 2, 4), (2, 4, 3),
    (4, 3, 3), (4, 4, 5), (2, 2, 8), (5, 3, 2),
])
def test_os1_end_to_end(M, N, K, latency):
    got, want = _os1(M, N, K, latency, seed=M * 100 + N * 10 + K + latency)
    np.testing.assert_array_equal(got, want)
