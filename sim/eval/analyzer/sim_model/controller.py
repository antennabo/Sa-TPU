import logging
from collections import deque
from enum import Enum, auto

logger = logging.getLogger(__name__)


class LoadState(Enum):
    IDLE    = auto()  # 无 load 任务
    PENDING = auto()  # 本拍触发 wb.load()，数据写入 inactive bank
    DONE    = auto()  # load 完成（持续 1 拍）


class ComputeState(Enum):
    IDLE    = auto()  # 无活跃 tile
    COMPUTE = auto()  # tile 正在填入 SA，drain 尚未开始
    DRAIN   = auto()  # SA 输出结果中，无后续 tile
    OVERLAP = auto()  # tile N drain 与 tile N+1 feed 同步进行


class Controller:
    _MODE_FLAGS = {
        "OS": (True,  True),
        "WS": (True,  False),
        "IS": (False, True),
    }

    def __init__(self, M: int, N: int, K: int, mode: str = "OS"):
        self.M, self.N, self._K = M, N, K
        self._mode = mode
        self.shift_row, self.shift_col = self._MODE_FLAGS[mode]
        self.reset()

    def issue(self, instr):
        pass

    # ------------------------------------------------------------------ #
    # Internal helper
    # ------------------------------------------------------------------ #

    def _check_drain_status(self, cycle):
        """(first_row_done, last_row_done) for oldest in-flight tile. Pure."""
        if not self._comp_starts:
            logger.debug("[drain  ] cy=%d  _comp_starts empty → (False, False)", cycle)
            return False, False
        elapsed   = cycle - self._comp_starts[0]
        K, N, M   = self._K, self.N, self.M
        thr_first = K + N - 1
        thr_last  = K + N - 1 + M - 1 + N
        first_done = elapsed == thr_first
        last_done  = elapsed == thr_last
        logger.debug(
            "[drain  ] cy=%d  comp_start=%d  elapsed=%d  thr_first=%d  thr_last=%d"
            "  → first=%s  last=%s",
            cycle, self._comp_starts[0], elapsed, thr_first, thr_last,
            first_done, last_done,
        )
        return first_done, last_done

    def _update_pe_result_ready(self, cnt: int):
        """按对角线更新 pe_result_ready bitmap。
        PE(r, c) 在 cnt == comp_start + (K+N-1) + (r+c) 时完成。"""
        if not self._comp_starts:
            return
        diag = cnt - self._comp_starts[0] - (self.N - 1)
        if diag < 0:
            return
        value = None if diag >= 8 else diag
        self.pe_result_ready = [value] + self.pe_result_ready[:-1]
        logger.debug("[pe_done] cnt=%d  pe_result_ready=%s", cnt, self.pe_result_ready)

    # ------------------------------------------------------------------ #
    # Stage 2: Next-state logic  (pure — no mutations to any register)
    # ------------------------------------------------------------------ #

    def _next_load_state(self, weight_available: bool):
        if not self.shift_col:
            return
        s = self.load_state
        if s == LoadState.IDLE:
            self._load_state_next = (
                LoadState.PENDING if weight_available
                else LoadState.IDLE
            )
        elif s == LoadState.PENDING:
            self._load_state_next = LoadState.DONE
        elif s == LoadState.DONE:
            self._load_state_next = LoadState.IDLE
        if self._load_state_next != s:
            logger.debug("[load   ] cy=%d  %s → %s", self._cycle, s.name, self._load_state_next.name)

    def _next_compute_state(self, has_row: bool, has_col: bool,
                             wb_pending: bool, ab_pending: bool):
        self._pending_pushes        = []
        self._pending_pops          = 0
        self._pending_tile_id_delta = 0

        cycle = self._cycle
        cs    = self.compute_state
        K, N, M = self._K, self.N, self.M
        first_row_done, last_row_done = self._check_drain_status(cycle)

        if cs == ComputeState.IDLE:
            weight_ready = not self.shift_col or wb_pending
            activ_ready  = not self.shift_row or ab_pending
            if weight_ready and activ_ready:
                self._pending_pushes.append(cycle + 1)
                self._compute_state_next = ComputeState.COMPUTE
            else:
                self._compute_state_next = ComputeState.IDLE

        elif cs == ComputeState.COMPUTE:
            if last_row_done:
                self._pending_pops          += 1
                self._pending_tile_id_delta += 1
                self._compute_state_next = ComputeState.IDLE
            elif first_row_done:
                self._compute_state_next = (
                    ComputeState.OVERLAP if wb_pending else ComputeState.DRAIN
                )
            else:
                self._compute_state_next = ComputeState.COMPUTE

        elif cs == ComputeState.DRAIN:
            if last_row_done:
                self._pending_pops          += 1
                self._pending_tile_id_delta += 1
                self._compute_state_next = ComputeState.IDLE
            elif wb_pending:
                self._compute_state_next = ComputeState.OVERLAP
            else:
                self._compute_state_next = ComputeState.DRAIN

        elif cs == ComputeState.OVERLAP:
            # TBD: compute的状态机还有bug，需要修改
            if has_row and has_col and len(self._comp_starts) == 1:
                self._pending_pushes.append(cycle)
                logger.debug("[compute] cy=%d  tile N+1 comp_start=%d", cycle, cycle)

            if last_row_done:
                self._pending_pops          += 1
                self._pending_tile_id_delta += 1
                # Tile N+1's comp_start: already queued at [1], or staged above
                if len(self._comp_starts) > 1:
                    n1_start = self._comp_starts[1]
                elif self._pending_pushes:
                    n1_start = self._pending_pushes[0]
                else:
                    n1_start = None

                if n1_start is None:
                    self._compute_state_next = ComputeState.IDLE
                else:
                    elapsed_n1 = cycle - n1_start
                    first_n1 = elapsed_n1 == K + N - 1
                    last_n1  = elapsed_n1 == K + N - 1 + M - 1
                    if last_n1:
                        self._pending_pops          += 1
                        self._pending_tile_id_delta += 1
                        self._compute_state_next = ComputeState.IDLE
                    elif first_n1 and wb_pending:
                        self._compute_state_next = ComputeState.OVERLAP
                    elif first_n1:
                        self._compute_state_next = ComputeState.DRAIN
                    else:
                        self._compute_state_next = ComputeState.COMPUTE
            else:
                self._compute_state_next = ComputeState.OVERLAP

        if self._compute_state_next != cs:
            logger.debug("[compute] cy=%d  %s → %s  tile=%d",
                         cycle, cs.name, self._compute_state_next.name, self._tile_id)

    # ------------------------------------------------------------------ #
    # Stage 3: Set output control signals  (reads current state only)
    # ------------------------------------------------------------------ #

    def _set_output_signals(self, ab_pending: bool, activ_available: bool):
        self.load_weight     = self.shift_col and self.load_state == LoadState.PENDING
        self.weight_add_head = self._first_wb
        self.load_activation = self.shift_row and not ab_pending and activ_available
        self.activ_add_head  = self._first_ab
        self.drive_sa        = self.compute_state != ComputeState.IDLE
        self.buf_advance     = self._compute_state_next != ComputeState.IDLE

        self.drain_tile_id = self._tile_id

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def control(self, has_row: bool, has_col: bool,
                wb_pending: bool, ab_pending: bool,
                weight_available: bool, activ_available: bool):
        print(f"[ctrl] cy={self._cycle:4d}  compute={self.compute_state.name}")
        # Stage 2: next state
        self._next_load_state(weight_available)
        self._next_compute_state(has_row, has_col, wb_pending, ab_pending)

        # Stage 3: output signals
        self._set_output_signals(ab_pending, activ_available)

    def commit(self):
        # Update PE result bitmap (same cycle as accum.load_write)
        self._update_pe_result_ready(self._cycle)

        # FSM registers
        self._cycle        += 1
        self.load_state     = self._load_state_next
        self.compute_state  = self._compute_state_next
        for start in self._pending_pushes:
            self._comp_starts.append(start)
        for _ in range(self._pending_pops):
            self._comp_starts.popleft()
        self._tile_id += self._pending_tile_id_delta

        # Update add_head flags after first load
        if self.load_weight:
            self._first_wb = False
        if self.load_activation:
            self._first_ab = False

    def reset(self):
        # Scheduling state
        self._comp_starts = deque()  # comp_start cycle per in-flight tile
        self._tile_id     = 0
        self._cycle       = 0
        self._first_wb    = True
        self._first_ab    = True

        # FSM registers
        self.load_state          = LoadState.IDLE
        self._load_state_next    = LoadState.IDLE
        self.compute_state       = ComputeState.IDLE
        self._compute_state_next = ComputeState.IDLE

        # Within-cycle staged mutations (reset at start of _next_compute_state)
        self._pending_pushes        = []
        self._pending_pops          = 0
        self._pending_tile_id_delta = 0

        # 长度 N 的移位寄存器：pe_result_ready[i] 记录第 i 列 PE 完成时的周期号
        # 每拍从头部移入 cnt，尾部淘汰，初始全为 None
        self.pe_result_ready: list[int | None] = [None] * self.N

        # Output control signals (valid after control())
        self.load_weight     = False  # 本拍应将 weight tile 写入 wb
        self.weight_add_head = False  # wb.load() 的 add_head 参数
        self.load_activation = False  # 本拍应将 activation tile 写入 ab
        self.activ_add_head  = False  # ab.load() 的 add_head 参数
        self.drive_sa        = False  # 本拍应驱动 SA 输入
        self.buf_advance     = False  # 本拍应推进 wb/ab 读 pipeline
        self.drain_tile_id   = 0      # drain 写入 accum 的 tile 编号
