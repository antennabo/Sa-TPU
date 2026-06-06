import logging
import numpy as np
from .module import module
from .pe import pe

logger = logging.getLogger(__name__)


class spatial_array(module):
    """两段式脉动阵列，update 为总接口，靠 6 个 shift 信号支持 OS/WS/IS。

    数据流：row_data 从左边缘进、沿行向右；col_data 从上边缘进、沿列向下。
      OS: is_shift_row=1, is_shift_col=1, acc_d=0, acc_l=0  (psum 原地累加)
      WS: is_shift_row=1, is_shift_col=0, acc_d=1, acc_l=0  (weight 原地, psum 下流)
      IS: is_shift_row=0, is_shift_col=1, acc_d=0, acc_l=1  (activation 原地, psum 左流)
    sa 只做路由：按信号从快照算每个 PE 的 a_in/b_in/acc_in，再调 pe.update。
    """

    def __init__(self, M, N, dtype_in=np.int8, dtype_acc=np.int32, latency=1):
        super().__init__(dtype_state=dtype_acc)
        self.M = M
        self.N = N
        self.pes = [[pe(dtype_in=dtype_in, dtype_acc=dtype_acc, latency=latency)
                     for _ in range(N)] for _ in range(M)]

    def update(self, row_data, col_data,
               is_shift_col, is_shift_row, is_shift_acc_d, is_shift_acc_l,
               restore_data, restore_mask):
        a_in   = self._route_a(row_data, is_shift_row)
        b_in   = self._route_b(col_data, is_shift_col)
        acc_in = self._route_acc(restore_data, restore_mask, is_shift_acc_d, is_shift_acc_l)
        for r in range(self.M):
            for c in range(self.N):
                self.pes[r][c].update(a_in[r][c], b_in[r][c], acc_in[r][c])

    def _route_a(self, row_data, is_shift_row):
        """a 右流：每行左边缘灌 row_data[r]，其余取左邻；不移则原地。返回 [M][N]。"""
        M, N = self.M, self.N
        a = [[self.pes[r][c].a for c in range(N)] for r in range(M)]
        if not is_shift_row:
            return a
        return [[row_data[r] if c == 0 else a[r][c - 1] for c in range(N)] for r in range(M)]

    def _route_b(self, col_data, is_shift_col):
        """b 下流：每列上边缘灌 col_data[c]，其余取上邻；不移则原地。返回 [M][N]。"""
        M, N = self.M, self.N
        b = [[self.pes[r][c].b for c in range(N)] for r in range(M)]
        if not is_shift_col:
            return b
        return [[col_data[c] if r == 0 else b[r - 1][c] for c in range(N)] for r in range(M)]

    def _route_acc(self, restore_data, restore_mask, is_shift_acc_d, is_shift_acc_l):
        """acc 源选择：restore > 下移入 > 左移入 > 原地。返回 [M][N]。"""
        M, N = self.M, self.N
        Z = self.dtype_state(0)
        s = [[self.pes[r][c].state for c in range(N)] for r in range(M)]
        out = [[Z] * N for _ in range(M)]
        for r in range(M):
            for c in range(N):
                if restore_mask[r][c]:
                    out[r][c] = restore_data[r][c]
                elif is_shift_acc_d:
                    out[r][c] = s[r - 1][c] if r > 0 else Z
                elif is_shift_acc_l:
                    out[r][c] = s[r][c + 1] if c < N - 1 else Z
                else:
                    out[r][c] = s[r][c]
        return out

    def commit(self):
        for row in self.pes:
            for P in row:
                P.commit()

    def reset(self):
        for row in self.pes:
            for P in row:
                P.reset()

    @property
    def data(self):
        """当前各 PE 的 psum（[M][N]），供 accumulator 按 drain mask 读取。"""
        return [[self.pes[r][c].state for c in range(self.N)] for r in range(self.M)]

    def get_result(self) -> np.ndarray:
        return np.array(self.data)
