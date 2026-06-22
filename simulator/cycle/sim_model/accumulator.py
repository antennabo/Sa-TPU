import logging
from .module import module

logger = logging.getLogger(__name__)


class Accumulator(module):
    """输出累加器。_mem[slot][row][col] 存输出 tile。

    统一接口 update（OS drain / WS capture 同一种写，见 doc/accumulator_design.md §6）：

      update(values, vld, row, slot, add):
        values[N] -- sa 出的 per-column 结果（OS=drain 总线 / WS=底行 psum sa.data[AR-1]）
        vld       -- 标量：这拍注入的地址有效否（硬件 valid；为假时 row/slot/add 是 don't-care）
        row       -- 标量：写输出 tile 第几行
        slot      -- 标量：写哪个槽（_mem 第一维）
        add       -- 标量：False=覆写 / True=累加（+= 旧值）

    vld/row/slot/add 一起标量注入；per-column 由内部传播 SR 铺开（§8）：注入端进 (vld,row,slot,add)，
    列 c 延 (c + cap_delay) 拍读出该列的 (vld,row,slot,add)，vld 为真才写 _mem[slot][row][c]（add 则 +=）。
    cap_delay 默认 0：调用方（controller）已经把信号对齐到 col 0 psum 到达时刻，accumulator 只做
    column-stagger（列 c 延 c 拍）。需要 accumulator 同时做 pipeline 对齐的场景（sa-only dump 等）
    可以传 cap_delay > 0，列 0 也延 cap_delay 拍。
    """

    NUM_TILES = 16

    def __init__(self, num_rows: int, num_cols: int, cap_delay: int = 0):
        super().__init__()
        self.M = num_rows
        self.N = num_cols
        self._cap_delay = cap_delay
        self.reset()

    def reset(self):
        self._mem = [[[0] * self.N for _ in range(self.M)] for _ in range(self.NUM_TILES)]
        self._pending = []                                   # 本拍待落盘 (slot, row, col, val)
        L = self.N + self._cap_delay
        self._sr      = [(False, 0, 0, False)] * L           # per-column 地址传播 SR：(vld,row,slot,add)
        self._sr_next = [(False, 0, 0, False)] * L

    def update(self, values, vld, row, slot, add):
        # 标量 (vld,row,slot,add) 注入端、逐拍右移；列 c 延 (c + cap_delay) 拍读出本列地址，vld 真才写
        self._sr_next = [(bool(vld), row, slot, bool(add))] + self._sr[:-1]
        self._pending = []
        for c in range(self.N):
            v, r, s, ad = self._sr_next[c + self._cap_delay]
            if v:
                base = self._mem[s][r][c] if ad else 0
                self._pending.append((s, r, c, base + values[c]))

    def commit(self):
        for slot, r, c, v in self._pending:
            self._mem[slot][r][c] = v
        self._pending = []
        self._sr = self._sr_next

    def get_tile(self, slot: int):
        return self._mem[slot]
