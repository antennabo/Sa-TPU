from simulator.cycle.sim_model.commonbuf import CommonBuf


class TestCommonBuf:
    """CommonBuf：纯被动 lane buffer，外部驱动 (wr_en, wr_addr, wr_data, rd_en, rd_addr)。
    读出 data/vld 寄存 1 拍：本拍 update 读 → 当拍 commit 后即出。"""

    def tick(self, b, wr_en, wr_addr, wr_data, rd_en, rd_addr):
        b.update(wr_en, wr_addr, wr_data, rd_en, rd_addr)
        b.commit()

    def test_write_then_read_back(self):
        b = CommonBuf(N=2, DEPTH=4)
        N = b.N
        # 先写：lane0 addr=1 写 100，lane1 addr=2 写 200
        self.tick(b,
                  wr_en=[True, True], wr_addr=[1, 2], wr_data=[100, 200],
                  rd_en=[False] * N, rd_addr=[0] * N)
        # 再读
        self.tick(b,
                  wr_en=[False] * N, wr_addr=[0] * N, wr_data=[0] * N,
                  rd_en=[True, True], rd_addr=[1, 2])
        assert b.data == [100, 200]
        assert b.vld  == [True, True]

    def test_rd_en_low_outputs_zero_and_invalid(self):
        b = CommonBuf(N=2, DEPTH=2)
        N = b.N
        self.tick(b, [True, True], [0, 0], [9, 9], [False] * N, [0] * N)
        # rd_en 全 0：data/vld 都该是 0/False
        self.tick(b, [False] * N, [0] * N, [0] * N, [False, False], [0, 0])
        assert b.data == [0, 0]
        assert b.vld  == [False, False]

    def test_lanes_are_independent(self):
        b = CommonBuf(N=3, DEPTH=4)
        N = b.N
        # 写：lane0 addr=0=10、lane1 addr=1=11、lane2 addr=2=12
        self.tick(b,
                  wr_en=[True] * N, wr_addr=[0, 1, 2], wr_data=[10, 11, 12],
                  rd_en=[False] * N, rd_addr=[0] * N)
        # 只读 lane1
        self.tick(b,
                  wr_en=[False] * N, wr_addr=[0] * N, wr_data=[0] * N,
                  rd_en=[False, True, False], rd_addr=[0, 1, 0])
        assert b.vld == [False, True, False]
        assert b.data[1] == 11

    def test_overwrite_same_address(self):
        b = CommonBuf(N=1, DEPTH=2)
        self.tick(b, [True], [0], [7], [False], [0])
        self.tick(b, [True], [0], [99], [False], [0])      # 覆写
        self.tick(b, [False], [0], [0], [True], [0])
        assert b.data == [99]

    def test_two_writes_then_read_alternating(self):
        b = CommonBuf(N=1, DEPTH=4)
        # 写入 addr 0..3 = 0,10,20,30，逐拍
        for a, v in [(0, 0), (1, 10), (2, 20), (3, 30)]:
            self.tick(b, [True], [a], [v], [False], [0])
        seen = []
        for a in [3, 1, 2, 0]:                              # 任意顺序读
            self.tick(b, [False], [0], [0], [True], [a])
            seen.append(b.data[0])
        assert seen == [30, 10, 20, 0]
