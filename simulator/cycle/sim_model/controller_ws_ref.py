"""ControllerWSRef — 逐位镜像 rtl/controller_ws.sv 的 Python 参考模型，专给 cosim 用。

接口与 RTL 一一对应（doc/controller_ws.md §1）：
  指令字段 (指令内常量)：
    i_wtile_num   — 跑几个 weight tile (≥1)
    i_act_staddr  — abuf 读起点（所有 wtile 共用）
    i_acc_staddr  — accumulator 写起点（首 wtile）
    i_feed_num    — 每 wtile 读多少行 activation = 写多少行 psum (≥W)
  流控：
    i_start / i_weight_loaded / i_activ_available

7 态 FSM（doc/controller_ws.md §3.3）：
  IDLE / WLOAD / FEED / CAPTURE / OVERLAP / REWAIT / DRAIN
  REWAIT 出口双分支（变体 II 早退）：
    cnt<W & weight_loaded ↑ → OVERLAP (boundary inject)
    cnt≥W & weight_loaded ↑ → FEED    (cold inject)

输出：
  o_weight_sw[AR]                          行 stagger SR
  o_act_ren[AR] / o_act_raddr[AR]          per-lane abuf 读
  o_acc_wen[AC] / o_acc_waddr[AC] /        per-column 已 deskew accum 写
  o_acc_accen[AC] / o_acc_outen[AC]
  o_acc_ren[AC]=0 / o_acc_raddr[AC]=0      占位（STORE 类指令再用）
  o_ws_state                               观察
"""

from enum import IntEnum


class WSStateRef(IntEnum):
    IDLE    = 0
    WLOAD   = 1
    FEED    = 2     # warmup, feed only, no psum
    CAPTURE = 3     # feed + psum out
    OVERLAP = 4     # 旧 wtile drain + 新 wtile warmup-feed (部分重叠)
    REWAIT  = 5     # 旧 drain 顺势走 + 等下一份 weight
    DRAIN   = 6     # 末 wtile 收尾


class ControllerWSRef:
    """硬件常量（compile-time）：AR, AC, LATENCY, WTILE_NUM_MAX, ACT_ADDR_W, ACC_ADDR_W
    运行时输入（per-update）：wtile_num, act_staddr, acc_staddr, feed_num
    """
    def __init__(self, AR=8, AC=8, LATENCY=2, WTILE_NUM_MAX=4,
                 ACT_ADDR_W=8, ACC_ADDR_W=10):
        self.AR            = AR
        self.AC            = AC
        self.LATENCY       = LATENCY
        self.WTILE_NUM_MAX = WTILE_NUM_MAX
        self.ACT_ADDR_W    = ACT_ADDR_W
        self.ACC_ADDR_W    = ACC_ADDR_W
        self.W             = AR + LATENCY                  # warmup / drain 拍数
        self.reset()

    # ------------------------------------------------------------------ #
    # reset
    # ------------------------------------------------------------------ #
    def reset(self):
        AR, AC = self.AR, self.AC
        # FSM 寄存器
        self.ws_state    = WSStateRef.IDLE
        self.ws_cnt      = 0
        self.start_prev  = False
        self.wtile_idx   = 0
        self.wr_row      = 0
        self.offset      = [0] * AR
        # 输出寄存器
        self.weight_sw   = [False] * AR
        self.act_ren     = [False] * AR
        self.act_raddr   = [0]     * AR
        self.acc_wen     = [False] * AC
        self.acc_waddr   = [0]     * AC
        self.acc_outen   = [False] * AC
        self.ws_state_out = WSStateRef.IDLE
        # next-state 槽位
        self._reset_next()

    def _reset_next(self):
        AR, AC = self.AR, self.AC
        self._ws_state_next   = WSStateRef.IDLE
        self._ws_cnt_next     = 0
        self._start_prev_next = False
        self._wtile_idx_next  = 0
        self._wr_row_next     = 0
        self._offset_next     = [0] * AR
        self._weight_sw_next  = [False] * AR
        self._act_ren_next    = [False] * AR
        self._act_raddr_next  = [0]     * AR
        self._acc_wen_next    = [False] * AC
        self._acc_waddr_next  = [0]     * AC
        self._acc_outen_next  = [False] * AC

    # ------------------------------------------------------------------ #
    # update — 计算 next（纯组合）
    # ------------------------------------------------------------------ #
    def update(self, start, weight_loaded, activ_available,
               wtile_num=1, act_staddr=0, acc_staddr=0, feed_num=None):
        if feed_num is None:
            feed_num = self.W
        assert 1 <= int(wtile_num) <= self.WTILE_NUM_MAX, \
            f"wtile_num={wtile_num} out of [1, WTILE_NUM_MAX={self.WTILE_NUM_MAX}]"
        assert int(feed_num) >= self.W, \
            f"feed_num={feed_num} must be ≥ W={self.W}"
        wtn = int(wtile_num)
        fn  = int(feed_num)
        sa  = int(act_staddr)
        ca  = int(acc_staddr)
        start_edge = bool(start) and not self.start_prev
        wl = bool(weight_loaded)
        aa = bool(activ_available)

        ws_state_next, ws_cnt_next, wtile_idx_next = \
            self._fsm_next(start_edge, wl, aa, wtn, fn)

        # inject / offset_rst / out_feed_next 派生
        cold = ((self.ws_state == WSStateRef.WLOAD)  and (ws_state_next == WSStateRef.FEED)) \
            or ((self.ws_state == WSStateRef.REWAIT) and (ws_state_next == WSStateRef.FEED))
        # boundary fires only on TRANSITION to OVERLAP (not throughout OVERLAP):
        #   FEED/CAP/REW → OVL: state changes, fires once
        #   OVL → OVL (Case E inter-wtile): same state but cnt==W-1 (about to wrap to new OVL)
        boundary = (ws_state_next == WSStateRef.OVERLAP) and (
            (self.ws_state in (WSStateRef.FEED, WSStateRef.CAPTURE, WSStateRef.REWAIT)) or
            (self.ws_state == WSStateRef.OVERLAP and self.ws_cnt == self.W - 1)
        )
        inject = cold or boundary
        # offset_rst 只在 REWAIT 退出时拉起 (REWAIT 期间 lane 停 fire, offset 冻结在
        # 非 0 值, 必须强 reset; 其他过渡靠自然 wrap 即可，强 reset 会破坏 row stagger)
        offset_rst = (self.ws_state == WSStateRef.REWAIT) and \
                     (ws_state_next in (WSStateRef.OVERLAP, WSStateRef.FEED))
        out_feed_next = ws_state_next in (WSStateRef.FEED, WSStateRef.CAPTURE,
                                          WSStateRef.OVERLAP)

        # weight_sw SR：左边缘注入，行 stagger
        weight_sw_next = [False] * self.AR
        weight_sw_next[0] = inject
        for r in range(1, self.AR):
            weight_sw_next[r] = self.weight_sw[r-1]

        # lane_fire[c]: lane 0 用 out_feed_next；c≥1 用 当前 act_ren[c-1] FF
        lane_fire = [False] * self.AR
        lane_fire[0] = out_feed_next
        for c in range(1, self.AR):
            lane_fire[c] = self.act_ren[c-1]

        # act_ren SR + offset 直 mux
        act_ren_next   = [False] * self.AR
        act_raddr_next = [0]     * self.AR
        offset_next    = [0]     * self.AR
        for c in range(self.AR):
            act_ren_next[c] = lane_fire[c]
            raddr_d = 0 if offset_rst else self.offset[c]
            act_raddr_next[c] = sa + raddr_d
            if offset_rst and lane_fire[c]:
                offset_next[c] = 1
            elif offset_rst:
                offset_next[c] = 0
            elif lane_fire[c]:
                offset_next[c] = 0 if self.offset[c] == fn - 1 else self.offset[c] + 1
            else:
                offset_next[c] = self.offset[c]

        # wr_row 闸 + accum 写地址
        wr_row_active = self.ws_state in (WSStateRef.CAPTURE, WSStateRef.OVERLAP,
                                          WSStateRef.REWAIT, WSStateRef.DRAIN)
        if wtile_idx_next != self.wtile_idx:
            wr_row_next = 0
        elif wr_row_active and self.wr_row < fn:
            wr_row_next = self.wr_row + 1
        else:
            wr_row_next = self.wr_row

        wr_vld_scalar  = wr_row_active and (self.wr_row < fn)
        tile_acc_base  = ca + self.wtile_idx * fn
        acc_addr_scalar = tile_acc_base + self.wr_row

        # per-column deskew SR：col 0 直用标量，c≥1 用 当前 FF state
        acc_wen_next   = [False] * self.AC
        acc_waddr_next = [0]     * self.AC
        acc_outen_next = [False] * self.AC
        is_last_wtile = (self.wtile_idx == wtn - 1)
        acc_wen_next[0]   = wr_vld_scalar
        acc_waddr_next[0] = acc_addr_scalar
        acc_outen_next[0] = wr_vld_scalar and is_last_wtile
        for c in range(1, self.AC):
            acc_wen_next[c]   = self.acc_wen[c-1]
            acc_waddr_next[c] = self.acc_waddr[c-1]
            acc_outen_next[c] = self.acc_outen[c-1]

        # 落 next 槽位
        self._ws_state_next   = ws_state_next
        self._ws_cnt_next     = ws_cnt_next
        self._start_prev_next = bool(start)
        self._wtile_idx_next  = wtile_idx_next
        self._wr_row_next     = wr_row_next
        self._offset_next     = offset_next
        self._weight_sw_next  = weight_sw_next
        self._act_ren_next    = act_ren_next
        self._act_raddr_next  = act_raddr_next
        self._acc_wen_next    = acc_wen_next
        self._acc_waddr_next  = acc_waddr_next
        self._acc_outen_next  = acc_outen_next

    # ------------------------------------------------------------------ #
    # _fsm_next — 7-state FSM 转移
    # ------------------------------------------------------------------ #
    def _fsm_next(self, start_edge, weight_loaded, activ_available, wtile_num, feed_num):
        W = self.W
        cs = self.ws_state
        ws_state_next  = cs
        ws_cnt_next    = self.ws_cnt + 1
        wtile_idx_next = self.wtile_idx

        feed_last    = self.ws_cnt == W - 1
        cap_last     = self.ws_cnt == feed_num - W - 1
        ovl_last     = self.ws_cnt == W - 1
        drain_last   = self.ws_cnt == W - 1
        rew_drain_done = self.ws_cnt >= W
        is_last_wtile      = self.wtile_idx == wtile_num - 1
        is_last_wtile_next = self.wtile_idx == wtile_num - 2

        if cs == WSStateRef.IDLE:
            ws_cnt_next = 0
            if start_edge:
                ws_state_next  = WSStateRef.WLOAD
                wtile_idx_next = 0
        elif cs == WSStateRef.WLOAD:
            if weight_loaded and activ_available:
                ws_state_next  = WSStateRef.FEED
                ws_cnt_next    = 0
                wtile_idx_next = 0
        elif cs == WSStateRef.FEED:
            if feed_last:
                ws_cnt_next = 0
                if feed_num > W:
                    ws_state_next = WSStateRef.CAPTURE
                elif is_last_wtile:
                    ws_state_next = WSStateRef.DRAIN
                elif weight_loaded:
                    ws_state_next = WSStateRef.OVERLAP
                else:
                    ws_state_next = WSStateRef.REWAIT
        elif cs == WSStateRef.CAPTURE:
            if cap_last:
                ws_cnt_next = 0
                if is_last_wtile:
                    ws_state_next = WSStateRef.DRAIN
                elif weight_loaded:
                    ws_state_next = WSStateRef.OVERLAP
                else:
                    ws_state_next = WSStateRef.REWAIT
        elif cs == WSStateRef.OVERLAP:
            if ovl_last:
                ws_cnt_next    = 0
                wtile_idx_next = self.wtile_idx + 1
                if feed_num > W:
                    ws_state_next = WSStateRef.CAPTURE
                elif is_last_wtile_next:
                    ws_state_next = WSStateRef.DRAIN
                elif weight_loaded:
                    ws_state_next = WSStateRef.OVERLAP
                else:
                    ws_state_next = WSStateRef.REWAIT
        elif cs == WSStateRef.REWAIT:
            if weight_loaded:
                ws_cnt_next = 0
                if rew_drain_done:
                    ws_state_next  = WSStateRef.FEED
                    wtile_idx_next = self.wtile_idx + 1
                else:
                    ws_state_next = WSStateRef.OVERLAP
            else:
                # saturate cnt at W to avoid rew_drain_done bouncing
                ws_cnt_next = self.ws_cnt if rew_drain_done else self.ws_cnt + 1
        elif cs == WSStateRef.DRAIN:
            if drain_last:
                ws_state_next = WSStateRef.IDLE
                ws_cnt_next   = 0

        return ws_state_next, ws_cnt_next, wtile_idx_next

    # ------------------------------------------------------------------ #
    # commit — 锁存 next（纯时序）
    # ------------------------------------------------------------------ #
    def commit(self):
        self.ws_state    = self._ws_state_next
        self.ws_cnt      = self._ws_cnt_next
        self.start_prev  = self._start_prev_next
        self.wtile_idx   = self._wtile_idx_next
        self.wr_row      = self._wr_row_next
        self.offset      = list(self._offset_next)
        self.weight_sw   = list(self._weight_sw_next)
        self.act_ren     = list(self._act_ren_next)
        self.act_raddr   = list(self._act_raddr_next)
        self.acc_wen     = list(self._acc_wen_next)
        self.acc_waddr   = list(self._acc_waddr_next)
        self.acc_outen   = list(self._acc_outen_next)
        self.ws_state_out = self._ws_state_next

    # ------------------------------------------------------------------ #
    # 便捷：一拍完成（update + commit）
    # ------------------------------------------------------------------ #
    def tick(self, start, weight_loaded, activ_available,
             wtile_num=1, act_staddr=0, acc_staddr=0, feed_num=None):
        self.update(start, weight_loaded, activ_available,
                    wtile_num=wtile_num, act_staddr=act_staddr,
                    acc_staddr=acc_staddr, feed_num=feed_num)
        self.commit()
