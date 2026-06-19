from simulator.cycle.sim_model.accumulator import Accumulator


class TestAccumulator:
    """统一 update：显式 vld + 标量地址注入 → 内部 per-column 传播 → 写/累加。见 doc/accumulator_design.md。"""

    def tick(self, a, values, vld, row=0, slot=0, add=False):
        a.update(values, vld, row, slot, add)
        a.commit()

    def test_invalid_no_write(self):
        a = Accumulator(4, 3, cap_delay=0)
        self.tick(a, [9, 9, 9], vld=False, row=1, slot=0)     # vld=False → 不写（地址 don't-care）
        assert a.get_tile(0) == [[0] * 3 for _ in range(4)]

    def test_percol_skew(self):
        # cap_delay=0：列 c 延 c 拍。注入 row=2 再 row=3，结果沿对角线落盘
        a = Accumulator(4, 3, cap_delay=0)
        self.tick(a, [10, 20, 30], vld=True, row=2, slot=1)   # col0 ← row2
        self.tick(a, [11, 21, 31], vld=True, row=3, slot=1)   # col0 ← row3, col1 ← row2
        self.tick(a, [12, 22, 32], vld=False)                 # col1 ← row3, col2 ← row2
        self.tick(a, [13, 23, 33], vld=False)                 # col2 ← row3
        tile = a.get_tile(1)
        assert tile[2] == [10, 21, 32]                        # row2 的对角值
        assert tile[3] == [11, 22, 33]                        # row3 的对角值

    def test_accumulate(self):
        a = Accumulator(2, 1, cap_delay=0)
        self.tick(a, [5], vld=True, row=0, slot=0, add=False) # 覆写
        assert a.get_tile(0)[0][0] == 5
        self.tick(a, [3], vld=True, row=0, slot=0, add=True)  # 累加
        assert a.get_tile(0)[0][0] == 8

    def test_overwrite_vs_newslot(self):
        a = Accumulator(2, 1, cap_delay=0)
        self.tick(a, [7], vld=True, row=1, slot=0)            # 槽0
        self.tick(a, [4], vld=True, row=1, slot=2)            # 槽2（互不干扰）
        assert a.get_tile(0)[1][0] == 7
        assert a.get_tile(2)[1][0] == 4

    def test_cap_delay(self):
        # cap_delay=2：列 0 也要延 2 拍才落盘
        a = Accumulator(2, 1, cap_delay=2)
        self.tick(a, [1], vld=True, row=0, slot=0)            # 注入，col0 读 sr[0+2]，还没到
        assert a.get_tile(0)[0][0] == 0
        self.tick(a, [2], vld=False)
        assert a.get_tile(0)[0][0] == 0
        self.tick(a, [3], vld=False)                          # 此拍 col0 读到最初注入 → 写当前 values[0]=3
        assert a.get_tile(0)[0][0] == 3
