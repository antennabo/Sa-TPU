"""controller_ws cosim 向量 dump。

跑 ControllerWSRef，逐拍 dump 输入 + 期望输出到 build/ctrl_ws_cosim/*.txt，
tb/ctrl_ws_tb 用同一份 .txt 喂 RTL 比对。

每行（row N）含义：cy=N 拍 TB 要驱动的输入 + 该拍 RTL 应该输出的值
                  （= ref pre-tick 状态，即上一拍 commit 后的寄存器值）。

数据行字段：cy start wL aA sw tag tn sp  state feed b_sw[AR]  wr_row wr_tile wr_vld  rd_en[AR] rd_addr[AR]
   start = 启动脉冲（上升沿触发 IDLE→WLOAD），wL = weight_loaded（shadow 满），aA = activ_available
   wr_row/wr_tile/wr_vld = controller 内部 SR 延迟 CAP_DELAY 拍后的 capture 信号（对齐 col 0 psum）
"""

import os
from simulator.cycle.sim_model.controller_ws_ref import ControllerWSRef


def _snapshot(ctrl):
    return {
        "state":   int(ctrl.ws_state),
        "feed":    int(ctrl.feed),
        "b_sw":    [int(x) for x in ctrl.b_sw],
        "wr_row":  int(ctrl.wr_row),
        "wr_tile": int(ctrl.wr_tile),
        "wr_vld":  int(ctrl.wr_vld),
        "rd_en":   [int(x) for x in ctrl.rd_en],
        "rd_addr": [int(x) for x in ctrl.rd_addr],
    }


def _row_values(cy, drive, expect):
    start, wL, aA, sw, tag, tn, sp = drive
    vals = [cy, start, wL, aA, sw, tag, tn, sp,
            expect["state"], expect["feed"]]
    vals += expect["b_sw"]
    vals += [expect["wr_row"], expect["wr_tile"], expect["wr_vld"]]
    vals += expect["rd_en"]
    vals += expect["rd_addr"]
    return vals


def _dump_ctrl_ws(ctrl, F, drives, dump_path, desc):
    """drives: list of (start, wL, aA, sw, tag, tn, sp) tuples，每个 tuple 表示一拍 TB 要驱动的输入。
       F：当前作业的 M 维（激活行数），作为运行时输入跟着 tick 走。
       start=启动脉冲（上升沿触发 IDLE→WLOAD），wL=weight_loaded (shadow 满), aA=activ_available。"""
    AR, AC = ctrl.AR, ctrl.AC
    NCYC = len(drives)

    rows = []
    for cy, drive in enumerate(drives):
        expect = _snapshot(ctrl)
        rows.append(_row_values(cy, drive, expect))
        start, wL, aA, sw, tag, tn, sp = drive
        ctrl.tick(start=start, weight_loaded=wL, activ_available=aA, F=F,
                  switch_weight=sw, tag=tag, tile_num=tn, switch_page=sp)

    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w") as fh:
        fh.write(f"# controller_ws cosim golden  AR={AR} AC={AC} F={F} "
                 f"LATENCY={ctrl.LATENCY} K_ABUF_MAX={ctrl.K_ABUF_MAX} "
                 f"TILE_NUM_MAX={ctrl.TILE_NUM_MAX} NCYC={NCYC}\n")
        fh.write(f"# {desc}\n")
        fh.write("# state 编码: IDLE=0 WLOAD=1 FEED=2 CAPTURE=3 OVERLAP=4 DRAIN=5\n")
        fh.write("# row N: drive[N] = cy=N 输入，expect[N] = cy=N 应读到的 RTL 输出 (寄存器值)\n")
        fh.write("#\n")

        header = ["cy", "stt", "wL", "aA", "sw", "tag", "tn", "sp",
                  "st", "fd"]
        header += [f"bs{c}" for c in range(AR)]
        header += ["wrR", "wrT", "wrV"]
        header += [f"re{c}" for c in range(AR)]
        header += [f"ra{c}" for c in range(AR)]
        fh.write("# " + " ".join(f"{p:>4}" for p in header) + "\n")

        fh.write(f"{AR} {AC} {F} {NCYC}\n")
        for row in rows:
            fh.write("  " + " ".join(f"{v:>4d}" for v in row) + "\n")

    return rows


# ---------------------------------------------------------------------- #
# Scenarios
# ---------------------------------------------------------------------- #
def _root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_ctrl_ws_single_dump():
    """单 tile：IDLE → WLOAD → FEED(W=5 拍) → DRAIN(5 拍) → IDLE。
       AR=2, AC=2, LATENCY=2 → W=AR+L+1=5；F=5 (=W) → CAPTURE 长度 0 跳过。
       start 在 cy=1 给单拍上升沿触发 IDLE→WLOAD。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, TILE_NUM_MAX=2)
    F = 5
    # (start, wL, aA, sw, tag, tn, sp)
    drives = [
        (0, 0, 0, 0, 0, 1, 0),   # cy  0  IDLE
        (1, 0, 1, 0, 0, 1, 0),   # cy  1  start 0→1 上升沿 → IDLE→WLOAD
        (0, 0, 1, 0, 0, 1, 0),   # cy  2  WLOAD 等 wL
        (0, 1, 1, 0, 0, 1, 0),   # cy  3  WLOAD→FEED (cold inject)
        (0, 1, 1, 0, 0, 1, 0),   # cy  4  FEED cnt=0
        (0, 1, 1, 0, 0, 1, 0),   # cy  5  FEED cnt=1
        (0, 1, 1, 0, 0, 1, 0),   # cy  6  FEED cnt=2
        (0, 1, 1, 0, 0, 1, 0),   # cy  7  FEED cnt=3
        (0, 1, 1, 0, 0, 1, 0),   # cy  8  FEED cnt=W-1=4 sw=0 → DRAIN（F==W 跳过 CAPTURE）
        (0, 0, 0, 0, 0, 1, 0),   # cy  9  DRAIN cnt=0
        (0, 0, 0, 0, 0, 1, 0),   # cy 10  DRAIN cnt=1
        (0, 0, 0, 0, 0, 1, 0),   # cy 11  DRAIN cnt=2
        (0, 0, 0, 0, 0, 1, 0),   # cy 12  DRAIN cnt=3
        (0, 0, 0, 0, 0, 1, 0),   # cy 13  DRAIN cnt=W-1=4 → IDLE
        (0, 0, 0, 0, 0, 1, 0),   # cy 14  IDLE
        (0, 0, 0, 0, 0, 1, 0),   # cy 15  IDLE (盖住最后一拍 commit 的可见效果)
    ]
    _dump_ctrl_ws(ctrl, F, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "single.txt"),
                  desc="scenario: single tile, F==W (no CAPTURE), FEED→DRAIN")


def test_ctrl_ws_switch_dump():
    """2 个 N-tile via switch_weight：第一段 FEED 末拍 sw=1 + tag 切到 1 → 进 OVERLAP 跑第二段。
       AR=2, AC=2, LATENCY=2 → W=5；F=5 (=W) → CAPTURE 跳过，
       FEED→OVERLAP(tile 0 drain + tile 1 warmup)→DRAIN(tile 1 drain)。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, TILE_NUM_MAX=2)
    F = 5
    # (start, wL, aA, sw, tag, tn, sp)
    drives = [
        (0, 0, 0, 0, 0, 1, 0),   # cy  0  IDLE
        (1, 0, 1, 0, 0, 1, 0),   # cy  1  start 0→1 → IDLE→WLOAD
        (0, 0, 1, 0, 0, 1, 0),   # cy  2  WLOAD 等 wL
        (0, 1, 1, 0, 0, 1, 0),   # cy  3  WLOAD→FEED tag=0 (cold inject)
        (0, 1, 1, 0, 0, 1, 0),   # cy  4  FEED cnt=0
        (0, 1, 1, 0, 0, 1, 0),   # cy  5  FEED cnt=1
        (0, 1, 1, 0, 0, 1, 0),   # cy  6  FEED cnt=2
        (0, 1, 1, 0, 0, 1, 0),   # cy  7  FEED cnt=3
        (0, 1, 1, 1, 1, 1, 0),   # cy  8  FEED cnt=W-1=4 sw=1 tag=1 → OVERLAP (boundary inject)
        (0, 1, 1, 0, 1, 1, 0),   # cy  9  OVERLAP cnt=0
        (0, 1, 1, 0, 1, 1, 0),   # cy 10  OVERLAP cnt=1
        (0, 1, 1, 0, 1, 1, 0),   # cy 11  OVERLAP cnt=2
        (0, 1, 1, 0, 1, 1, 0),   # cy 12  OVERLAP cnt=3
        (0, 1, 1, 0, 1, 1, 0),   # cy 13  OVERLAP cnt=W-1=4 sw=0 → DRAIN (F==W 跳 CAPTURE)
        (0, 0, 0, 0, 0, 1, 0),   # cy 14  DRAIN cnt=0
        (0, 0, 0, 0, 0, 1, 0),   # cy 15  DRAIN cnt=1
        (0, 0, 0, 0, 0, 1, 0),   # cy 16  DRAIN cnt=2
        (0, 0, 0, 0, 0, 1, 0),   # cy 17  DRAIN cnt=3
        (0, 0, 0, 0, 0, 1, 0),   # cy 18  DRAIN cnt=W-1=4 → IDLE
        (0, 0, 0, 0, 0, 1, 0),   # cy 19  IDLE (盖住最后一拍 commit)
    ]
    _dump_ctrl_ws(ctrl, F, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "switch.txt"),
                  desc="scenario: 2 N-tiles via switch_weight, FEED→OVERLAP→DRAIN, tag 0→1")