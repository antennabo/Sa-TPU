"""ControllerWSRef — 逐位镜像 rtl/controller_ws.sv 的 Python 参考模型，专给 cosim 用。

跟 controller.py / commonbuf.py 的关系：
  - controller.py 走 functional 链路，跟 commonbuf 配合；这里【不】共用
  - 这个 ref 把 RTL 的状态机 + abuf 寻址一口气吃进来，输出跟 RTL signal 一对一
  - 互不干扰：functional 测试链路保持原样

状态机：6 个 state，feed 跟 capture 通过状态显式切分（不用 SR 错位）
  IDLE  → start_edge          → WLOAD
  WLOAD → wL && aA            → FEED      (cold inject：b_sw 注入 + cur_tile 锁存)
  FEED  → cnt == W-1          → CAPTURE   feed=1 wr_vld=0（W 拍 warmup，psum 在流水）
  CAPTURE → cnt == F-W-1      → OVERLAP / DRAIN
                                           feed=1 wr_vld=1（feed 续 + psum 出来写 accum）
  OVERLAP → cnt == W-1        → CAPTURE   feed=1 wr_vld=1（下个 tile FEED + 当前 tile DRAIN 重合）
                                           cur_tile 这一拍从 pending_tile 更新
  DRAIN → cnt == W-1          → IDLE      feed=0 wr_vld=1（停 feed，排完最后 W 行）

W = AR + LATENCY（从 feed=1 到 wr_vld=1 出现，含 abuf 1 拍 + SA 流水 + 寄存边界）
F >= W + 1（CAPTURE 至少 1 拍）

wr_row 规则：
  CAPTURE:        wr_row = ws_cnt_next            (0..F-W-1)
  OVERLAP/DRAIN:  wr_row = (F-W) + ws_cnt_next    (F-W..F-1)

wr_tile 规则（cur_tile 跟随当前正被写 accum 的 tile）：
  cold (WLOAD→FEED):       cur_tile ← i_tag
  CAPTURE→OVERLAP boundary: pending_tile ← i_tag （新 tile 的 tag 先暂存）
  OVERLAP→CAPTURE:         cur_tile ← pending_tile （这拍切到新 tile capture）
"""

from enum import IntEnum


class WSStateRef(IntEnum):
    IDLE    = 0
    WLOAD   = 1
    FEED    = 2     # warmup：feed only，psum 还在流水里
    CAPTURE = 3     # feed 续 + capture（写 accum）
    OVERLAP = 4     # 下个 tile FEED + 当前 tile DRAIN（feed=1 + wr_vld=1，但 tile 不同）
    DRAIN   = 5     # 停 feed，排完最后 W 行 psum


class ControllerWSRef:
    """硬件常量（compile-time）：AR、AC、LATENCY、TILE_NUM_MAX、K_ABUF_MAX
    运行时输入（per-update）：F = 当前矩阵 M 维（激活行数），随作业变化
    """
    def __init__(self, AR=4, AC=2, LATENCY=2, TILE_NUM_MAX=4, K_ABUF_MAX=None):
        self.AR           = AR
        self.AC           = AC
        self.LATENCY      = LATENCY
        self.TILE_NUM_MAX = TILE_NUM_MAX
        # K_ABUF_MAX：abuf 每 tile 最大深度，默认 = TILE_NUM_MAX * 一个保守值；RTL 综合时确定
        # 这里用默认值跟 RTL 对齐（实际值取决于 PAGE_SPAN 计算需要）
        self.K_ABUF_MAX   = K_ABUF_MAX if K_ABUF_MAX is not None else 8
        self.PAGE_SPAN    = self.K_ABUF_MAX * TILE_NUM_MAX
        self.ABUF_DEPTH   = 2 * self.PAGE_SPAN
        # warmup 拍数 = FEED 状态长度 = AR + L + 1（abuf 1 + SA AR+L-1 + 寄存边界 1）
        self.W            = AR + LATENCY + 1
        self.reset()

    # ------------------------------------------------------------------ #
    # reset
    # ------------------------------------------------------------------ #
    def reset(self):
        AR = self.AR
        # 状态寄存器（commit 锁存）
        self.ws_state     = WSStateRef.IDLE
        self.ws_cnt       = 0
        self.switch_sr    = [False] * AR
        self.prop         = [False] * AR
        self.rd_ptr       = [0]     * AR
        self.page         = 0
        self.start_prev   = False
        self.cur_tile     = 0                    # 当前正在被 capture 的 tile（wr_tile 出口源）
        self.pending_tile = 0                    # 边界 inject 时暂存的新 tile tag
        # 输出寄存器
        self.feed         = False
        self.b_sw         = [False] * AR
        self.wr_row       = 0
        self.wr_tile      = 0
        self.wr_vld       = False
        self.rd_en        = [False] * AR
        self.rd_addr      = [0]     * AR
        # next-state 槽位
        self._reset_next()

    def _reset_next(self):
        AR = self.AR
        self._ws_state_next     = WSStateRef.IDLE
        self._ws_cnt_next       = 0
        self._switch_sr_next    = [False] * AR
        self._prop_next         = [False] * AR
        self._rd_ptr_next       = [0]     * AR
        self._page_next         = 0
        self._start_prev_next   = False
        self._cur_tile_next     = 0
        self._pending_tile_next = 0
        self._feed_next         = False
        self._b_sw_next         = [False] * AR
        self._wr_row_next       = 0
        self._wr_tile_next      = 0
        self._wr_vld_next       = False
        self._rd_en_next        = [False] * AR
        self._rd_addr_next      = [0]     * AR
        self._switch_page_in    = False

    # ------------------------------------------------------------------ #
    # update — 计算 next（纯组合）
    # 结构：update 只做参数校验 + 调度 4 个内部子函数 + 锁 next 槽位
    #   _fsm_next            : 6-state 状态机
    #   _switch_and_tile_next: cold/boundary inject → switch_sr/b_sw + cur/pending tile
    #   _drivers_next        : feed / wr_vld / wr_row / wr_tile（基于 state_next）
    #   _abuf_addr_next      : prop / rd_en / rd_addr / rd_ptr / page
    # ------------------------------------------------------------------ #
    def update(self, start, weight_loaded, activ_available, F,
               switch_weight=False, tag=0, tile_num=1, switch_page=False):
        # F：当前作业的 M 维（激活行数），运行时输入
        # 约束：W <= F <= K_ABUF_MAX（下限 = warmup，上限 = abuf 每 tile 物理深度）
        W = self.W
        F = int(F)
        assert F >= W, f"F={F} must be >= W=AR+L+1={W}"
        assert F <= self.K_ABUF_MAX, f"F={F} must be <= K_ABUF_MAX={self.K_ABUF_MAX} (abuf 容量上限)"
        start_edge = bool(start) and not self.start_prev

        ws_state_next, ws_cnt_next = self._fsm_next(
            start_edge, bool(weight_loaded), bool(activ_available), F, bool(switch_weight))

        switch_sr_next, b_sw_next, cur_tile_next, pending_tile_next = \
            self._switch_and_tile_next(self.ws_state, ws_state_next, int(tag))

        feed_next, wr_vld_next, wr_row_next, wr_tile_next = \
            self._drivers_next(ws_state_next, ws_cnt_next, F, cur_tile_next)

        prop_next, rd_en_next, rd_addr_next, rd_ptr_next, page_next = \
            self._abuf_addr_next(F, int(tile_num), bool(switch_page))

        # 锁到 next 槽位
        self._ws_state_next     = ws_state_next
        self._ws_cnt_next       = ws_cnt_next
        self._switch_sr_next    = switch_sr_next
        self._prop_next         = prop_next
        self._rd_ptr_next       = rd_ptr_next
        self._page_next         = page_next
        self._start_prev_next   = bool(start)
        self._cur_tile_next     = cur_tile_next
        self._pending_tile_next = pending_tile_next
        self._feed_next         = feed_next
        self._b_sw_next         = b_sw_next
        self._wr_row_next       = wr_row_next
        self._wr_tile_next      = wr_tile_next
        self._wr_vld_next       = wr_vld_next
        self._rd_en_next        = rd_en_next
        self._rd_addr_next      = rd_addr_next
        self._switch_page_in    = bool(switch_page)

    # ------------------------------------------------------------------ #
    # _fsm_next — 6-state 状态机：(ws_state, ws_cnt) → (ws_state_next, ws_cnt_next)
    # ------------------------------------------------------------------ #
    def _fsm_next(self, start_edge, weight_loaded, activ_available, F, switch_weight):
        W = self.W
        cs = self.ws_state
        ws_state_next = cs
        ws_cnt_next   = self.ws_cnt + 1

        if cs == WSStateRef.IDLE:
            ws_cnt_next = 0
            if start_edge:
                ws_state_next = WSStateRef.WLOAD
        elif cs == WSStateRef.WLOAD:
            if weight_loaded and activ_available:
                ws_state_next = WSStateRef.FEED
                ws_cnt_next   = 0
        elif cs == WSStateRef.FEED:
            if self.ws_cnt == W - 1:
                if F > W:
                    ws_state_next = WSStateRef.CAPTURE
                else:
                    # F == W：CAPTURE 0 拍，直接 OVERLAP/DRAIN
                    ws_state_next = WSStateRef.OVERLAP if switch_weight else WSStateRef.DRAIN
                ws_cnt_next = 0
        elif cs == WSStateRef.CAPTURE:
            if self.ws_cnt == F - W - 1:
                # 切下个 tile（switch_weight=1）→ OVERLAP；否则 DRAIN
                # 不再检查 weight_loaded：上层确保 sw 时 weight 在 OVERLAP 期间能装完（SA 内 b_sw
                # 沿列每拍传 1 格，给 W 拍 warmup 足够 tile 1 weight 跟上）
                ws_state_next = WSStateRef.OVERLAP if switch_weight else WSStateRef.DRAIN
                ws_cnt_next = 0
        elif cs == WSStateRef.OVERLAP:
            if self.ws_cnt == W - 1:
                if F > W:
                    ws_state_next = WSStateRef.CAPTURE
                else:
                    # F == W：OVERLAP 已经把下个 tile 完整 feed 完，跳过 CAPTURE
                    ws_state_next = WSStateRef.OVERLAP if switch_weight else WSStateRef.DRAIN
                ws_cnt_next = 0
        elif cs == WSStateRef.DRAIN:
            if self.ws_cnt == W - 1:
                ws_state_next = WSStateRef.IDLE
                ws_cnt_next   = 0

        return ws_state_next, ws_cnt_next

    # ------------------------------------------------------------------ #
    # _switch_and_tile_next — cold/boundary inject → switch_sr / b_sw + tile tag
    # cold     : WLOAD→FEED 首段冷启动恒切（单拍脉冲）
    # boundary : 进入 OVERLAP 的【那拍】= 段间切 weight（单拍脉冲）
    #   - FEED→OVERLAP (F==W 首段) / CAPTURE→OVERLAP (F>W) / OVERLAP→OVERLAP (F==W 连续段)
    #   OVERLAP→OVERLAP 必须 ws_cnt == W-1 才是转换拍（OVERLAP 内部 state_next 一直是 OVERLAP，
    #   不能每拍都注入，否则 b_sw 变电平）
    # cur_tile : 当前 capture 的 tile（wr_tile 出口源）
    # pending  : 边界 inject 时暂存下一 tile 的 tag
    # ------------------------------------------------------------------ #
    def _switch_and_tile_next(self, cs, ws_state_next, tag):
        AR, W = self.AR, self.W

        cold     = (cs == WSStateRef.WLOAD) and (ws_state_next == WSStateRef.FEED)
        boundary = (
            ((cs in (WSStateRef.FEED, WSStateRef.CAPTURE)) and (ws_state_next == WSStateRef.OVERLAP))
            or ((cs == WSStateRef.OVERLAP) and (self.ws_cnt == W - 1)
                and (ws_state_next == WSStateRef.OVERLAP))
        )
        ws_switch_inject = cold or boundary

        switch_sr_next = [False] * AR
        switch_sr_next[0] = ws_switch_inject
        for k in range(1, AR):
            switch_sr_next[k] = self.switch_sr[k - 1]
        b_sw_next = list(switch_sr_next)

        if cold:
            cur_tile_next     = tag
            pending_tile_next = self.pending_tile
        elif (cs == WSStateRef.OVERLAP) and (ws_state_next == WSStateRef.OVERLAP) and (self.ws_cnt == W - 1):
            # OVERLAP→OVERLAP 转换拍（F==W 连续段）：cur_tile 推进，pending 锁新 i_tag
            cur_tile_next     = self.pending_tile
            pending_tile_next = tag
        elif boundary:
            # FEED/CAPTURE→OVERLAP：当前段还在 capture，下段 tag 暂存
            cur_tile_next     = self.cur_tile
            pending_tile_next = tag
        elif (cs == WSStateRef.OVERLAP) and (ws_state_next in (WSStateRef.CAPTURE, WSStateRef.DRAIN)):
            # OVERLAP→CAPTURE 切到新 tile capture；OVERLAP→DRAIN (F==W) 也推进 cur_tile
            cur_tile_next     = self.pending_tile
            pending_tile_next = self.pending_tile
        else:
            cur_tile_next     = self.cur_tile
            pending_tile_next = self.pending_tile

        return switch_sr_next, b_sw_next, cur_tile_next, pending_tile_next

    # ------------------------------------------------------------------ #
    # _drivers_next — feed / wr_vld / wr_row / wr_tile（基于 state_next）
    # feed=1   : state_next ∈ {FEED, CAPTURE, OVERLAP}
    # wr_vld=1 : state_next ∈ {CAPTURE, OVERLAP, DRAIN}
    # wr_row   : CAPTURE → ws_cnt_next (0..F-W-1)；OVERLAP/DRAIN → (F-W)+ws_cnt_next (F-W..F-1)
    # wr_tile  : 写哪个 tile，取 cur_tile_next（边沿采样）
    # ------------------------------------------------------------------ #
    def _drivers_next(self, ws_state_next, ws_cnt_next, F, cur_tile_next):
        W = self.W
        feed_next   = ws_state_next in (WSStateRef.FEED, WSStateRef.CAPTURE, WSStateRef.OVERLAP)
        wr_vld_next = ws_state_next in (WSStateRef.CAPTURE, WSStateRef.OVERLAP, WSStateRef.DRAIN)

        if ws_state_next == WSStateRef.CAPTURE:
            wr_row_next = ws_cnt_next
        elif ws_state_next in (WSStateRef.OVERLAP, WSStateRef.DRAIN):
            wr_row_next = (F - W) + ws_cnt_next
        else:
            wr_row_next = 0

        wr_tile_next = cur_tile_next if wr_vld_next else 0
        return feed_next, wr_vld_next, wr_row_next, wr_tile_next

    # ------------------------------------------------------------------ #
    # _abuf_addr_next — prop / rd_en / rd_addr / rd_ptr / page
    # prop_next[0] = self.feed（上拍 commit 后值，与 RTL prop_next[0] = o_feed 对齐）
    # ------------------------------------------------------------------ #
    def _abuf_addr_next(self, F, tile_num, switch_page):
        AR = self.AR

        prop_next = [False] * AR
        prop_next[0] = bool(self.feed)
        for c in range(1, AR):
            prop_next[c] = self.prop[c - 1]

        cap         = tile_num * F                                  # 当前作业 abuf 每 tile 深度 = F
        page_offset = self.PAGE_SPAN if self.page else 0

        rd_en_next   = [False] * AR
        rd_addr_next = [0]     * AR
        rd_ptr_next  = list(self.rd_ptr)
        for c in range(AR):
            rd_en_next[c]   = prop_next[c]
            rd_addr_next[c] = page_offset + self.rd_ptr[c]
            if prop_next[c]:
                if self.rd_ptr[c] + 1 >= cap:
                    rd_ptr_next[c] = 0
                else:
                    rd_ptr_next[c] = self.rd_ptr[c] + 1

        page_next = self.page ^ (1 if switch_page else 0)
        return prop_next, rd_en_next, rd_addr_next, rd_ptr_next, page_next

    # ------------------------------------------------------------------ #
    # commit — 锁存 next（纯时序）
    # ------------------------------------------------------------------ #
    def commit(self):
        AR = self.AR
        self.ws_state     = self._ws_state_next
        self.ws_cnt       = self._ws_cnt_next
        self.switch_sr    = list(self._switch_sr_next)
        self.prop         = list(self._prop_next)
        if self._switch_page_in:
            self.rd_ptr   = [0] * AR
        else:
            self.rd_ptr   = list(self._rd_ptr_next)
        self.page         = self._page_next
        self.start_prev   = self._start_prev_next
        self.cur_tile     = self._cur_tile_next
        self.pending_tile = self._pending_tile_next
        self.feed         = self._feed_next
        self.b_sw         = list(self._b_sw_next)
        self.wr_row       = self._wr_row_next
        self.wr_tile      = self._wr_tile_next
        self.wr_vld       = self._wr_vld_next
        self.rd_en        = list(self._rd_en_next)
        self.rd_addr      = list(self._rd_addr_next)

    # ------------------------------------------------------------------ #
    # 便捷：一拍完成（update + commit）
    # ------------------------------------------------------------------ #
    def tick(self, start, weight_loaded, activ_available, F,
             switch_weight=False, tag=0, tile_num=1, switch_page=False):
        self.update(start, weight_loaded, activ_available, F,
                    switch_weight=switch_weight, tag=tag,
                    tile_num=tile_num, switch_page=switch_page)
        self.commit()
