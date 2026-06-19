from simulator.cycle.sim_model.commonfifo import CommonFIFO


class TestCommonFIFO:
    """CommonFIFO：DMA 写攒 tile、标量 feed → 内部 lane 传播生成 skew、avail 握手。
    见 doc/weight_fifo_design.md。
    """

    def tick(self, f, wdata, feed):
        f.update(wdata, feed)
        f.commit()

    def preload(self, f, tile):
        """tile = K 条深度向量 [K][N]；逐拍 DMA 写入（feed=False，不读）。"""
        for vec in tile:
            self.tick(f, vec, False)

    def test_avail_after_full_tile(self):
        f = CommonFIFO(N=3, K=2)
        assert not f.avail
        self.tick(f, [1, 2, 3], False)        # 写第 1 条（tile 未满）
        assert not f.avail
        self.tick(f, [4, 5, 6], False)        # 写第 2 条 → 攒满 1 个 tile
        assert f.avail

    def test_scalar_feed_generates_skew(self):
        # N=3, K=3：tile 各 lane 的深度序列 = 列号*10 + 深度
        # lane c 深度 d 的值 = c*10 + d
        f = CommonFIFO(N=3, K=3)
        tile = [[0 + d, 10 + d, 20 + d] for d in range(3)]   # [K][N]
        self.preload(f, tile)
        assert f.avail

        # 标量 feed 拉高 K 拍：lane c 应延 c 拍才开始吐，且按深度递增 → skew
        outs = []
        for _ in range(3 + 3):                # 多跑几拍看完整 skew
            self.tick(f, None, True)
            outs.append(list(f.data))

        # lane0 立刻吐：深度 0,1,2 在第 0,1,2 拍
        assert outs[0][0] == 0 and outs[1][0] == 1 and outs[2][0] == 2
        # lane1 延 1 拍：第 0 拍还没点亮(=0)，第 1,2,3 拍吐 10,11,12
        assert outs[0][1] == 0
        assert outs[1][1] == 10 and outs[2][1] == 11 and outs[3][1] == 12
        # lane2 延 2 拍：第 0,1 拍=0，第 2,3,4 拍吐 20,21,22
        assert outs[0][2] == 0 and outs[1][2] == 0
        assert outs[2][2] == 20 and outs[3][2] == 21 and outs[4][2] == 22

    def test_feed_low_outputs_zero(self):
        f = CommonFIFO(N=2, K=2)
        self.preload(f, [[1, 2], [3, 4]])
        self.tick(f, None, False)             # feed=False → 全 0、不弹
        assert f.data == [0, 0]
        self.tick(f, None, True)              # feed=True → lane0 吐队头
        assert f.data[0] == 1

    def test_started_consumes_avail(self):
        f = CommonFIFO(N=2, K=2)
        self.preload(f, [[1, 2], [3, 4]])
        assert f.avail
        self.tick(f, None, True)              # lane0 弹出 tile-head → _started+1
        assert not f.avail                    # 已开喂 → avail 落
