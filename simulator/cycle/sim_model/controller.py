import logging
from enum import Enum, auto

from .module import module

logger = logging.getLogger(__name__)

class OSState(Enum):
    IDLE             = auto()  # 无活跃 tile
    COMPUTE          = auto()  # 最老 tile 填充/传播中，drain 尚未开始
    DRAIN            = auto()  # 最老 tile 输出结果中，无后续 tile
    OVERLAP_SEAMLESS = auto()  # 最老 tile drain 与后继 feed 重叠，无气泡
    OVERLAP_GAP      = auto()  # 最老 tile drain，后继被数据饥饿卡住（气泡拍）

class WSState(Enum):
    IDLE   = auto()  # 无活跃 tile
    WLOAD  = auto()  # 冷启动：首 tile 权重逐拍移入 shadow（AR 拍，暴露）
    STREAM = auto()  # 连续喂 F 行激活 + 反对角线 switch 波前 + 后台 shadow load + capture
    STALL  = auto()  # 预留：activation 饥饿 / weight 没备好 → feed+switch 冻结、capture 继续
    DRAIN  = auto()  # 末 tile 排空剩余 psum（仅 capture），到最后一个 capture → IDLE

class Controller(module):
    _MODE_FLAGS = {
        "OS": (True,  True),
        "WS": (True,  False),
        "IS": (False, True),
    }
    _LOAD_TO_PE = 2  # load pulse 到该 tile 首元素到达 PE(0,0) 的延迟（buffer load→state→feed）
    # 三个 feed 态行为相同（喂 K 拍），只是入口不同：首个/背靠背/迟到接入
    _OS_FEED_STATES = (OSState.COMPUTE, OSState.OVERLAP_SEAMLESS, OSState.OVERLAP_GAP)
    # ── 维度约定 ──────────────────────────────────────────────────────────────
    # self.M/self.N 恒为【物理阵列】行/列；self._K 为【时间维 feed 拍数】。
    # 完整矩阵乘：C = A × B

    # A 是 M 行 × K 列
    # B 是 K 行 × N 列
    # C 是 M 行 × N 列
    # 所以 M、N、K = 整个问题的三个尺寸（M=输出行数，N=输出列数，K=中间相乘相加的"收缩"长度）。
    # WS 下阵列怎么摆：阵列行 = K 方向、阵列列 = N 方向、M 走时间（一拍喂一行激活）。
    # 一次阵列最多吃下 K 的"阵列行数"那么深、N 的"阵列列数"那么宽。
    # K 比阵列行数大 → K 要切几段（每段 = 阵列行数那么深）。
    # N 比阵列列数大 → N 要切几块。
    # M 不用切（时间上一直流）。

    # mode 在 controller 显形：OS/WS/IS 各自独立状态机 + 独立信号生成（if/else 分开，pe/sa 不知 mode）。
    # ─────────────────────────────────────────────────────────────────────────
    def __init__(self, M: int, N: int, K: int, mode: str = "OS", latency: int = 1):
        self.M, self.N, self._K = M, N, K
        self._mode = mode
        self.shift_row, self.shift_col = self._MODE_FLAGS[mode]
        # PE latency L 是唯一根参数，三个延迟都从它推导：
        #   OS drain_delay/restore_delay = L+1（补偿 PE MAC 流水 + 寄存，drain/overwrite 波前延后读）
        #   WS cap_delay = (AR-1)+L（psum 下流 AR 行的空间延迟 + PE 流水），见 WS capture
        self._latency       = latency
        self._drain_delay   = latency + 1
        self._restore_delay = latency + 1
        self.reset()

    def issue(self, instr):
        pass

    # ------------------------------------------------------------------ #
    # 第二段：次态组合逻辑（纯组合，只写 _os_state_next / _cnt_next）
    # ------------------------------------------------------------------ #
    def _next_os_state(self, weight_available: bool, activ_available: bool):
        cs      = self.os_state
        cnt     = self._cnt
        K, M, N = self._K, self.M, self.N

        # 下一 tile 的 weight/activation 都就绪
        avail = (((not self.shift_col) or weight_available) and
                 ((not self.shift_row) or activ_available))

        # 默认保持现态、计数器 +1，再按 case 覆盖（避免 latch）。cnt 每相位清零
        self._os_state_next = cs
        self._cnt_next           = cnt + 1

        # --- 完整 5 状态；阈值固定，与 read/skew 无关 ---
        if cs == OSState.IDLE:
            self._cnt_next = 0
            if avail:
                self._os_state_next = OSState.COMPUTE
        elif cs in self._OS_FEED_STATES:                            # feed K 拍：cnt 0..K-1
            if cnt == K - 1:                                     # feed 边界：接上→SEAMLESS，否则→DRAIN
                self._os_state_next = (
                    OSState.OVERLAP_SEAMLESS if avail else OSState.DRAIN)
                self._cnt_next = 0
        elif cs == OSState.DRAIN:                           # drain M+N-1 拍：cnt 0..M+N-2
            if avail:                                            # 迟到 tile drain 中途到达 → 带间隔喂
                self._os_state_next = OSState.OVERLAP_GAP
                self._cnt_next = 0
            elif cnt == M + N - 2:
                self._os_state_next = OSState.IDLE
                self._cnt_next = 0

        # --- 块边界事件（驱动 drain / overwrite / tile_id；流水与顺序统一）---
        nxt         = self._os_state_next
        boundary    = cs in self._OS_FEED_STATES and cnt == K - 1          # 某 chunk feed 末拍
        chunk_start = self._cnt_next == 0 and nxt in self._OS_FEED_STATES  # 某 chunk feed 起拍
        # 旧块完成 → drain：本 chunk 是块的最后一个（后面是新块 new_tile，或没有了 →DRAIN）
        self._block_end   = boundary and ((not avail) or self._new_tile)
        # 新输出块首 chunk 起 → tile_id+1 + overwrite(=0)
        self._block_start = self._new_tile and chunk_start
        # acc_read 注入：新块 overwrite(data=0)
        self._psum_init   = self._block_start
        # 写哪个 accumulator 槽由指令 accum_addr 决定（_new_tile_id）；drain 注入仍用旧 _tile_id
        self._tile_id_next = self._new_tile_id if self._block_start else self._tile_id

        if self._os_state_next != cs:
            logger.debug("[compute] cy=%d  %s → %s", self._cycle, cs.name, self._os_state_next.name)

    # ------------------------------------------------------------------ #
    # 第三段：输出组合逻辑（寄存输出，按次态算 → 锁存后与状态同拍对齐）
    # ------------------------------------------------------------------ #
    def _os_feed(self):
        # controller 只发标量 feed（这拍喂不喂）；skew 由 wb/ab fifo 内部 lane 传播生成
        # （见 doc/weight_fifo_design.md §9）。头部 ramp、drain 尾巴、两 tile 重叠都在 fifo 那侧天然包办。
        self._feed_next = self._os_state_next in self._OS_FEED_STATES

    def _os_drain(self):
        # acc(write) 行向移位寄存器（长 M+drain_delay）：旧块完成往 d=0 注入 tile_id，每拍向高 d 推进。
        # 控制随数据从左进、在 sa 内右传：ctrl 只给【左边缘每行 drain 使能】output_sel[r]（bool）。
        # 列方向(+c) 的传播在 sa；目的槽 tile_id 由标量 wr_tile 给 accum（drain 不重叠，一拍至多一个块，
        # 靠 Gk≥M+N-1 保证）：row0 左边缘起 drain 那拍锁存该块号，保持到下个块 drain 起。
        inject = self._tile_id if self._block_end else None
        self._acc_sr_next = [inject] + self._acc_sr[:-1]
        D = self._drain_delay
        self._output_sel_next = [self._acc_sr_next[r + D] is not None for r in range(self.M)]
        head = self._acc_sr_next[D]                      # row0 本拍 drain 的块（None=无新 drain 起）
        self._wr_tile_next = head if head is not None else self._wr_tile

    def _os_restore(self):
        # acc_read：psum-init 波前（新块首 chunk overwrite,data=0）→ 左边缘每行 bool 使能 acc_rst[r]，sa 右传成波前。
        self._acc_read_sr_next = [self._psum_init] + self._acc_read_sr[:-1]
        D = self._restore_delay
        self._acc_rst_next = [self._acc_read_sr_next[r + D] for r in range(self.M)]

    # ================================================================== #
    # WS 路径（独立状态机 + 四道波前；pe/sa 不知 mode）。见 doc/WS_weight_design.md
    # ================================================================== #
    def _next_ws_state(self, weight_available: bool, activ_available: bool):
        cs, cnt = self.ws_state, self._ws_cnt
        AR, AC, F = self.M, self.N, self._K
        avail = weight_available and activ_available    # 权重(载 shadow)+激活都就绪
        self.ws_state_next = cs
        self._ws_cnt_next  = cnt + 1
        if cs == WSState.IDLE:
            self._ws_cnt_next = 0
            if activ_available:                          # 有激活就启动；权重在 WLOAD 里载（避免空 shadow 死锁）
                self.ws_state_next = WSState.WLOAD
        elif cs == WSState.WLOAD:                        # 载 shadow：sa 每列反压全拉起 = 灌满
            if weight_available:                         # = sa [N] 反压全拉起（不再 controller 数 AR 拍）
                self.ws_state_next = WSState.STREAM
                self._ws_cnt_next = 0
        elif cs == WSState.STREAM:                       # 喂 F 行激活
            if cnt == F - 1:
                self.ws_state_next = WSState.STREAM if avail else WSState.DRAIN
                self._ws_cnt_next = 0
            # STALL 预留：avail 但数据没齐 / tile 内饥饿 → STALL（v1 不触发）
        elif cs == WSState.STALL:                        # 预留
            if avail:
                self.ws_state_next = WSState.STREAM
                self._ws_cnt_next = 0
        elif cs == WSState.DRAIN:                        # 排空到最后一个 capture
            if cnt == AR + AC + self._latency - 2:       # = cap_delay + AC - F... 跑测试钉死
                self.ws_state_next = WSState.IDLE
                self._ws_cnt_next = 0
        # switch 注入：首 tile WLOAD→STREAM 恒切；STREAM 边界 switch_weight 切
        nxt = self.ws_state_next
        cold     = (cs == WSState.WLOAD and nxt == WSState.STREAM)
        boundary = (cs == WSState.STREAM and cnt == F - 1 and nxt == WSState.STREAM
                    and self._switch_weight)
        self._ws_switch_inject = cold or boundary

    def _ws_feed(self):
        # 标量 feed（STREAM 期间喂激活）；activation 的 skew 由 ab buf 内部传播（activation_buf 设计待写）
        self._feed_next = (self.ws_state_next == WSState.STREAM)

    def _ws_switch(self):
        # b_sw[k]=switch_sr[k]：左边缘按行 skew 注入，列向 +c 由 sa 右推（成对角线 k+n）。
        # 与旧 w_switch[k][n]=sr[k+n] 几何等价（把 +c 从 controller 挪给 sa）。
        self._ws_switch_sr_next = [self._ws_switch_inject] + self._ws_switch_sr[:-1]
        self._b_sw_next = [self._ws_switch_sr_next[k] for k in range(self.M)]

    def _ws_capture(self):
        # capture 标量注入：STREAM 喂行 m=cnt_next 时输出 (m, wr_tile=tag)，否则 None。
        # per-column 对齐（psum 下流 AR 行 + 流水 cap_delay）由下游随 psum 传播，controller 不算 [N]。
        # wr_tile 与 OS drain 槽同一语义（§14-D）。
        if self.ws_state_next == WSState.STREAM:
            self._ws_m_next       = self._ws_cnt_next
            self._ws_wr_tile_next = self._tag
        else:
            self._ws_m_next       = None
            self._ws_wr_tile_next = None

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def update(self, cycle, weight_available: bool, activ_available: bool,
               new_tile: bool = False, tile_id: int = 0,
               switch_weight: bool = False, tag: int = 0):
        self._cycle = cycle
        # mode 在此显形：OS/WS 各自独立状态机 + 信号生成（IS 待实现）。pe/sa 不知 mode。
        if self._mode == "OS":
            self._new_tile    = new_tile     # 新输出块：写 _new_tile_id + psum overwrite(=0)
            self._new_tile_id = tile_id      # 新块写入的 accumulator 槽（指令 accum_addr）
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("[ctrl] cy=%4d  %-16s cnt=%2d tid=%d  feed=%d  wA=%d aA=%d nt=%d",
                             cycle, self.os_state.name, self._cnt, self._tile_id, int(self.feed),
                             int(weight_available), int(activ_available), int(new_tile))
            self._next_os_state(weight_available, activ_available)  # ② 次态组合（_os_state_next/_cnt_next）
            self._os_feed()                                         # ③ 输出组合（依赖 _os_state_next）
            self._os_drain()
            self._os_restore()
        elif self._mode == "WS":
            self._switch_weight = switch_weight
            self._tag           = tag
            self._next_ws_state(weight_available, activ_available)  # ② 次态
            self._ws_feed()                                         # ③ 三道控制波（feed/switch/capture）
            self._ws_switch()                                       #   权重载入走 wb↔sa 自握手，controller 不产
            self._ws_capture()
        else:
            raise NotImplementedError(f"mode {self._mode} 未实现")

    def commit(self):
        # 第一段：时序，纯锁存 reg = reg_next（无任何逻辑）。按 mode 分支锁存各自寄存器。
        if self._mode == "OS":
            self.os_state        = self._os_state_next
            self._cnt            = self._cnt_next
            self._tile_id        = self._tile_id_next
            self.feed            = self._feed_next       # 标量 feed（给 wb/ab fifo，内部传播出 skew）
            self._acc_sr         = self._acc_sr_next
            self._acc_read_sr    = self._acc_read_sr_next
            self.output_sel = self._output_sel_next  # 左边缘每行 drain 使能(bool [M])，sa 右传成波前
            self.wr_tile    = self._wr_tile_next     # 标量目的槽 tile_id（给 accum；drain 不重叠）
            self.acc_rst    = self._acc_rst_next     # 左边缘每行 reset 使能(bool [M])，sa 右传成波前
        elif self._mode == "WS":
            self.ws_state        = self.ws_state_next
            self._ws_cnt         = self._ws_cnt_next
            self.feed            = self._feed_next       # 标量 feed（给 ab buf，内部传播出 skew）
            self._ws_switch_sr   = self._ws_switch_sr_next
            self.b_sw            = self._b_sw_next          # 左边缘每行换权重使能(bool [M])，sa 右推成 k+n
            self.m               = self._ws_m_next          # 标量：本拍喂的输出行（None=非 STREAM）
            self.wr_tile         = self._ws_wr_tile_next    # 标量：写哪个槽（= 旧 tag，同 OS 语义）

    def reset(self):
        self._cycle = 0  # 仅用于打印
        # 共享输出（OS/WS 都用）：标量 feed（skew 由下游 fifo/buf 内部传播生成）
        self.feed       = False
        self._feed_next = False
        # 按 mode 只初始化各自寄存器（与 update/commit 对称）
        if self._mode == "OS":
            self._reset_os()
        elif self._mode == "WS":
            self._reset_ws()
        else:
            raise NotImplementedError(f"mode {self._mode} 未实现")

    def _reset_os(self):
        self._cnt                = 0
        self._cnt_next           = 0
        self._tile_id            = 0
        self._tile_id_next       = 0
        self.os_state            = OSState.IDLE
        self._os_state_next      = OSState.IDLE
        # drain/restore 行向 SR：只取 col-0 [r+D]，行向+delay 够（列向传播在 sa）。长 M+各自 delay
        DLEN_w = self.M + self._drain_delay
        DLEN_r = self.M + self._restore_delay
        self._acc_sr            = [None] * DLEN_w
        self._acc_sr_next       = [None] * DLEN_w
        self._acc_read_sr       = [False] * DLEN_r
        self._acc_read_sr_next  = [False] * DLEN_r
        self._new_tile    = False   # 新输出块（由 update 传入）
        self._new_tile_id = 0       # 新块写入的 accumulator 槽（由 update 传入）
        self._block_end   = False   # 本拍旧块完成（drain）
        self._block_start = False   # 本拍新块首 chunk 起（tile_id+1 + overwrite）
        self._psum_init   = False   # 本拍 acc_read 注入（overwrite）
        # 左边缘每行注入（长 M，sa 右传成波前）：output_sel=drain 使能(bool)、acc_rst=reset 使能(bool)
        self._output_sel_next = [False] * self.M
        self._acc_rst_next    = [False] * self.M
        self.output_sel = [False] * self.M  # 左边缘每行 drain 使能(bool)
        self.acc_rst    = [False] * self.M  # 左边缘每行 reset 使能(bool)
        self._wr_tile = 0       # 标量目的槽 tile_id（drain 起拍锁存当前块号、保持）
        self._wr_tile_next = 0
        self.wr_tile = 0

    def _reset_ws(self):
        # AR=self.M 行=K, AC=self.N 列=N, F=self._K=M
        AR = self.M
        self.ws_state            = WSState.IDLE
        self.ws_state_next       = WSState.IDLE
        self._ws_cnt             = 0
        self._ws_cnt_next        = 0
        self._switch_weight      = False    # 输入：边界是否换权重
        self._tag                = 0        # 输入：当前 tile 标识 → 打进 capture
        self._ws_switch_inject   = False    # 本拍是否往 switch SR 注入（首 tile 恒切 / 边界 switch_weight）
        # switch SR（长 AR，行 skew）→ b_sw[k]=sr[k]；列向 +c 由 sa 右推（旧 sr[k+n] 的 +c 移到 sa）
        self._ws_switch_sr       = [False] * AR
        self._ws_switch_sr_next  = [False] * AR
        # 输出
        self.b_sw          = [False] * AR     # 左边缘每行换权重使能(bool [M])
        self._b_sw_next    = [False] * AR
        self.m             = None             # 标量：本拍喂的输出行（None=非 STREAM）
        self._ws_m_next    = None
        self.wr_tile       = 0                # 标量：写哪个槽（= 旧 tag，同 OS 语义）
        self._ws_wr_tile_next = None