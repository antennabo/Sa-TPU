import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonbuf import CommonBuf
from simulator.cycle.sim_model.controller import Controller, WSState
from simulator.cycle.sim_model.accumulator import Accumulator


def _ws1(AR, AC, F, L, seed, dump_path=None):
    """WS-1 单 tile 端到端【真实闭环】：controller 驱动 + ab fifo 喂激活 + sa 算 + accum 抓底行。
    weight_available 由 sa.shadow_full 真实回馈（WLOAD 期间倒序喂权重、反压填 shadow、满则进 STREAM）。
    返回 (accum tile0, A@B)。AR=K, AC=N, F=M。
    cap_delay = (AR-1)+L+2：psum 下流 AR 行 + PE 流水 L + 寄存 + 驱动循环一拍（accum 读上拍底行）。
    """
    rng = np.random.default_rng(seed)
    B = rng.integers(-3, 4, size=(AR, AC))        # 权重 K×N
    A = rng.integers(-3, 4, size=(F, AR))         # 激活 M×K
    cap_delay = (AR - 1) + L + 2

    sa = spatial_array(AR, AC, latency=L,
                       is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
    ab = CommonBuf(N=AR, K=F)               # WS 激活用 buf（可寻址、可重读复用，见 common_buf_design.md）
    ctrl = Controller(AR, AC, F, mode="WS", latency=L)
    accum = Accumulator(num_rows=F, num_cols=AC, cap_delay=cap_delay)

    # ab 预载 A（lane k = A[:,k]）；shadow 不预载——靠 WLOAD 真实载入
    for d in range(F):
        ab.update([int(A[d][k]) for k in range(AR)], False); ab.commit()

    load_idx = streamed = 0
    rows = []
    for cy in range(2 * AR + F + AC + L + 14):
        wa = all(sa.shadow_full)                   # 真实 weight_available（每列反压全拉起）
        aa = streamed < (F + 1)                    # 单 tile：喂完即停 → DRAIN
        ctrl.update(cy, weight_available=wa, activ_available=aa)
        # WLOAD 期间倒序喂权重行 B[AR-1]..B[0]，反压填 shadow
        if ctrl.ws_state == WSState.WLOAD and load_idx < AR:
            b_data = [int(B[AR - 1 - load_idx][n]) for n in range(AC)]; b_vld = [True] * AC
            do_load = True
        else:
            b_data = [0] * AC; b_vld = [False] * AC; do_load = False
        ab.update(None, ctrl.feed)                 # 标量 feed → fifo 内部 skew → a_data/a_vld
        a_in   = [int(x) for x in ab.data]         # 录：该拍 sa 边界激励
        av_in  = [int(bool(x)) for x in ab.vld]
        bsw_in = [int(bool(x)) for x in ctrl.b_sw]
        sa.update(list(ab.data), list(ab.vld), b_data, b_vld, b_sw=list(ctrl.b_sw))
        cap_vld = ctrl.m is not None               # capture：m=None 时不写
        accum.update(list(sa.data[AR - 1]),
                     cap_vld,
                     ctrl.m if cap_vld else 0,
                     ctrl.wr_tile if cap_vld else 0,
                     False)
        ctrl.commit(); ab.commit(); sa.commit(); accum.commit()
        if dump_path is not None:                   # commit 后底行 = RTL out 该拍应有值
            exp = [int(x) for x in sa.data[AR - 1]]
            rows.append(a_in + av_in + [int(v) for v in b_data]
                        + [int(bool(v)) for v in b_vld] + bsw_in + exp)
        if do_load:
            load_idx += 1
        if ctrl.ws_state == WSState.STREAM:
            streamed += 1

    if dump_path is not None:
        import os
        os.makedirs(os.path.dirname(dump_path), exist_ok=True)
        with open(dump_path, "w") as fh:
            fh.write(f"{AR} {AC} {F} {len(rows)}\n")
            for r in rows:
                fh.write(" ".join(str(v) for v in r) + "\n")

    return np.array(accum.get_tile(0)), A @ B


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,N,M", [
    (2, 2, 3), (3, 3, 4), (3, 2, 5), (2, 4, 3),
    (4, 3, 3), (3, 3, 3), (4, 4, 6), (2, 2, 8),
])
def test_ws1_end_to_end(K, N, M, latency):
    got, want = _ws1(K, N, M, latency, seed=K * 100 + N * 10 + M + latency)
    np.testing.assert_array_equal(got, want)


def test_ws1_dump_cosim():
    """导出 sa_tb 逐拍对拍向量 build/sa_cosim/ws1.txt（RTL PIPE_MUL=1 ↔ latency=2）。"""
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    got, want = _ws1(2, 2, 3, 2, seed=223,
                     dump_path=os.path.join(root, "build", "sa_cosim", "ws1.txt"))
    np.testing.assert_array_equal(got, want)
