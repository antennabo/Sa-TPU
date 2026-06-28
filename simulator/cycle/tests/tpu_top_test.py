"""tpu_top 等效行为级 e2e（新接口）：照 rtl/tinytpu_top.sv 结构组装 sim_model 跑 matmul。

组件对应：
  ControllerWSRef  ↔ rtl/controller_ws.sv  (7 态 FSM + per-AC deskew SR + abuf 读地址)
  CommonBuf        ↔ rtl/activation_buf.sv (M lane 纯被动 RAM)
  CommonFIFO       ↔ rtl/weight_fifo.sv    (WS 读口：每列 vld/rdy 反压)
  spatial_array    ↔ rtl/systolic_array.sv
  Accumulator      ↔ rtl/accumulator.sv     (per-AC 直收, 地址平坦)

指令字段（指令内常量）：
  wtile_num — 跑几个 weight tile (≥1)，每个 wtile 写到 acc[wtile_idx*feed_num .. ]
  feed_num  — 每个 wtile 读 feed_num 行 activation = 写 feed_num 行 psum (≥W)
  act_staddr — abuf 读起点 (所有 wtile 共用同一份 activation)
  acc_staddr — accumulator 写起点 (首 wtile)

约束 (D8)：W = AR + LATENCY；feed_num ≥ W；所有 wtile 共用同一份 A。

cosim dump：test_tpu_top_dump_* 系列把 sim 全程的 per-cycle 输入 + 末状态 accumulator
golden 写出 build/tpu_top_cosim/*.txt，由 tb/tpu_top_tb 回放对拍 RTL。
"""

import os
import numpy as np
import pytest

from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.commonbuf import CommonBuf
from simulator.cycle.sim_model.commonfifo import CommonFIFO
from simulator.cycle.sim_model.controller_ws_ref import ControllerWSRef, WSStateRef
from simulator.cycle.sim_model.accumulator import Accumulator


def _tpu_top_run(A, B_tiles, AR, AC, L=2, debug=False):
    """端到端跑一次 tpu_top 等效模型。

    A:        [M][AR]              激活（M = feed_num，所有 wtile 共用）
    B_tiles:  list of [AR][AC]     权重 tiles，wtile_num = len(B_tiles)
    返回:     list of np.ndarray [M][AC]，每段 = A @ B_tiles[g]，来自 accum 不同 addr 区间
    """
    wtile_num = len(B_tiles)
    W = AR + L
    M = A.shape[0]                                                # = feed_num
    assert M >= W, f"M={M} must be ≥ W={W}"
    assert A.shape == (M, AR)

    feed_num   = M
    act_staddr = 0
    acc_staddr = 0
    mem_depth  = wtile_num * feed_num + 16
    abuf_depth = max(M, 4)

    sa = spatial_array(AR, AC, latency=L,
                       is_shift_col=0, is_shift_row=1,
                       is_shift_acc_d=1, is_shift_acc_l=0)
    abuf  = CommonBuf(N=AR, DEPTH=abuf_depth)
    wfifo = CommonFIFO(N=AC, K=AR)
    ctrl  = ControllerWSRef(AR=AR, AC=AC, LATENCY=L,
                            WTILE_NUM_MAX=max(wtile_num, 1),
                            ACT_ADDR_W=max(int(np.ceil(np.log2(abuf_depth))), 1),
                            ACC_ADDR_W=max(int(np.ceil(np.log2(mem_depth))), 1))
    accum = Accumulator(num_cols=AC, mem_depth=mem_depth)

    # ── 激活预载（DMA 一拍写一行进 abuf）──
    no_rd_en   = [False] * AR
    no_rd_addr = [0] * AR
    for d in range(M):
        abuf.update([True] * AR, [d] * AR,
                    [int(A[d][k]) for k in range(AR)],
                    no_rd_en, no_rd_addr)
        abuf.commit()

    # ── 主循环 ──
    weight_pushed     = 0
    total_weight_rows = wtile_num * AR
    NCYC = wtile_num * (M + W) + AR + AC + 40
    no_acc_en  = [False] * AC
    no_acc_rd  = [False] * AC
    zero_addr  = [0] * AC

    for cy in range(NCYC):
        start = (cy == 0)
        wl    = all(sa.shadow_full)
        aa    = True

        ctrl.update(start=start, weight_loaded=wl, activ_available=aa,
                    wtile_num=wtile_num, act_staddr=act_staddr,
                    acc_staddr=acc_staddr, feed_num=feed_num)

        # ── wfifo push（倒序 B[AR-1..0] 推入）──
        if weight_pushed < total_weight_rows:
            tile_idx    = weight_pushed // AR
            row_in_tile = weight_pushed % AR
            wfifo_wdata = [int(B_tiles[tile_idx][AR - 1 - row_in_tile][n])
                           for n in range(AC)]
        else:
            wfifo_wdata = None

        b_rdy = [not f for f in sa.shadow_full]
        wfifo.ws_update(wfifo_wdata, ready=b_rdy)
        if wfifo_wdata is not None:
            weight_pushed += 1

        # ── abuf read ──
        abuf.update([False] * AR, [0] * AR, [0] * AR,
                    list(ctrl.act_ren), list(ctrl.act_raddr))

        # ── SA ──
        sa.update(list(abuf.data), list(abuf.vld),
                  list(wfifo.data), list(wfifo.vld),
                  b_sw=list(ctrl.weight_sw))

        # ── accumulator（per-AC 直收 controller 已 deskew 信号）──
        # psum_vld 全 True（SA 模型每拍都有 psum，由 controller 的 acc_wen 闸有效写）
        accum.update(i_psum=list(sa.data[AR - 1]),
                     i_psum_vld=[True] * AC,
                     i_wr_vld=list(ctrl.acc_wen),
                     i_wr_addr=list(ctrl.acc_waddr),
                     i_acc_en=no_acc_en,
                     i_out_en=list(ctrl.acc_outen),
                     i_rd_en=no_acc_rd, i_rd_addr=zero_addr)

        if debug:
            print(f"cy={cy:3d} {WSStateRef(ctrl.ws_state).name:7} cnt={ctrl.ws_cnt} "
                  f"idx={ctrl.wtile_idx} wr_row={ctrl.wr_row} "
                  f"act_ren={[int(x) for x in ctrl.act_ren]} "
                  f"raddr={ctrl.act_raddr} wen={[int(x) for x in ctrl.acc_wen]} "
                  f"waddr={ctrl.acc_waddr}")

        ctrl.commit(); abuf.commit(); wfifo.commit(); sa.commit(); accum.commit()

    # 提取每个 wtile 的输出：wtile g 写到 acc[g*feed_num .. (g+1)*feed_num)
    out = []
    for g in range(wtile_num):
        tile = np.zeros((feed_num, AC), dtype=int)
        for c in range(AC):
            col = accum.get_col(c)
            for r in range(feed_num):
                tile[r][c] = col[acc_staddr + g * feed_num + r]
        out.append(tile)
    return out


# ────────────────────────────────────────────────────────────────────────── #
# Tests
# ────────────────────────────────────────────────────────────────────────── #

@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("AR,AC,M_extra", [
    (2, 2, 0),    # M = W
    (2, 2, 4),    # M = W + 4 (有 CAPTURE 段)
    (3, 3, 0),
    (3, 3, 5),
    (4, 4, 6),
])
def test_tpu_top_single_wtile(AR, AC, M_extra, latency):
    """单 weight tile (wtile_num=1)：直接 A @ B 验证。"""
    W = AR + latency
    M = W + M_extra
    rng = np.random.default_rng(AR * 1000 + AC * 100 + M * 10 + latency)
    A = rng.integers(-3, 4, size=(M, AR))
    B = rng.integers(-3, 4, size=(AR, AC))
    got = _tpu_top_run(A, [B], AR=AR, AC=AC, L=latency)
    np.testing.assert_array_equal(got[0], A @ B)


@pytest.mark.parametrize("latency", [1, 2])
@pytest.mark.parametrize("AR,AC,wtile_num,M_extra", [
    # 多 wtile，共享同一份 A；每个 wtile 用不同 B
    (2, 2, 2, 4),
    (2, 2, 3, 4),
    (3, 3, 2, 6),
    (4, 4, 2, 6),
])
def test_tpu_top_multi_wtile(AR, AC, wtile_num, M_extra, latency):
    """多 weight tile，wtile_num 段权重，每段独立 A @ B_g。"""
    W = AR + latency
    M = W + M_extra
    rng = np.random.default_rng(AR * 1000 + AC * 100 + M * 10 + wtile_num + latency)
    A = rng.integers(-3, 4, size=(M, AR))
    B_tiles = [rng.integers(-3, 4, size=(AR, AC)) for _ in range(wtile_num)]
    got = _tpu_top_run(A, B_tiles, AR=AR, AC=AC, L=latency)
    for g in range(wtile_num):
        np.testing.assert_array_equal(got[g], A @ B_tiles[g])


# ────────────────────────────────────────────────────────────────────────── #
# cosim dump：跑 sim_model 把 per-cycle 输入 + 末状态 accumulator 写 txt，
# tb/tpu_top_tb 回放对拍 RTL。
# ────────────────────────────────────────────────────────────────────────── #


def _pow2(x):
    """≥ x 的最小 2 的幂（RTL $clog2 走整数向上的位宽，TB 用 2 的幂当 DEPTH 简单匹配）。"""
    n = 1
    while n < x:
        n <<= 1
    return n


def _dump_tpu_top(A, B_tiles, AR, AC, L, dump_path, desc, debug=False):
    """跑全程 sim_model，逐拍 dump 输入 + 末状态 accumulator golden。

    每拍记录 TB 要驱动的：start aA wr_en wr_addr wd[AR] wfv wfd[AC]
    （instr 常量 wtile_num/act_staddr/acc_staddr/feed_num 写头行，TB 复位后常驱即可）。

    流程对齐 _tpu_top_run，但把 abuf preload + main loop 合成一条 NCYC 序列，
    preload 段所有 FSM/SA/wfifo/accum 一并 tick (输入恒 0/IDLE，不影响状态)。
    """
    wtile_num   = len(B_tiles)
    W           = AR + L
    M           = A.shape[0]
    assert M >= W, f"M={M} must be ≥ W={W}"
    assert A.shape == (M, AR)

    feed_num    = M
    act_staddr  = 0
    acc_staddr  = 0
    mem_depth   = _pow2(wtile_num * feed_num + 16)
    abuf_depth  = _pow2(max(M, 4))

    sa    = spatial_array(AR, AC, latency=L,
                          is_shift_col=0, is_shift_row=1,
                          is_shift_acc_d=1, is_shift_acc_l=0)
    abuf  = CommonBuf(N=AR, DEPTH=abuf_depth)
    wfifo = CommonFIFO(N=AC, K=AR)
    ctrl  = ControllerWSRef(AR=AR, AC=AC, LATENCY=L,
                            WTILE_NUM_MAX=max(wtile_num, 1),
                            ACT_ADDR_W=max(int(np.ceil(np.log2(abuf_depth))), 1),
                            ACC_ADDR_W=max(int(np.ceil(np.log2(mem_depth))), 1))
    accum = Accumulator(num_cols=AC, mem_depth=mem_depth)

    rows = []
    no_acc_rd = [False] * AC
    zero_addr = [0]     * AC

    def _tick(start, aA, wr_en, wr_addr, wr_data, wfifo_wdata):
        """模拟一拍：先 update 所有模块，再 commit；同时把 TB 驱动 vector 写进 rows。"""
        wfv = 1 if wfifo_wdata is not None else 0
        wfd = list(wfifo_wdata) if wfifo_wdata is not None else [0] * AC
        rows.append({
            'start':   int(start),   'aA':      int(aA),
            'wr_en':   int(wr_en),   'wr_addr': int(wr_addr),
            'wr_data': list(wr_data),
            'wfv':     wfv,          'wfd':     wfd,
        })

        wL = all(sa.shadow_full)
        ctrl.update(start=start, weight_loaded=wL, activ_available=aA,
                    wtile_num=wtile_num, act_staddr=act_staddr,
                    acc_staddr=acc_staddr, feed_num=feed_num)
        b_rdy = [not f for f in sa.shadow_full]
        wfifo.ws_update(wfifo_wdata, ready=b_rdy)
        abuf.update([bool(wr_en)] * AR, [wr_addr] * AR, list(wr_data),
                    list(ctrl.act_ren), list(ctrl.act_raddr))
        sa.update(list(abuf.data), list(abuf.vld),
                  list(wfifo.data), list(wfifo.vld),
                  b_sw=list(ctrl.weight_sw))
        accum.update(i_psum=list(sa.data[AR - 1]), i_psum_vld=[True] * AC,
                     i_wr_vld=list(ctrl.acc_wen),  i_wr_addr=list(ctrl.acc_waddr),
                     i_acc_en=[False] * AC,        i_out_en=list(ctrl.acc_outen),
                     i_rd_en=no_acc_rd,            i_rd_addr=zero_addr)
        ctrl.commit(); abuf.commit(); wfifo.commit(); sa.commit(); accum.commit()

    def _dump_pe_b(tag, cy, pushed=None):
        sw_edge = [int(x) for x in ctrl.weight_sw]
        bsw_g   = [[int(sa._bsw_grid[r][c]) for c in range(AC)] for r in range(AR)]
        pe_b    = [[int(sa.pes[r][c].b)     for c in range(AC)] for r in range(AR)]
        pe_buf  = [[int(sa.pes[r][c].b_buf) for c in range(AC)] for r in range(AR)]
        pe_bv   = [[int(sa.pes[r][c].b_buf_vld) for c in range(AC)] for r in range(AR)]
        # wfifo→sa 接口：wfifo.data/vld 是上一拍 commit 后 sa 这一拍读到的值
        fifo_d  = [int(x) for x in wfifo.data]
        fifo_v  = [int(x) for x in wfifo.vld]
        # 每 lane fifo 当前长度（debug shadow 反压时挺有用）
        fifo_len = [len(wfifo._fifos[c]) for c in range(AC)]
        # b_rdy[c] = 该列 PE(0).b_rdy（_route_b ready 链顶端）
        b_rdy = [(not sa.pes[AR - 1][c].b_buf_vld)
                 or any(not sa.pes[k][c].b_buf_vld for k in range(AR - 1))
                 for c in range(AC)]
        push_str = f" pushed={pushed}" if pushed is not None else " pushed=None"
        print(f"{tag} cy={cy:3d} state={WSStateRef(ctrl.ws_state).name:7} "
              f"cnt={ctrl.ws_cnt} idx={ctrl.wtile_idx} "
              f"weight_sw={sw_edge} bsw_grid={bsw_g}")
        print(f"        wfifo→sa  data={fifo_d} vld={fifo_v} "
              f"flen={fifo_len} b_rdy={[int(x) for x in b_rdy]}{push_str}")
        for r in range(AR):
            print(f"        row{r}  b={pe_b[r]}  b_buf={pe_buf[r]}  b_buf_vld={pe_bv[r]}")

    # ── Phase 1: abuf 预载（M 拍，FSM IDLE）──
    for d in range(M):
        wr_data = [int(A[d][k]) for k in range(AR)]
        _tick(start=False, aA=False, wr_en=True, wr_addr=d, wr_data=wr_data,
              wfifo_wdata=None)

    # ── Phase 2: 主循环 ──
    weight_pushed     = 0
    total_weight_rows = wtile_num * AR
    NCYC_MAIN = wtile_num * (M + W) + AR + AC + 40

    for cy_main in range(NCYC_MAIN):
        start_pulse = (cy_main == 0)
        if weight_pushed < total_weight_rows:
            tile_idx    = weight_pushed // AR
            row_in_tile = weight_pushed % AR
            wfifo_wdata = [int(B_tiles[tile_idx][AR - 1 - row_in_tile][n])
                           for n in range(AC)]
            weight_pushed += 1
        else:
            wfifo_wdata = None
        _tick(start=start_pulse, aA=True,
              wr_en=False, wr_addr=0, wr_data=[0] * AR,
              wfifo_wdata=wfifo_wdata)
        if debug:
            _dump_pe_b("[post-commit]", cy_main, pushed=wfifo_wdata)

    # ── 末状态 sanity：accumulator 内存等于 A @ B_tiles[g]──
    mismatches = []
    actuals   = []
    for g in range(wtile_num):
        expected = A @ B_tiles[g]
        actual   = np.zeros((feed_num, AC), dtype=int)
        for c in range(AC):
            col = accum.get_col(c)
            for r in range(feed_num):
                actual[r, c] = col[acc_staddr + g * feed_num + r]
        actuals.append(actual)
        if not np.array_equal(actual, expected):
            mismatches.append(g)

    if mismatches:
        print(f"\n=== sim_model dump 末状态错位 ({desc}) ===")
        print(f"AR={AR} AC={AC} L={L} M={M} W={W} "
              f"wtile_num={wtile_num} feed_num={feed_num} "
              f"acc_staddr={acc_staddr} mem_depth={mem_depth}")
        print(f"A ({A.shape})=\n{A}")
        for g in range(wtile_num):
            exp = A @ B_tiles[g]
            got = actuals[g]
            tag = "MISMATCH" if g in mismatches else "ok"
            base = acc_staddr + g * feed_num
            print(f"\n--- wtile g={g} [{tag}]  acc[{base}..{base+feed_num}) ---")
            print(f"B_tiles[{g}] ({B_tiles[g].shape})=\n{B_tiles[g]}")
            print(f"expected (A @ B)=\n{exp}")
            print(f"got      (accum)=\n{got}")
            if g in mismatches:
                print(f"diff (got - exp)=\n{got - exp}")
        g0 = mismatches[0]
        exp0 = A @ B_tiles[g0]
        got0 = actuals[g0]
        rr, cc = np.argwhere(got0 != exp0)[0]
        raise AssertionError(
            f"sim_model dump 末状态错位 wtile={g0} row={rr} col={cc} "
            f"got={int(got0[rr,cc])} exp={int(exp0[rr,cc])}")

    # ── golden 扁平（每行 AC 个）──
    golden_rows = []
    for g in range(wtile_num):
        for r in range(feed_num):
            golden_rows.append([
                int(accum.get_col(c)[acc_staddr + g * feed_num + r])
                for c in range(AC)
            ])

    NCYC = len(rows)
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w") as fh:
        fh.write(f"# tpu_top cosim  AR={AR} AC={AC} W={W} "
                 f"wtile_num={wtile_num} feed_num={feed_num} "
                 f"abuf_depth={abuf_depth} accum_depth={mem_depth} NCYC={NCYC}\n")
        fh.write(f"# {desc}\n")
        fh.write("# 头行: AR AC WTILE_NUM ACT_STADDR ACC_STADDR FEED_NUM ABUF_DEPTH ACCUM_DEPTH NCYC\n")
        fh.write("# 数据行: cy start aA wr_en wr_addr wd[0..AR-1] wfv wfd[0..AC-1]\n")
        fh.write("# golden: WTILE_NUM*FEED_NUM 行 × AC 列 "
                 "(= wtile g 写到 acc[acc_staddr + g*feed_num .. ))\n")
        fh.write("#\n")
        fh.write(f"{AR} {AC} {wtile_num} {act_staddr} {acc_staddr} {feed_num} "
                 f"{abuf_depth} {mem_depth} {NCYC}\n")
        for cy, r in enumerate(rows):
            vals = [cy, r['start'], r['aA'], r['wr_en'], r['wr_addr']]
            vals += r['wr_data']
            vals += [r['wfv']]
            vals += r['wfd']
            fh.write(" ".join(f"{v:>5d}" for v in vals) + "\n")
        for row in golden_rows:
            fh.write(" ".join(f"{v:>7d}" for v in row) + "\n")

    return rows, golden_rows


def _cosim_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_tpu_top_dump_small():
    """Single wtile, M=W: 最小 scenario，cover IDLE→WLOAD→FEED→DRAIN→IDLE。"""
    AR, AC, L = 2, 2, 2
    W = AR + L
    M = W
    rng = np.random.default_rng(20260628)
    A = rng.integers(-3, 4, size=(M, AR))
    B = rng.integers(-3, 4, size=(AR, AC))
    _dump_tpu_top(A, [B], AR=AR, AC=AC, L=L,
                  dump_path=os.path.join(_cosim_root(), "build", "tpu_top_cosim", "small.txt"),
                  desc=f"single wtile  AR={AR} AC={AC} L={L} M=W={M}")


def test_tpu_top_dump_switch():
    """Two wtile, M=W+4: cover FEED→CAPTURE→OVERLAP→CAPTURE→DRAIN。"""
    AR, AC, L = 2, 2, 2
    W = AR + L
    M = W + 4
    rng = np.random.default_rng(20260628 + 1)
    A = rng.integers(-3, 4, size=(M, AR))
    B_tiles = [rng.integers(-3, 4, size=(AR, AC)) for _ in range(2)]
    _dump_tpu_top(A, B_tiles, AR=AR, AC=AC, L=L,
                  dump_path=os.path.join(_cosim_root(), "build", "tpu_top_cosim", "switch.txt"),
                  desc=f"two wtile  AR={AR} AC={AC} L={L} M=W+4={M}",
                  debug=True)
