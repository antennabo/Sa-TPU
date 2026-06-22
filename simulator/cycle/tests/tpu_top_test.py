"""tpu_top 等效行为级 e2e：照 rtl/tinytpu_top.sv 结构组装 sim_model 跑 matmul。

组件对应：
  ControllerWSRef  ↔ rtl/controller_ws.sv
  CommonBuf        ↔ rtl/activation_buf.sv  (M lane 寻址缓冲，输出 1 拍寄存)
  CommonFIFO       ↔ rtl/weight_fifo.sv     (WS 读口：每列 vld/rdy 反压)
  spatial_array    ↔ rtl/systolic_array.sv  (MXU)
  Accumulator      ↔ 外部 accumulator       (top 现在还没集成)

输入约定（跟 RTL 的 tinytpu_top port 对应）：
  - 激活：预载 abuf（DMA 一拍写一行，外部驱动）
  - 权重：每拍 push 一行进 wfifo（倒序 B[AR-1..0]）；SA 的反压通过 b_rdy=!shadow_full 自动节流
  - start：cy=0 单拍脉冲
  - switch_weight：段末拍触发，切下一段
  - weight_loaded = !any(b_rdy) = all(shadow_full)，从 SA 推
"""

import numpy as np
import pytest

from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonbuf import CommonBuf
from simulator.cycle.sim_model.commonfifo import CommonFIFO
from simulator.cycle.sim_model.controller_ws_ref import ControllerWSRef, WSStateRef
from simulator.cycle.sim_model.accumulator import Accumulator


def _tpu_top_run(A, B_tiles, AR, AC, F, L=2, debug=True):
    """端到端跑一次 tpu_top 等效模型。
    A:        [F][AR]              激活（M=F 行，K=AR 列，不切 K）
    B_tiles:  list of [AR][AC]     按 N 方向切的权重 tiles，G_N = len(B_tiles)
    返回:     list of np.ndarray [F][AC]，每段一份 = A @ B_tiles[g]

    架构约束（多 tile back-to-back）：F >= AR + AC + 1。
    原因：下一段 weight 通过 wfifo 倒序灌入 SA reserve，column-stagger 1 拍/列；
    所有列 reserve 全满需要 AR + AC 拍，再加上 cold swap 后 1 拍延迟才看见 wl=T。
    """
    G_N = len(B_tiles)

    sa = spatial_array(AR, AC, latency=L,
                       is_shift_col=0, is_shift_row=1,
                       is_shift_acc_d=1, is_shift_acc_l=0)
    abuf  = CommonBuf(N=AR, K=F)
    wfifo = CommonFIFO(N=AC, K=AR)
    ctrl  = ControllerWSRef(AR=AR, AC=AC, LATENCY=L, K_ABUF_MAX=F)
    accum = Accumulator(num_rows=F, num_cols=AC)        # capture 对齐由 controller 内部做

    if debug:
        print(f"\n=== inputs  AR={AR} AC={AC} F={F} L={L}  G_N={G_N}  W={ctrl.W} ===")
        print(f"A [{F}x{AR}]:")
        for r in range(F):
            print(f"  row{r}: {[int(x) for x in A[r]]}")
        for g, B in enumerate(B_tiles):
            print(f"B_tiles[{g}] [{AR}x{AC}]:")
            for r in range(AR):
                print(f"  row{r}: {[int(x) for x in B[r]]}")
            exp = np.array(A) @ np.array(B)
            print(f"  expected A @ B_tiles[{g}] [{F}x{AC}]:")
            for r in range(F):
                print(f"    row{r}: {[int(x) for x in exp[r]]}")
        print()

        # ── 表头：分组 cy / state / sched / wl / shadow_full / wfifo_in / wfifo_out / abuf_out / sa_out / capture ──
        def _vec_h(name, n):
            return " ".join(f"{name}{i}" for i in range(n))
        _hdr = (f"{'cy':>3} | {'state':<7} {'cnt':>3} {'fd':>2} {'sw':>2} {'sg':>2}"
                f" | {_vec_h('bsw', AR)}"
                f" | {'wl':>2} {_vec_h('sf', AC)}"
                f" | {_vec_h('wfi', AC)} | {_vec_h('wfo', AC)}"
                f" | {_vec_h('ab', AR)} | {_vec_h('sa', AC)}"
                f" | {'wv':>2} {'wR':>2} {'wT':>2}")
        print(_hdr)
        print("-" * len(_hdr))

    # ── 激活预载（DMA 一拍写一行进 abuf，feed=False 不读）──
    for d in range(F):
        abuf.update([int(A[d][k]) for k in range(AR)], False); abuf.commit()

    # ── 主循环 ──
    weight_pushed     = 0                   # 已 push 进 wfifo 的总行数（含所有 tile）
    total_weight_rows = G_N * AR
    segments_done     = 0                   # 已经 sw=1 切走的段数（决定下次 sw 给不给）
    NCYC = G_N * (AR + F) + ctrl.W + AC + 20

    for cy in range(NCYC):
        # ── controller 上层握手 ──
        start = (cy == 0)
        wl    = all(sa.shadow_full)
        aa    = True                        # 激活已预载，恒可用
        # 段末拍判断：当前是 STREAM/OVERLAP 且 cnt==F-1，且后面还有段
        # sw_now: 段末触发 → 进 OVERLAP
        #   F > W: 段末 = CAPTURE 末拍 (cnt == F-W-1)
        #   F == W: 段末 = FEED 末拍（首段）或 OVERLAP 末拍（中间段）(cnt == W-1)
        if F > ctrl.W:
            sw_now = (ctrl.ws_state == WSStateRef.CAPTURE
                      and ctrl.ws_cnt == F - ctrl.W - 1
                      and segments_done < G_N - 1)
        else:   # F == W
            sw_now = (ctrl.ws_state in (WSStateRef.FEED, WSStateRef.OVERLAP)
                      and ctrl.ws_cnt == ctrl.W - 1
                      and segments_done < G_N - 1)
        # tag 在 cold/boundary 那一拍给对的就行（controller 内部已锁存）
        # cold：WLOAD→STREAM 那拍 i_tag = 段 0 的 tag = 0
        # boundary：sw=1 那拍 i_tag = 下一段 tag = segments_done + 1
        tag_now = segments_done + (1 if sw_now else 0)
        ctrl.update(start=start, weight_loaded=wl, activ_available=aa, F=F,
                    switch_weight=sw_now, tag=tag_now)

        # ── wfifo push (DMA：每拍 push 一行权重，倒序 B[AR-1..0]) ──
        if weight_pushed < total_weight_rows:
            tile_idx    = weight_pushed // AR
            row_in_tile = weight_pushed % AR
            wfifo_wdata = [int(B_tiles[tile_idx][AR - 1 - row_in_tile][n]) for n in range(AC)]
        else:
            wfifo_wdata = None

        # ── wfifo ws_update：组合 peek + 同拍 pop，ready 来自 SA pre-update b_rdy ──
        b_rdy = [not f for f in sa.shadow_full]
        wfifo.ws_update(wfifo_wdata, ready=b_rdy)
        if wfifo_wdata is not None:
            weight_pushed += 1

        # ── abuf read (controller.feed 触发) ──
        abuf.update(None, ctrl.feed)

        # ── SA ──
        sa.update(list(abuf.data), list(abuf.vld),
                  list(wfifo.data), list(wfifo.vld),
                  b_sw=list(ctrl.b_sw))

        # ── accumulator ──
        # controller 已经把 wr_* 信号延迟对齐到 col 0 psum 到达时刻，accumulator 直接吃
        accum.update(list(sa.data[AR - 1]), ctrl.wr_vld,
                     ctrl.wr_row, ctrl.wr_tile, False)

        if debug:
            state_name = WSStateRef(ctrl.ws_state).name
            def _fmt(x):    return "  -" if x is None else f"{int(x):>3}"
            def _vec(xs):   return " ".join(_fmt(v) for v in xs)

            # wfifo.data 只在 vld && b_rdy（该列这一拍真在 pop）时显示，否则 -
            wfifo_out = [wfifo.data[c] if (wfifo.vld[c] and b_rdy[c]) else None for c in range(AC)]
            # abuf.data 只在 vld（abuf 这一拍真有读出）时显示，否则 -
            abuf_out  = [abuf.data[c] if abuf.vld[c] else None for c in range(AR)]
            wfifo_in  = wfifo_wdata if wfifo_wdata is not None else [None] * AC

            print(f"{cy:>3} | {state_name:<7} {ctrl.ws_cnt:>3} {int(ctrl.feed):>2}"
                  f" {int(sw_now):>2} {segments_done:>2}"
                  f" | {_vec(ctrl.b_sw)}"
                  f" | {int(wl):>2} {_vec(sa.shadow_full)}"
                  f" | {_vec(wfifo_in)} | {_vec(wfifo_out)}"
                  f" | {_vec(abuf_out)} | {_vec(sa.data[AR-1])}"
                  f" | {int(ctrl.wr_vld):>2} {ctrl.wr_row:>2} {ctrl.wr_tile:>2}")

        # ── commit ──
        ctrl.commit(); abuf.commit(); wfifo.commit(); sa.commit(); accum.commit()

        # 段计数：刚刚执行 sw=1 → segments_done +1
        if sw_now:
            segments_done += 1

    return [np.array(accum.get_tile(g)) for g in range(G_N)]


# ────────────────────────────────────────────────────────────────────────── #
# Tests
# ────────────────────────────────────────────────────────────────────────── #

@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,N,M", [
    # 约束：M (=F) >= W = K + L + 1。L=2 时 M >= K+3。
    (2, 2, 5), (3, 3, 6), (4, 4, 7), (4, 4, 8),
])
def test_tpu_top_single_tile(K, N, M, latency):
    """单 N-tile（G_N=1）：跑一段，无 switch_weight，验证 A@B。"""
    rng = np.random.default_rng(K * 100 + N * 10 + M + latency)
    A = rng.integers(-3, 4, size=(M, K))            # 激活 M×K
    B = rng.integers(-3, 4, size=(K, N))            # 权重 K×N
    got = _tpu_top_run(A, [B], AR=K, AC=N, F=M, L=latency)
    np.testing.assert_array_equal(got[0], A @ B)


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("K,N_per_tile,M,G_N", [
    # 约束：M (=F) >= K + N_per_tile + 1 才能 back-to-back（否则下一段 weight 没灌完）
    (2, 2, 5, 2),
    (3, 3, 7, 2),
    (4, 4, 9, 2),
    (2, 2, 5, 3),
])
def test_tpu_top_multi_tile(K, N_per_tile, M, G_N, latency):
    """多 N-tile（G_N 段）：每段切 switch_weight，验证拼接 = A @ horzcat(B_tiles)。"""
    rng = np.random.default_rng(K * 1000 + N_per_tile * 100 + M * 10 + G_N + latency)
    A = rng.integers(-3, 4, size=(M, K))            # 激活共享
    B_full = rng.integers(-3, 4, size=(K, N_per_tile * G_N))
    B_tiles = [B_full[:, g * N_per_tile:(g + 1) * N_per_tile] for g in range(G_N)]
    got = _tpu_top_run(A, B_tiles, AR=K, AC=N_per_tile, F=M, L=latency)
    want_full = A @ B_full
    got_full = np.hstack(got)
    np.testing.assert_array_equal(got_full, want_full)
