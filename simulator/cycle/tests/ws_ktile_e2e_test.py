import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonbuf import CommonBuf
from simulator.cycle.sim_model.controller import Controller, WSState
from simulator.cycle.sim_model.accumulator import Accumulator


def _ws_ktile(AR, AC, F, L, Gk, seed):
    """WS K-tiling 端到端：证明【跨 K-chunk 累加】——reduction 维 K 大于阵列行数时切成 Gk 个
    K-chunk，逐 chunk 跑 WS 单 tile 闭环，部分和靠 accumulator 的 add 累加进同一槽。

    WS 阵列只有 AR 行（= 一个 K-chunk 的深度），完整 K = Gk*AR 塞不下，沿 K 切 Gk 段。
    每个 K-chunk：fresh sa（载本段权重 B_chunk、清阵列）+ fresh ctrl + 重载本段激活 A_chunk，
    底行 psum 写 accum slot 0：kc==0 覆写、kc>0 累加（add=True）。Gk 段累加完 = A@B。
    返回 (accum tile0, A @ B)。AR=K-chunk 深, AC=N, F=M, Gk=K 切片数。
    """
    rng = np.random.default_rng(seed)
    Kfull = Gk * AR
    B = rng.integers(-3, 4, size=(Kfull, AC))         # 权重 K_full×N
    A = rng.integers(-3, 4, size=(F, Kfull))          # 激活 M×K_full
    cap_delay = (AR - 1) + L + 2

    accum = Accumulator(num_rows=F, num_cols=AC, cap_delay=cap_delay)   # 跨 chunk 共用

    for kc in range(Gk):
        A_chunk = A[:, kc * AR:(kc + 1) * AR]         # 本段激活 M×AR
        B_chunk = B[kc * AR:(kc + 1) * AR, :]         # 本段权重 AR×N
        sa = spatial_array(AR, AC, latency=L,
                           is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
        ab = CommonBuf(N=AR, K=F)                      # 每段重载本段激活
        ctrl = Controller(AR, AC, F, mode="WS", latency=L)

        for d in range(F):
            ab.update([int(A_chunk[d][k]) for k in range(AR)], False); ab.commit()

        load_idx = streamed = 0
        for cy in range(2 * AR + F + AC + L + 14):
            wa = all(sa.shadow_full)
            aa = streamed < (F + 1)
            ctrl.update(cy, weight_available=wa, activ_available=aa)
            if ctrl.ws_state == WSState.WLOAD and load_idx < AR:
                b_data = [int(B_chunk[AR - 1 - load_idx][n]) for n in range(AC)]; b_vld = [True] * AC
                do_load = True
            else:
                b_data = [0] * AC; b_vld = [False] * AC; do_load = False
            ab.update(None, ctrl.feed)
            sa.update(list(ab.data), list(ab.vld), b_data, b_vld, b_sw=list(ctrl.b_sw))
            cap_vld = ctrl.m is not None
            accum.update(list(sa.data[AR - 1]),
                         cap_vld,
                         ctrl.m if cap_vld else 0,
                         0,
                         kc > 0)                       # kc==0 覆写，kc>0 累加
            ctrl.commit(); ab.commit(); sa.commit(); accum.commit()
            if do_load:
                load_idx += 1
            if ctrl.ws_state == WSState.STREAM:
                streamed += 1

    return np.array(accum.get_tile(0)), A @ B


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("AR,AC,F,Gk", [
    (2, 2, 3, 2), (3, 3, 4, 2), (3, 2, 5, 3),
    (4, 3, 3, 2), (2, 2, 8, 3), (8, 8, 8, 8),
])
def test_ws_ktile_accumulate(AR, AC, F, Gk, latency):
    got, want = _ws_ktile(AR, AC, F, latency, Gk, seed=AR * 100 + AC * 10 + F + Gk + latency)
    np.testing.assert_array_equal(got, want)
