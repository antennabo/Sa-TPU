import logging
import numpy as np
from .module import module
from .pe import pe

logger = logging.getLogger(__name__)


class spatial_array(module):
    """两段式脉动阵列，update 为总接口，靠静态拓扑 flag 支持 OS/WS/IS（详见 doc/SA_array_design.md）。

    静态拓扑 flag（构造定、整 run 不变 = 模式）：
      OS: is_shift_row=1, is_shift_col=1, acc_d=0, acc_l=0  (b 下流进 active, psum 原地累加)
      WS: is_shift_row=1, is_shift_col=0, acc_d=1, acc_l=0  (b 进 shadow 反压 FIFO, psum 下流)
      IS: is_shift_row=0, is_shift_col=1, acc_d=0, acc_l=1  (a 驻留, psum 左流)

    sa 每拍从上一拍快照 + 本拍边缘注入算出每个 PE 的输入，再调 pe.update：
      - a（激活）右流：a 与其 valid 同行右传（纯 valid，无反压）。
      - b（权重）：OS/IS 下流进 active（b_load=b_vld）；WS 进 shadow 反压 FIFO——
        每列算一条 ready 链（组合往上），填满即停、保持到 b_sw swap。
      - b_sw / acc_clr：[M] 左边缘注入、在 sa 内右推成对角图案。
    """

    def __init__(self, M, N, dtype_in=np.int8, dtype_acc=np.int32, latency=1,
                 is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0):
        super().__init__(dtype_state=dtype_acc)
        self.M = M
        self.N = N
        self.is_shift_col   = is_shift_col      # b 下流进 active（OS/IS）；=0 时 b 进 shadow（WS）
        self.is_shift_row   = is_shift_row      # a 右流
        self.is_shift_acc_d = is_shift_acc_d    # psum 下流
        self.is_shift_acc_l = is_shift_acc_l    # psum 左流
        is_b_buf = (not is_shift_col)           # WS：b 进 shadow → PE 启用双缓冲
        self.pes = [[pe(dtype_in=dtype_in, dtype_acc=dtype_acc, latency=latency, is_b_buf=is_b_buf)
                     for _ in range(N)] for _ in range(M)]
        self.reset()

    def update(self, a_data, a_vld, b_data, b_vld, acc_clr=None, output_sel=None, b_sw=None):
        # a_data[M]/a_vld[M]：左边缘逐行注入的激活 + valid（右流，valid 同行右传）
        # b_data[N]/b_vld[N]：上边缘逐列注入的权重 + valid（OS 进 active；WS 进 shadow 反压 FIFO）
        # acc_clr[M]/b_sw[M]：左边缘逐行注入的控制（右推成对角图案）；output_sel 预留(出口读出，见 §12)
        a_in, a_vin          = self._route_a(a_data, a_vld)
        b_in, b_vin, b_rdy   = self._route_b(b_data, b_vld)
        # b_sw 第 0 列也打 1 拍 FF（用 self._bsw_edge），跟 a 的 data_buf 第 0 列对齐
        bsw_g                = self._shift_right_reg_edge(self._bsw_grid, self._bsw_edge)
        self._bsw_edge_next  = [bool(b_sw[r]) if b_sw is not None else False for r in range(self.M)]
        clr_g                = self._shift_right(self._clr_grid, acc_clr)
        out_g                = self._shift_right(self._out_grid, output_sel)  # OS drain 读出波
        acc_in               = self._route_acc(clr_g)

        for r in range(self.M):
            for c in range(self.N):
                self.pes[r][c].update(a_in[r][c], a_vin[r][c],
                                      b_in[r][c], b_vin[r][c], b_rdy[r][c],
                                      acc_in[r][c], bool(bsw_g[r][c]))

        # OS drain mux：每列被点亮的 PE（≤1 个）的 psum → output[c]
        # 行地址不在这给（冗余）：由 controller 标量 output_sel 经 accum 内部 SR 传播（统一 update）
        self._output_next = [0] * self.N
        for c in range(self.N):
            rows = [r for r in range(self.M) if out_g[r][c]]
            assert len(rows) <= 1, f"列 {c} 同拍 drain {len(rows)} 个 PE（Gk<M）"
            if rows:
                self._output_next[c] = self.pes[rows[0]][c].state

        self._bsw_grid_next = bsw_g
        self._clr_grid_next = clr_g
        self._out_grid_next = out_g

    # ---- 数据路由 ----

    def _route_a(self, a_data, a_vld):
        """a 右流：左边缘灌 a_data[r]/a_vld[r]，其余取左邻 a/a_vld（valid 同行右传）。
        is_shift_row=0(IS) 则 a 原地。返回 (a_in[M][N], a_vld_in[M][N])。"""
        M, N = self.M, self.N
        a  = [[self.pes[r][c].a     for c in range(N)] for r in range(M)]
        av = [[self.pes[r][c].a_vld for c in range(N)] for r in range(M)]
        if not self.is_shift_row:
            return a, av
        a_in  = [[a_data[r] if c == 0 else a[r][c - 1] for c in range(N)] for r in range(M)]
        a_vin = [[bool(a_vld[r]) if c == 0 else av[r][c - 1] for c in range(N)] for r in range(M)]
        return a_in, a_vin

    def _route_b(self, b_data, b_vld):
        """每个 PE 的 b 输入 (b_in) + 握手 (b_vld, b_rdy)，pe 在 b_vld&b_rdy 时载入。
        OS/IS：active 下流，b_vld=b_vld[c]、b_rdy 恒真（无反压）。
        WS：shadow 反压 FIFO，每列算 ready 链——ready_k = 空 or 下面 ready；上游有效 in_vld
            = 顶为 b_vld[c]、其余为上邻 b_buf_vld；b_in = 顶为 b_data[c]、其余为上邻 b_buf。
        返回 (b_in[M][N], b_vld_g[M][N], b_rdy_g[M][N])。"""
        M, N = self.M, self.N
        if self.is_shift_col:                       # OS/IS：active 下流
            b = [[self.pes[r][c].b for c in range(N)] for r in range(M)]
            b_in    = [[b_data[c] if r == 0 else b[r - 1][c] for c in range(N)] for r in range(M)]
            b_vld_g = [[bool(b_vld[c]) for c in range(N)] for _ in range(M)]
            b_rdy_g = [[True for _ in range(N)] for _ in range(M)]
            return b_in, b_vld_g, b_rdy_g
        # WS：shadow 反压 FIFO（每列独立）
        bb = [[self.pes[r][c].b_buf     for c in range(N)] for r in range(M)]
        bv = [[self.pes[r][c].b_buf_vld for c in range(N)] for r in range(M)]
        b_in    = [[0] * N for _ in range(M)]
        b_vld_g = [[False] * N for _ in range(M)]
        b_rdy_g = [[False] * N for _ in range(M)]
        for c in range(N):
            ready = [False] * M
            ready[M - 1] = (not bv[M - 1][c])
            for k in range(M - 2, -1, -1):
                ready[k] = (not bv[k][c]) or ready[k + 1]
            for k in range(M):
                b_vld_g[k][c] = bool(b_vld[c]) if k == 0 else bv[k - 1][c]
                b_in[k][c]    = b_data[c]      if k == 0 else bb[k - 1][c]
                b_rdy_g[k][c] = ready[k]
        return b_in, b_vld_g, b_rdy_g

    def _route_acc(self, clr_g):
        """acc 源优先级：acc_clr 命中→0 > 下移入 > 左移入 > 原地。返回 [M][N]。"""
        M, N = self.M, self.N
        Z = self.dtype_state(0)
        s = [[self.pes[r][c].state for c in range(N)] for r in range(M)]
        out = [[Z] * N for _ in range(M)]
        for r in range(M):
            for c in range(N):
                if clr_g[r][c]:
                    out[r][c] = Z
                elif self.is_shift_acc_d:
                    out[r][c] = s[r - 1][c] if r > 0 else Z
                elif self.is_shift_acc_l:
                    out[r][c] = s[r][c + 1] if c < N - 1 else Z
                else:
                    out[r][c] = s[r][c]
        return out

    # ---- 控制波传播（[M] 左边缘注入 + 逐拍右推）----

    def _shift_right(self, grid, inject):
        """右推一格：col0 灌 inject[r]（None→False，但在飞波继续右推），其余取上一拍左邻 grid[r][c-1]。"""
        M, N = self.M, self.N
        edge = lambda r: bool(inject[r]) if inject is not None else False
        return [[edge(r) if c == 0 else grid[r][c - 1] for c in range(N)] for r in range(M)]

    def _shift_right_reg_edge(self, grid, edge_reg):
        """右推一格 + 第 0 列也是 registered（用 edge_reg = 上拍 commit 的 inject）：
        col 0 = edge_reg[r]，col c+1 = grid[r][c]（上拍）。下拍 inject 通过 _bsw_edge_next 注入。
        这样 col 0 也有 1 拍 FF，跟 RTL 的 b_sw_grid[row][0] always_ff 对齐。"""
        M, N = self.M, self.N
        return [[edge_reg[r] if c == 0 else grid[r][c - 1] for c in range(N)] for r in range(M)]

    def commit(self):
        for row in self.pes:
            for P in row:
                P.commit()
        self._bsw_grid = self._bsw_grid_next
        self._bsw_edge = self._bsw_edge_next      # 第 0 列 edge FF 也 commit
        self._clr_grid = self._clr_grid_next
        self._out_grid = self._out_grid_next
        self.output  = self._output_next

    def reset(self):
        for row in self.pes:
            for P in row:
                P.reset()
        F = lambda: [[False] * self.N for _ in range(self.M)]
        self._bsw_grid = F(); self._bsw_grid_next = F()
        self._bsw_edge = [False] * self.M         # b_sw 第 0 列 edge FF（跟 RTL b_sw_grid[row][0] 对齐）
        self._bsw_edge_next = [False] * self.M
        self._clr_grid = F(); self._clr_grid_next = F()
        self._out_grid = F(); self._out_grid_next = F()
        self.output  = [0] * self.N; self._output_next  = [0] * self.N

    @property
    def data(self):
        """当前各 PE 的 psum（[M][N]）。OS 校验整阵列；WS 读底行 data[M-1]。"""
        return [[self.pes[r][c].state for c in range(self.N)] for r in range(self.M)]

    @property
    def shadow_full(self):
        """WS：每列 shadow 反压 FIFO 是否填满（整列 b_buf_vld 全 True）。
        给驱动算 weight_available = all(shadow_full)（对 wb 的每列反压全拉起）。"""
        return [all(self.pes[k][c].b_buf_vld for k in range(self.M)) for c in range(self.N)]

    def get_result(self) -> np.ndarray:
        return np.array(self.data)
