from simulator.cycle.sim_model.commonbuf import CommonBuf


class TestCommonBuf:
    """CommonBuf：写口存当前页、标量 feed → 内部 lane 传播生成 skew（同 fifo）、读指针自加、
    封顶 tile_num*K 归 0 复用、ping-pong 切页。见 doc/common_buf_design.md。
    """

    def tick(self, b, wdata, feed, tile_num=1):
        b.update(wdata, feed, tile_num)
        b.commit()

    def preload(self, b, tile):
        """tile = K 条深度向量 [K][N]；逐拍写入（feed=False，不读）。"""
        for vec in tile:
            self.tick(b, vec, False)

    def test_scalar_feed_generates_skew(self):
        # 与 CommonFIFO 同款 skew：lane c 延 c 拍点亮，按深度递增
        b = CommonBuf(N=3, K=3)
        tile = [[0 + d, 10 + d, 20 + d] for d in range(3)]   # [K][N]
        self.preload(b, tile)
        outs = []
        for _ in range(b.K + b.N):
            self.tick(b, None, True)
            outs.append(list(b.data))
        # lane0 立刻吐深度 0,1,2
        assert outs[0][0] == 0 and outs[1][0] == 1 and outs[2][0] == 2
        # lane1 延 1 拍
        assert outs[0][1] == 0
        assert outs[1][1] == 10 and outs[2][1] == 11 and outs[3][1] == 12
        # lane2 延 2 拍
        assert outs[0][2] == 0 and outs[1][2] == 0
        assert outs[2][2] == 20 and outs[3][2] == 21 and outs[4][2] == 22

    def test_feed_low_outputs_zero(self):
        b = CommonBuf(N=2, K=2)
        self.preload(b, [[1, 2], [3, 4]])
        self.tick(b, None, False)             # feed=False → 全 0、不读
        assert b.data == [0, 0]
        self.tick(b, None, True)              # feed=True → lane0 读指针处取数
        assert b.data[0] == 1

    def test_pointer_wraps_at_tile_num_K(self):
        # 单 tile（tile_num=1, K=2）：lane0 连读，指针自加到 2 归 0 → 重读同一份（自动复用）
        b = CommonBuf(N=1, K=2)
        self.preload(b, [[5], [6]])           # _buf[page][0] = [5, 6]
        seen = []
        for _ in range(5):                    # 读 5 拍，应循环 5,6,5,6,5
            self.tick(b, None, True, tile_num=1)
            seen.append(b.data[0])
        assert seen == [5, 6, 5, 6, 5]
        assert b._ptr[0] == 1                 # 5 拍后指针回到 1（0→1→0→1→0→1）

    def test_tile_num_two_spans_both_tiles(self):
        # tile_num=2, K=2：封顶 4，指针扫两个 tile 的全部行再归 0
        b = CommonBuf(N=1, K=2)
        self.preload(b, [[10], [11], [20], [21]])   # 两个 tile：[10,11] | [20,21]
        seen = []
        for _ in range(6):
            self.tick(b, None, True, tile_num=2)
            seen.append(b.data[0])
        assert seen == [10, 11, 20, 21, 10, 11]     # 扫满 4 行后归 0 重来

    def test_switch_page_isolates_data(self):
        # ping-pong：page0 写一份、切页 page1 写另一份；切回 page0 数据仍在（双缓冲隔离）
        b = CommonBuf(N=1, K=1)
        self.tick(b, [100], False)            # page0 存 100
        b.switch_page()
        self.tick(b, [200], False)            # page1 存 200
        self.tick(b, None, True, tile_num=1)  # 读 page1
        assert b.data[0] == 200
        b.switch_page()                       # 切回 page0（读指针归 0）
        self.tick(b, None, True, tile_num=1)
        assert b.data[0] == 100               # page0 数据未被 page1 覆盖

    def test_vld_marks_lit_lanes(self):
        b = CommonBuf(N=2, K=1)
        self.preload(b, [[7, 8]])
        self.tick(b, None, True)              # lane0 点亮取数
        assert b.vld == [True, False] and b.data[0] == 7
        self.tick(b, None, False)             # feed 落，但脉冲右推 → lane1 这拍点亮
        assert b.vld == [False, True]
