from simulator.cycle.sim_model.controller import Controller, WSState


class TestControllerWS:
    """WS controller 信号结构单测（目标接口）。

    校验【结构/相对时序/门控】，不校验精确偏移与 accum==A·B——
    后者要 WS 端到端（capture 下游传播 / 输出存储层）建好后再钉死（见 doc/controller_design.md §14）。
    AR=M=2(K段), AC=N=2(N块), F=K=3(输出行走时间), latency=1。
    """

    def tick(self, ctrl, cy, wA, aA, **kw):
        ctrl.update(cy, wA, aA, **kw)
        ctrl.commit()

    def make(self):
        return Controller(2, 2, 3, mode="WS", latency=1)

    def test_no_legacy_attrs(self):
        c = self.make()
        # 目标输出存在
        assert hasattr(c, "b_sw") and hasattr(c, "m") and hasattr(c, "wr_tile")
        # 旧字段已移除
        assert not hasattr(c, "w_switch")
        assert not hasattr(c, "shadow_load")
        assert not hasattr(c, "ws_capture")
        # feed 改标量；read mask 已移除（skew 移到下游 fifo/buf）
        assert hasattr(c, "feed") and c.feed is False
        assert not hasattr(c, "read_weight")
        assert not hasattr(c, "read_activation")
        assert len(c.b_sw) == c.M                      # b_sw 是 [M] 不是 [M][N]
        assert all(b is False for b in c.b_sw)
        assert c.m is None

    def test_wload_gated_by_weight_available(self):
        c = self.make()
        self.tick(c, 0, wA=True, aA=True)              # IDLE → WLOAD
        assert c.ws_state == WSState.WLOAD
        self.tick(c, 1, wA=False, aA=True)             # 反压未全拉起 → 停在 WLOAD
        assert c.ws_state == WSState.WLOAD
        self.tick(c, 2, wA=False, aA=True)
        assert c.ws_state == WSState.WLOAD
        self.tick(c, 3, wA=True, aA=True)              # shadow 满 → STREAM
        assert c.ws_state == WSState.STREAM

    def test_cold_start_signals(self):
        c = self.make()
        TAG = 5
        # cy0: IDLE → WLOAD
        self.tick(c, 0, wA=True, aA=True, tag=TAG)
        assert c.ws_state == WSState.WLOAD
        assert all(b is False for b in c.b_sw)
        assert c.m is None
        # cy1: WLOAD → STREAM（冷启动恒切一次）：b_sw 行0 起、capture 出第 0 行
        self.tick(c, 1, wA=True, aA=True, tag=TAG)
        assert c.ws_state == WSState.STREAM
        assert c.b_sw == [True, False]                 # 行 skew 起点：只点行 0
        assert c.feed is True                          # STREAM 期间 feed 标量高（skew 在下游 buf）
        assert c.m == 0 and c.wr_tile == TAG           # wr_tile = tag（同 OS 语义）
        # cy2: STREAM 续，b_sw 行 skew 推进到行 1、capture 第 1 行
        self.tick(c, 2, wA=True, aA=True, tag=TAG)
        assert c.b_sw == [False, True]                 # 换权重波按行下移一格
        assert c.feed is True
        assert c.m == 1 and c.wr_tile == TAG
        # cy3: STREAM 末行（F=3：行 0..2），capture 第 2 行
        self.tick(c, 3, wA=True, aA=True, tag=TAG)
        assert c.m == 2 and c.wr_tile == TAG
        # cy4: feed 末拍边界、无下一 tile → DRAIN；capture 注入停（None，尾巴交下游）
        self.tick(c, 4, wA=False, aA=False, tag=TAG)
        assert c.ws_state == WSState.DRAIN
        assert c.m is None

    def test_controller_emits_scalar_feed_only(self):
        c = self.make()
        # WS：controller 对喂料只发标量 feed（无 per-lane mask；权重弹出走 wb↔sa 自握手）
        seq = [(True, True), (True, True), (True, True), (True, True), (False, False)]
        for cy, (wA, aA) in enumerate(seq):
            self.tick(c, cy, wA=wA, aA=aA, tag=5)
            assert isinstance(c.feed, bool)
            assert not hasattr(c, "read_weight")
