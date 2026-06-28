"""controller_ws cosim 向量 dump。

跑 ControllerWSRef，逐拍 dump 输入 + 期望输出到 build/ctrl_ws_cosim/*.txt，
tb/ctrl_ws_tb 用同一份 .txt 喂 RTL 比对。

每行：cy=N 拍 TB 要驱动的输入 + 该拍 RTL 应该输出的值（= ref pre-tick 状态 = 上一拍 commit 后的寄存器值）。

数据行字段（与 rtl/controller_ws.sv 端口对齐）：
  cy  start wL aA  wtn ac sa fn   state  weight_sw[AR]  act_ren[AR] act_raddr[AR]  acc_wen[AC] acc_waddr[AC] acc_outen[AC]

  start = 启动脉冲（IDLE 边沿触发 IDLE→WLOAD）
  wL    = i_weight_loaded（!|wfifo_rdy）
  aA    = i_activ_available
  wtn   = i_wtile_num     — 跑几个 weight tile (≥1)
  ac    = i_acc_staddr    — accumulator 写起点
  sa    = i_act_staddr    — abuf 读起点（所有 wtile 共用）
  fn    = i_feed_num      — 每 wtile feed 行数 (≥W)

  state: IDLE=0 WLOAD=1 FEED=2 CAPTURE=3 OVERLAP=4 REWAIT=5 DRAIN=6
"""

import os
from simulator.cycle.sim_model.controller_ws_ref import ControllerWSRef


def _snapshot(ctrl):
    return {
        "state":      int(ctrl.ws_state),
        "weight_sw":  [int(x) for x in ctrl.weight_sw],
        "act_ren":    [int(x) for x in ctrl.act_ren],
        "act_raddr":  [int(x) for x in ctrl.act_raddr],
        "acc_wen":    [int(x) for x in ctrl.acc_wen],
        "acc_waddr":  [int(x) for x in ctrl.acc_waddr],
        "acc_outen":  [int(x) for x in ctrl.acc_outen],
    }


def _row_values(cy, drive, expect):
    start, wL, aA, wtn, ac, sa, fn = drive
    vals = [cy, start, wL, aA, wtn, ac, sa, fn, expect["state"]]
    vals += expect["weight_sw"]
    vals += expect["act_ren"]
    vals += expect["act_raddr"]
    vals += expect["acc_wen"]
    vals += expect["acc_waddr"]
    vals += expect["acc_outen"]
    return vals


def _dump_ctrl_ws(ctrl, drives, dump_path, desc):
    """drives: list of (start, wL, aA, wtn, ac, sa, fn) tuples"""
    AR, AC = ctrl.AR, ctrl.AC
    NCYC = len(drives)

    rows = []
    for cy, drive in enumerate(drives):
        expect = _snapshot(ctrl)
        rows.append(_row_values(cy, drive, expect))
        start, wL, aA, wtn, ac, sa, fn = drive
        ctrl.tick(start=start, weight_loaded=wL, activ_available=aA,
                  wtile_num=wtn, act_staddr=sa, acc_staddr=ac, feed_num=fn)

    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w") as fh:
        fh.write(f"# controller_ws cosim golden  AR={AR} AC={AC} W={ctrl.W} "
                 f"LATENCY={ctrl.LATENCY} WTILE_NUM_MAX={ctrl.WTILE_NUM_MAX} NCYC={NCYC}\n")
        fh.write(f"# {desc}\n")
        fh.write("# state: IDLE=0 WLOAD=1 FEED=2 CAPTURE=3 OVERLAP=4 REWAIT=5 DRAIN=6\n")
        fh.write("# row N: drive[N] = cy=N 输入，expect[N] = cy=N 应读到的 RTL 输出 (寄存器值)\n")
        fh.write("#\n")

        header = ["cy", "stt", "wL", "aA", "wtn", "ac", "sa", "fn", "st"]
        header += [f"sw{c}" for c in range(AR)]
        header += [f"ar{c}" for c in range(AR)]
        header += [f"ad{c}" for c in range(AR)]
        header += [f"aw{c}" for c in range(AC)]
        header += [f"wa{c}" for c in range(AC)]
        header += [f"ao{c}" for c in range(AC)]
        fh.write("# " + " ".join(f"{p:>4}" for p in header) + "\n")

        fh.write(f"{AR} {AC} {NCYC}\n")
        for row in rows:
            fh.write("  " + " ".join(f"{v:>4d}" for v in row) + "\n")

    return rows


# ---------------------------------------------------------------------- #
# Scenarios
# ---------------------------------------------------------------------- #
def _root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_ctrl_ws_single_dump():
    """单 wtile, feed_num=W (Case A): IDLE → WLOAD → FEED(W) → DRAIN(W) → IDLE。
    AR=2, LATENCY=2 → W=4; wtile_num=1, feed_num=4 → 跳过 CAPTURE。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=2,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    # (start, wL, aA, wtn, ac, sa, fn)
    drives = [
        (0, 0, 0, 1, 0, 0, 4),   # cy 0  IDLE
        (1, 0, 1, 1, 0, 0, 4),   # cy 1  start ↑
        (0, 0, 1, 1, 0, 0, 4),   # cy 2  WLOAD 等 wL
        (0, 1, 1, 1, 0, 0, 4),   # cy 3  WLOAD wL=1 → FEED (cold inject)
        (0, 1, 1, 1, 0, 0, 4),   # cy 4  FEED cnt=0
        (0, 1, 1, 1, 0, 0, 4),   # cy 5  FEED cnt=1
        (0, 1, 1, 1, 0, 0, 4),   # cy 6  FEED cnt=2
        (0, 1, 1, 1, 0, 0, 4),   # cy 7  FEED cnt=W-1=3 last_wtile → DRAIN
        (0, 0, 0, 1, 0, 0, 4),   # cy 8  DRAIN cnt=0
        (0, 0, 0, 1, 0, 0, 4),   # cy 9  DRAIN cnt=1
        (0, 0, 0, 1, 0, 0, 4),   # cy 10 DRAIN cnt=2
        (0, 0, 0, 1, 0, 0, 4),   # cy 11 DRAIN cnt=W-1=3 → IDLE
        (0, 0, 0, 1, 0, 0, 4),   # cy 12 IDLE
        (0, 0, 0, 1, 0, 0, 4),   # cy 13 IDLE
        (0, 0, 0, 1, 0, 0, 4),   # cy 14 IDLE (盖住最后 commit)
    ]
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "single.txt"),
                  desc="Case A: wtile_num=1 feed_num=W=4, FEED→DRAIN (no CAPTURE)")


def test_ctrl_ws_switch_dump():
    """2 wtile, feed_num=W (Case C): FEED → OVERLAP → DRAIN。每个 wtile 写到不同 acc 区段。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=2,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    # (start, wL, aA, wtn, ac, sa, fn)
    drives = [
        (0, 0, 0, 2, 0, 0, 4),   # cy 0  IDLE
        (1, 0, 1, 2, 0, 0, 4),   # cy 1  start ↑
        (0, 0, 1, 2, 0, 0, 4),   # cy 2  WLOAD
        (0, 1, 1, 2, 0, 0, 4),   # cy 3  WLOAD → FEED (cold)
        (0, 1, 1, 2, 0, 0, 4),   # cy 4  FEED cnt=0
        (0, 1, 1, 2, 0, 0, 4),   # cy 5  FEED cnt=1
        (0, 1, 1, 2, 0, 0, 4),   # cy 6  FEED cnt=2
        (0, 1, 1, 2, 0, 0, 4),   # cy 7  FEED cnt=3 wL=1 !last → OVERLAP (boundary)
        (0, 1, 1, 2, 0, 0, 4),   # cy 8  OVERLAP cnt=0
        (0, 1, 1, 2, 0, 0, 4),   # cy 9  OVERLAP cnt=1
        (0, 1, 1, 2, 0, 0, 4),   # cy 10 OVERLAP cnt=2
        (0, 1, 1, 2, 0, 0, 4),   # cy 11 OVERLAP cnt=3 idx→1 last_next → DRAIN
        (0, 0, 0, 2, 0, 0, 4),   # cy 12 DRAIN cnt=0
        (0, 0, 0, 2, 0, 0, 4),   # cy 13 DRAIN cnt=1
        (0, 0, 0, 2, 0, 0, 4),   # cy 14 DRAIN cnt=2
        (0, 0, 0, 2, 0, 0, 4),   # cy 15 DRAIN cnt=3 → IDLE
        (0, 0, 0, 2, 0, 0, 4),   # cy 16 IDLE
        (0, 0, 0, 2, 0, 0, 4),   # cy 17 IDLE
        (0, 0, 0, 2, 0, 0, 4),   # cy 18 IDLE
    ]
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "switch.txt"),
                  desc="Case C: wtile_num=2 feed_num=W=4, FEED→OVERLAP→DRAIN")


def test_ctrl_ws_multi_tile_dump():
    """单 wtile, feed_num=2W (Case B): FEED(W) → CAPTURE(W) → DRAIN(W)。
    AR=2, LATENCY=2 → W=4; wtile_num=1, feed_num=8 → cap_last=3 (4 拍 CAPTURE)。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=2,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    # (start, wL, aA, wtn, ac, sa, fn)
    drives = [
        (0, 0, 0, 1, 0, 0, 8),   # cy 0  IDLE
        (1, 0, 1, 1, 0, 0, 8),   # cy 1  start ↑
        (0, 0, 1, 1, 0, 0, 8),   # cy 2  WLOAD
        (0, 1, 1, 1, 0, 0, 8),   # cy 3  WLOAD → FEED (cold)
        (0, 1, 1, 1, 0, 0, 8),   # cy 4  FEED cnt=0
        (0, 1, 1, 1, 0, 0, 8),   # cy 5  FEED cnt=1
        (0, 1, 1, 1, 0, 0, 8),   # cy 6  FEED cnt=2
        (0, 1, 1, 1, 0, 0, 8),   # cy 7  FEED cnt=3 → CAPTURE (feed_num>W)
        (0, 1, 1, 1, 0, 0, 8),   # cy 8  CAPTURE cnt=0
        (0, 1, 1, 1, 0, 0, 8),   # cy 9  CAPTURE cnt=1
        (0, 1, 1, 1, 0, 0, 8),   # cy 10 CAPTURE cnt=2
        (0, 1, 1, 1, 0, 0, 8),   # cy 11 CAPTURE cnt=cap_last=3 last_wtile → DRAIN
        (0, 0, 0, 1, 0, 0, 8),   # cy 12 DRAIN cnt=0
        (0, 0, 0, 1, 0, 0, 8),   # cy 13 DRAIN cnt=1
        (0, 0, 0, 1, 0, 0, 8),   # cy 14 DRAIN cnt=2
        (0, 0, 0, 1, 0, 0, 8),   # cy 15 DRAIN cnt=3 → IDLE
        (0, 0, 0, 1, 0, 0, 8),   # cy 16 IDLE
        (0, 0, 0, 1, 0, 0, 8),   # cy 17 IDLE
        (0, 0, 0, 1, 0, 0, 8),   # cy 18 IDLE
    ]
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "multi.txt"),
                  desc="Case B: wtile_num=1 feed_num=8 (2W), FEED→CAPTURE→DRAIN")


def test_ctrl_ws_capture_switch_dump():
    """2 wtile, feed_num=2W (Case D): FEED→CAPTURE→OVERLAP→CAPTURE→DRAIN。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=4,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    drives = [
        (0, 0, 0, 2, 0, 0, 8),   # cy 0  IDLE
        (1, 0, 1, 2, 0, 0, 8),   # cy 1  start ↑
        (0, 0, 1, 2, 0, 0, 8),   # cy 2  WLOAD
        (0, 1, 1, 2, 0, 0, 8),   # cy 3  WLOAD → FEED
        (0, 1, 1, 2, 0, 0, 8),   # cy 4  FEED 0
        (0, 1, 1, 2, 0, 0, 8),   # cy 5  FEED 1
        (0, 1, 1, 2, 0, 0, 8),   # cy 6  FEED 2
        (0, 1, 1, 2, 0, 0, 8),   # cy 7  FEED 3 → CAPTURE
        (0, 1, 1, 2, 0, 0, 8),   # cy 8  CAP 0
        (0, 1, 1, 2, 0, 0, 8),   # cy 9  CAP 1
        (0, 1, 1, 2, 0, 0, 8),   # cy 10 CAP 2
        (0, 1, 1, 2, 0, 0, 8),   # cy 11 CAP 3 → OVERLAP
        (0, 1, 1, 2, 0, 0, 8),   # cy 12 OVL 0
        (0, 1, 1, 2, 0, 0, 8),   # cy 13 OVL 1
        (0, 1, 1, 2, 0, 0, 8),   # cy 14 OVL 2
        (0, 1, 1, 2, 0, 0, 8),   # cy 15 OVL 3 idx→1 → CAPTURE (feed_num>W)
        (0, 1, 1, 2, 0, 0, 8),   # cy 16 CAP_new 0
        (0, 1, 1, 2, 0, 0, 8),   # cy 17 CAP_new 1
        (0, 1, 1, 2, 0, 0, 8),   # cy 18 CAP_new 2
        (0, 1, 1, 2, 0, 0, 8),   # cy 19 CAP_new 3 last → DRAIN
        (0, 0, 0, 2, 0, 0, 8),   # cy 20 DRAIN 0
        (0, 0, 0, 2, 0, 0, 8),   # cy 21 DRAIN 1
        (0, 0, 0, 2, 0, 0, 8),   # cy 22 DRAIN 2
        (0, 0, 0, 2, 0, 0, 8),   # cy 23 DRAIN 3 → IDLE
        (0, 0, 0, 2, 0, 0, 8),   # cy 24 IDLE
        (0, 0, 0, 2, 0, 0, 8),   # cy 25 IDLE
        (0, 0, 0, 2, 0, 0, 8),   # cy 26 IDLE
    ]
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "capture_switch.txt"),
                  desc="Case D: wtile_num=2 feed_num=8 (2W), FEED→CAP→OVL→CAP→DRAIN")


def test_ctrl_ws_rewait_early_dump():
    """2 wtile, feed_num=2W, weight 慢 (X=2, REWAIT→OVERLAP 早退路径)。
    CAP 末拍 wL=0 进 REWAIT，3 拍后 wL=1 → cnt=2<W=4 → OVERLAP。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=4,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    drives = (
        [(0, 0, 0, 2, 0, 0, 8)]           # cy 0  IDLE
        + [(1, 0, 1, 2, 0, 0, 8)]         # cy 1  start
        + [(0, 0, 1, 2, 0, 0, 8)]         # cy 2  WLOAD
        + [(0, 1, 1, 2, 0, 0, 8)] * 8     # cy 3..10  WLOAD→FEED→CAPTURE (wL=1 全程)
        + [(0, 0, 1, 2, 0, 0, 8)] * 3     # cy 11..13 CAP last 那拍 wL=0 → REWAIT cnt=0..2
        + [(0, 1, 1, 2, 0, 0, 8)] * 13    # cy 14 wL=1 cnt=2<W → OVERLAP; 后续 CAP_new→DRAIN
        + [(0, 0, 0, 2, 0, 0, 8)] * 3     # IDLE 收尾
    )
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "rewait_early.txt"),
                  desc="REWAIT early-exit (X<W): CAP→REWAIT(2)→OVERLAP→CAP→DRAIN")


def test_ctrl_ws_rewait_late_dump():
    """2 wtile, feed_num=2W, weight 长时间不来 (X=6, REWAIT→FEED cold restart)。
    CAP 末拍 wL=0 进 REWAIT，7 拍后 wL=1 → cnt=4(saturated)≥W=4 → FEED cold restart。"""
    ctrl = ControllerWSRef(AR=2, AC=2, LATENCY=2, WTILE_NUM_MAX=4,
                           ACT_ADDR_W=4, ACC_ADDR_W=6)
    drives = (
        [(0, 0, 0, 2, 0, 0, 8)]
        + [(1, 0, 1, 2, 0, 0, 8)]
        + [(0, 0, 1, 2, 0, 0, 8)]
        + [(0, 1, 1, 2, 0, 0, 8)] * 8
        + [(0, 0, 1, 2, 0, 0, 8)] * 7     # cy 11..17  CAP last → REWAIT, 7 拍 wL=0
        + [(0, 1, 1, 2, 0, 0, 8)] * 13    # cy 18 wL=1 cnt≥W → FEED cold; 后续 CAP→DRAIN
        + [(0, 0, 0, 2, 0, 0, 8)] * 3
    )
    _dump_ctrl_ws(ctrl, drives,
                  dump_path=os.path.join(_root(), "build", "ctrl_ws_cosim", "rewait_late.txt"),
                  desc="REWAIT late-exit (X>=W): CAP→REWAIT(7)→FEED→CAP→DRAIN cold restart")
