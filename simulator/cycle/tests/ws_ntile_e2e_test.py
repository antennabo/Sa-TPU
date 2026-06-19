import numpy as np
import pytest
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonbuf import CommonBuf
from simulator.cycle.sim_model.controller import Controller, WSState
from simulator.cycle.sim_model.accumulator import Accumulator


def _ws_ntile(AR, AC, F, L, Gn, seed):
    """WS 多 N-tile 端到端：证明 CommonBuf 的【可重读复用】——同一份激活喂多个 N-tile。

    权重 K×(Gn*AC) 沿 N 切成 Gn 个 AC 宽的块；激活 A(M×K)被【所有 N-tile 共用】。
    buf 只预载一次 A（cap = tile_num*K = 1*F = F）：每个 N-tile 流完 F 行，读指针自加到顶
    归 0，自动再供同一份 A 给下个 N-tile —— 不重新 DMA（buf 区别于 fifo 的核心价值）。

    每个 N-tile：fresh sa（载新权重块、清阵列）+ fresh ctrl，复用同一个 buf 与 accum。
    返回 (accum 各 tile 拼成的 M×(Gn*AC), A @ B_full)。AR=K, AC=列宽, F=M。
    """
    rng = np.random.default_rng(seed)
    Ntot = Gn * AC
    B = rng.integers(-3, 4, size=(AR, Ntot))          # 权重 K×(Gn*AC)
    A = rng.integers(-3, 4, size=(F, AR))             # 激活 M×K（全 N-tile 共用）
    cap_delay = (AR - 1) + L + 2

    ab = CommonBuf(N=AR, K=F)                          # 只此一个 buf，预载一次 A
    accum = Accumulator(num_rows=F, num_cols=AC, cap_delay=cap_delay)

    # buf 预载 A（lane k = A[:,k]）：唯一一次 DMA 写入
    for d in range(F):
        ab.update([int(A[d][k]) for k in range(AR)], False); ab.commit()

    for j in range(Gn):
        B_blk = B[:, j * AC:(j + 1) * AC]             # 本 N-tile 的权重块 K×AC
        sa = spatial_array(AR, AC, latency=L,
                           is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
        ctrl = Controller(AR, AC, F, mode="WS", latency=L)

        load_idx = streamed = 0
        for cy in range(2 * AR + F + AC + L + 14):
            wa = all(sa.shadow_full)                  # 真实 weight_available
            aa = streamed < (F + 1)                   # 喂完即停 → DRAIN
            ctrl.update(cy, weight_available=wa, activ_available=aa)
            if ctrl.ws_state == WSState.WLOAD and load_idx < AR:
                b_data = [int(B_blk[AR - 1 - load_idx][n]) for n in range(AC)]; b_vld = [True] * AC
                do_load = True
            else:
                b_data = [0] * AC; b_vld = [False] * AC; do_load = False
            ab.update(None, ctrl.feed)                # buf 不重载：指针自加/归 0 复用同一份 A
            sa.update(list(ab.data), list(ab.vld), b_data, b_vld, b_sw=list(ctrl.b_sw))
            cap_vld = ctrl.m is not None
            accum.update(list(sa.data[AR - 1]),
                         cap_vld,
                         ctrl.m if cap_vld else 0,
                         j if cap_vld else 0,          # 落本 N-tile 的 tile slot j
                         False)
            ctrl.commit(); ab.commit(); sa.commit(); accum.commit()
            if do_load:
                load_idx += 1
            if ctrl.ws_state == WSState.STREAM:
                streamed += 1

    got = np.concatenate([np.array(accum.get_tile(j)) for j in range(Gn)], axis=1)
    return got, A @ B


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,AC,M,Gn", [
    (2, 2, 3, 2), (3, 3, 4, 2), (3, 2, 5, 3),
    (4, 3, 3, 2), (2, 2, 8, 3), (3, 3, 3, 4),
])
def test_ws_ntile_reuse(K, AC, M, Gn, latency):
    got, want = _ws_ntile(K, AC, M, latency, Gn, seed=K * 100 + AC * 10 + M + Gn + latency)
    np.testing.assert_array_equal(got, want)


def _run_ws_phase(ab, accum, B_blk, AR, AC, F, L, slot):
    """在【共用 buf ab 的当前活动页】上跑一遍 WS 单 tile 闭环，结果落 accum slot。
    fresh sa（载权重块、清阵列）+ fresh ctrl；buf 不重载（读当前页），accum 共用。
    """
    sa = spatial_array(AR, AC, latency=L,
                       is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
    ctrl = Controller(AR, AC, F, mode="WS", latency=L)
    load_idx = streamed = 0
    for cy in range(2 * AR + F + AC + L + 14):
        wa = all(sa.shadow_full)
        aa = streamed < (F + 1)
        ctrl.update(cy, weight_available=wa, activ_available=aa)
        if ctrl.ws_state == WSState.WLOAD and load_idx < AR:
            b_data = [int(B_blk[AR - 1 - load_idx][n]) for n in range(AC)]; b_vld = [True] * AC
            do_load = True
        else:
            b_data = [0] * AC; b_vld = [False] * AC; do_load = False
        ab.update(None, ctrl.feed)                    # 读当前活动页（switch_page 决定哪页）
        sa.update(list(ab.data), list(ab.vld), b_data, b_vld, b_sw=list(ctrl.b_sw))
        cap_vld = ctrl.m is not None
        accum.update(list(sa.data[AR - 1]),
                     cap_vld,
                     ctrl.m if cap_vld else 0,
                     slot if cap_vld else 0,
                     False)
        ctrl.commit(); ab.commit(); sa.commit(); accum.commit()
        if do_load:
            load_idx += 1
        if ctrl.ws_state == WSState.STREAM:
            streamed += 1


def _ws_pingpong(AR, AC, F, L, seed):
    """WS ping-pong 端到端：证明 CommonBuf 的【双缓冲切页】——两页各装一份激活，switch_page 换用。

    双缓冲典型场景：权重 B 驻留，激活分批；一页算、另一页 DMA 载下批，switch_page 翻页隐藏访存。
    这里把两页都先写满（page0←A0、page1←A1），再切页分别计算，证明两页数据互不串。
    返回 ([slot0, slot1], [A0@B, A1@B])。
    """
    rng = np.random.default_rng(seed)
    B = rng.integers(-3, 4, size=(AR, AC))            # 权重 K×AC（两页共用）
    A0 = rng.integers(-3, 4, size=(F, AR))            # page0 激活
    A1 = rng.integers(-3, 4, size=(F, AR))            # page1 激活
    cap_delay = (AR - 1) + L + 2

    ab = CommonBuf(N=AR, K=F)
    accum = Accumulator(num_rows=F, num_cols=AC, cap_delay=cap_delay)

    # 两页都写上数据：page0←A0，切页，page1←A1，再切回 page0
    for d in range(F):
        ab.update([int(A0[d][k]) for k in range(AR)], False); ab.commit()   # 写 page0
    ab.switch_page()
    for d in range(F):
        ab.update([int(A1[d][k]) for k in range(AR)], False); ab.commit()   # 写 page1
    ab.switch_page()                                  # 回到 page0

    _run_ws_phase(ab, accum, B, AR, AC, F, L, slot=0)  # 算 page0 → slot0
    ab.switch_page()                                   # 翻到 page1
    _run_ws_phase(ab, accum, B, AR, AC, F, L, slot=1)  # 算 page1 → slot1

    got = [np.array(accum.get_tile(0)), np.array(accum.get_tile(1))]
    return got, [A0 @ B, A1 @ B]


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,AC,M", [
    (2, 2, 3), (3, 3, 4), (3, 2, 5), (4, 3, 3), (2, 2, 8),
])
def test_ws_pingpong(K, AC, M, latency):
    got, want = _ws_pingpong(K, AC, M, latency, seed=K * 100 + AC * 10 + M + latency)
    for g, w in zip(got, want):
        np.testing.assert_array_equal(g, w)
