import logging
from .module import module

logger = logging.getLogger(__name__)


class Accumulator(module):
    """输出 tile 累加器。_mem[tile_id][row][col] 存各 PE drain 出的 psum 快照。
    对外只有 update（同拍 restore 读 + drain 写，须在 sa.update 之前调）：

      update(data, write_mask, read_mask)
        data       -- sa.data [M][N]（已 commit 的 PE psum）
        write_mask -- ctrl.acc [M][N]：=1 的格子把 data[r][c] 写进 mem（覆盖；commit 落盘）
        read_mask  -- ctrl.acc_read [M][N]：=1 的格子从 mem 读出 → self.restore_data（给 sa 当 restore 输入）
    """

    NUM_TILES = 16

    def __init__(self, num_rows: int, num_cols: int):
        super().__init__()
        self.M = num_rows
        self.N = num_cols
        self.tile_id = 0           # 单 tensor：先固定 0（后续由指令驱动）
        self.reset()

    def reset(self):
        self._mem = [[[0] * self.N for _ in range(self.M)] for _ in range(self.NUM_TILES)]
        self.restore_data = [[0] * self.N for _ in range(self.M)]
        self._pending = []         # 本拍要落盘的 drain 写 (r, c, val)

    def update(self, data, write_mask, read_mask):
        # restore 读：read_mask 的格子读回（按 self.tile_id；多块续算再细化）→ restore_data
        mem = self._mem[self.tile_id]
        self.restore_data = [[mem[r][c] if read_mask[r][c] else 0
                              for c in range(self.N)] for r in range(self.M)]
        # drain 写：write_mask[r][c] = 目标 tile_id 或 None；暂存，commit 落盘（覆盖）
        self._pending = [(write_mask[r][c], r, c, data[r][c])
                         for r in range(self.M) for c in range(self.N)
                         if write_mask[r][c] is not None]

    def commit(self):
        for tid, r, c, v in self._pending:
            self._mem[tid][r][c] = v
        self._pending = []

    def get_tile(self, tile_id: int):
        return self._mem[tile_id]
