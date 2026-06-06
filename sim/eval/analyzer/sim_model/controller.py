import logging
from enum import Enum, auto

from .module import module

logger = logging.getLogger(__name__)

class ComputeState(Enum):
    IDLE             = auto()  # 无活跃 tile
    COMPUTE          = auto()  # 最老 tile 填充/传播中，drain 尚未开始
    DRAIN            = auto()  # 最老 tile 输出结果中，无后续 tile
    OVERLAP_SEAMLESS = auto()  # 最老 tile drain 与后继 feed 重叠，无气泡
    OVERLAP_GAP      = auto()  # 最老 tile drain，后继被数据饥饿卡住（气泡拍）

class Controller(module):
    _MODE_FLAGS = {
        "OS": (True,  True),
        "WS": (True,  False),
        "IS": (False, True),
    }
    _LOAD_TO_PE = 2  # load pulse 到该 tile 首元素到达 PE(0,0) 的延迟（buffer load→state→feed）
    # 三个 feed 态行为相同（喂 K 拍），只是入口不同：首个/背靠背/迟到接入
    _FEED_STATES = (ComputeState.COMPUTE, ComputeState.OVERLAP_SEAMLESS, ComputeState.OVERLAP_GAP)

    def __init__(self, M: int, N: int, K: int, mode: str = "OS",
                 drain_delay: int = 0, restore_delay: int = None):
        self.M, self.N, self._K = M, N, K
        self._mode = mode
        self.shift_row, self.shift_col = self._MODE_FLAGS[mode]
        # acc(drain) 波前延后拍数 = drain 读结果的流水延迟（典型 = PE latency + 1）。
        # acc_read(restore) 波前延后拍数 = 跟 chunk feed 到达 PE 对齐的延迟（与 drain 不同；默认同 drain）。
        self._drain_delay = drain_delay
        self._restore_delay = drain_delay if restore_delay is None else restore_delay
        self.reset()

    def issue(self, instr):
        pass

    # ------------------------------------------------------------------ #
    # 第二段：次态组合逻辑（纯组合，只写 _compute_state_next / _cnt_next）
    # ------------------------------------------------------------------ #
    def _next_compute_state(self, weight_available: bool, activ_available: bool):
        cs      = self.compute_state
        cnt     = self._cnt
        K, M, N = self._K, self.M, self.N

        # 下一 tile 的 weight/activation 都就绪
        avail = (((not self.shift_col) or weight_available) and
                 ((not self.shift_row) or activ_available))

        # 默认保持现态、计数器 +1，再按 case 覆盖（避免 latch）。cnt 每相位清零
        self._compute_state_next = cs
        self._cnt_next           = cnt + 1

        # --- 完整 5 状态；阈值固定，与 read/skew 无关 ---
        if cs == ComputeState.IDLE:
            self._cnt_next = 0
            if avail:
                self._compute_state_next = ComputeState.COMPUTE
        elif cs in self._FEED_STATES:                            # feed K 拍：cnt 0..K-1
            if cnt == K - 1:                                     # feed 边界：接上→SEAMLESS，否则→DRAIN
                self._compute_state_next = (
                    ComputeState.OVERLAP_SEAMLESS if avail else ComputeState.DRAIN)
                self._cnt_next = 0
        elif cs == ComputeState.DRAIN:                           # drain M+N-1 拍：cnt 0..M+N-2
            if avail:                                            # 迟到 tile drain 中途到达 → 带间隔喂
                self._compute_state_next = ComputeState.OVERLAP_GAP
                self._cnt_next = 0
            elif cnt == M + N - 2:
                self._compute_state_next = ComputeState.IDLE
                self._cnt_next = 0

        # --- 块边界事件（驱动 drain / overwrite / tile_id；流水与顺序统一）---
        nxt         = self._compute_state_next
        boundary    = cs in self._FEED_STATES and cnt == K - 1          # 某 chunk feed 末拍
        chunk_start = self._cnt_next == 0 and nxt in self._FEED_STATES  # 某 chunk feed 起拍
        restart     = cs not in self._FEED_STATES and nxt in self._FEED_STATES  # 从 IDLE/DRAIN 进 feed
        # 旧块完成 → drain：本 chunk 是块的最后一个（后面是新块 new_tile，或没有了 →DRAIN）
        self._block_end   = boundary and ((not avail) or self._new_tile)
        # 新输出块首 chunk 起 → tile_id+1 + overwrite(=0)
        self._block_start = self._new_tile and chunk_start
        # acc_read 注入：新块 overwrite(data=0) 或 续算 restore(data=partial，从非 feed 重启)
        self._psum_init   = self._block_start or (self._restore_en and restart)
        self._tile_id_next = self._tile_id + 1 if self._block_start else self._tile_id

        if self._compute_state_next != cs:
            logger.debug("[compute] cy=%d  %s → %s", self._cycle, cs.name, self._compute_state_next.name)

    # ------------------------------------------------------------------ #
    # 第三段：输出组合逻辑（寄存输出，按次态算 → 锁存后与状态同拍对齐）
    # ------------------------------------------------------------------ #
    def _update_load_signal(self):
        # read = 移位寄存器：把"本拍是否 feed"灌入 lane0，每拍向高 lane 传播一格（skew）。
        # 头部 ramp、drain 尾巴、重叠两 tile 的波前叠加都由它天然包办。
        feeding = self._compute_state_next in self._FEED_STATES
        self._read_sr_next = [feeding] + self._read_sr[:-1]
        self._read_weight_next     = self._read_sr_next[:self.N] if self.shift_col else [False] * self.N
        self._read_activation_next = self._read_sr_next[:self.M] if self.shift_row else [False] * self.M

    def _update_drain_signal(self):
        # acc(write)：反对角移位寄存器（长 M+N-1 + drain_delay）。每次 feed→DRAIN 往 d=0 注入一条
        # drain 波前，每拍向高 d 推进；输出读 [r+c+drain_delay]，整体延后 drain_delay 拍补偿流水延迟。
        # 多条波前可并存——同拍可写不同 PE（前一结果的尾 + 后一结果的头）。
        # 旧块完成（_block_end）→ 注入该块 tile_id 的 drain 波前（流水时与新块 overwrite 同步）
        inject = self._tile_id if self._block_end else None
        self._acc_sr_next = [inject] + self._acc_sr[:-1]
        D = self._drain_delay
        # acc[r][c] = 该 PE 本拍 drain 写入的 tile_id（None=不写）
        self._acc_next = [[self._acc_sr_next[r + c + D] for c in range(self.N)]
                          for r in range(self.M)]

    def _update_restore_signal(self):
        # acc_read：psum-init 波前。新块首 chunk(overwrite,data=0) 或 续算重启(restore,data=partial)。
        # _psum_init 已在次态逻辑里算好（含流水时 feed→feed 的新块起拍）。
        self._acc_read_sr_next = [self._psum_init] + self._acc_read_sr[:-1]
        D = self._restore_delay
        self._acc_read_next = [[self._acc_read_sr_next[r + c + D] for c in range(self.N)]
                               for r in range(self.M)]

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def update(self, cycle, weight_available: bool, activ_available: bool,
               restore_en: bool = False, new_tile: bool = False):
        self._cycle = cycle
        self._restore_en = restore_en   # 续算：读回上次 spill 的部分和
        self._new_tile   = new_tile     # 新输出块：tile_id+1 + psum overwrite(=0)
        if logger.isEnabledFor(logging.DEBUG):
            rw = ''.join(str(int(b)) for b in self.read_weight)
            ra = ''.join(str(int(b)) for b in self.read_activation)
            logger.debug("[ctrl] cy=%4d  %-16s cnt=%2d tid=%d  rw=%s ra=%s  wA=%d aA=%d re=%d nt=%d",
                         cycle, self.compute_state.name, self._cnt, self._tile_id, rw, ra,
                         int(weight_available), int(activ_available), int(restore_en), int(new_tile))
        # 第二段：次态组合逻辑（先算 _compute_state_next / _cnt_next）
        self._next_compute_state(weight_available, activ_available)
        # 第三段：输出组合逻辑（依赖 _compute_state_next）
        self._update_load_signal()
        self._update_drain_signal()
        self._update_restore_signal()

    def commit(self):
        # 第一段：时序，纯锁存 reg = reg_next（无任何逻辑）
        self.compute_state   = self._compute_state_next
        self._cnt            = self._cnt_next
        self._tile_id        = self._tile_id_next
        self._read_sr        = self._read_sr_next
        self.read_weight     = self._read_weight_next
        self.read_activation = self._read_activation_next
        self._acc_sr         = self._acc_sr_next
        self._acc_read_sr    = self._acc_read_sr_next
        self.acc             = self._acc_next
        self.acc_read        = self._acc_read_next

    def reset(self):
        self._cycle = 0  # 仅用于打印

        # 第一段寄存器 + 对应次态
        self._cnt                = 0
        self._cnt_next           = 0
        self._tile_id            = 0
        self._tile_id_next       = 0
        self.compute_state       = ComputeState.IDLE
        self._compute_state_next = ComputeState.IDLE
        # read 移位寄存器（长 max(M,N)）：feed 脉冲带 skew 传播
        self._read_sr              = [False] * max(self.M, self.N)
        self._read_sr_next         = [False] * max(self.M, self.N)
        self.read_weight           = [False] * self.N
        self._read_weight_next     = [False] * self.N
        self.read_activation       = [False] * self.M
        self._read_activation_next = [False] * self.M
        # drain-write SR 搬 tile_id（None=无波前）；restore SR 搬 bool。长 M+N-1+各自 delay
        DLEN_w = self.M + self.N - 1 + self._drain_delay
        DLEN_r = self.M + self.N - 1 + self._restore_delay
        self._acc_sr            = [None] * DLEN_w
        self._acc_sr_next       = [None] * DLEN_w
        self._acc_read_sr       = [False] * DLEN_r
        self._acc_read_sr_next  = [False] * DLEN_r
        self._restore_en = False   # 续算（由 update 传入）
        self._new_tile   = False   # 新输出块（由 update 传入）
        self._block_end  = False   # 本拍旧块完成（drain）
        self._block_start = False  # 本拍新块首 chunk 起（tile_id+1 + overwrite）
        self._psum_init  = False   # 本拍 acc_read 注入（overwrite/restore）
        # acc(write)[r][c]=写入的 tile_id 或 None；acc_read[r][c]=本拍是否 restore/overwrite
        self.acc            = [[None] * self.N for _ in range(self.M)]
        self._acc_next      = [[None] * self.N for _ in range(self.M)]
        self.acc_read       = [[False] * self.N for _ in range(self.M)]
        self._acc_read_next = [[False] * self.N for _ in range(self.M)]