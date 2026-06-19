import random
from simulator.cycle.sim_model.pe import pe


class TestPE:
    """单个 PE：MAC、a valid、active/shadow 载入(valid&ready 握手)、b_sw 翻转、latency 1/2。"""

    def tick(self, dut, **kw):
        dut.update(**kw)
        dut.commit()

    def test_reset(self):
        dut = pe()
        assert dut.a == 0 and dut.b == 0 and dut.b_buf == 0 and dut.state == 0
        assert dut.a_vld is False and dut.b_buf_vld is False

    def test_a_valid_gate(self):
        dut = pe()
        self.tick(dut, a_in=5, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=0)
        assert dut.a == 0 and dut.a_vld is False        # a_vld=False → 保持
        self.tick(dut, a_in=5, a_vld=True, b_in=0, b_vld=False, b_rdy=True, acc_in=0)
        assert dut.a == 5 and dut.a_vld is True          # a_vld=True → 载入，valid 右传

    def test_shadow_load(self):
        dut = pe(is_b_buf=True)                          # WS：b_in 进 shadow，active 保持
        dut.b = 3
        self.tick(dut, a_in=0, a_vld=False, b_in=7, b_vld=True, b_rdy=True, acc_in=0)
        assert dut.b == 3 and dut.b_buf == 7 and dut.b_buf_vld is True
        self.tick(dut, a_in=0, a_vld=False, b_in=9, b_vld=False, b_rdy=True, acc_in=0)
        assert dut.b_buf == 7 and dut.b_buf_vld is True  # b_vld=False → shadow 保持

    def test_backpressure_holds(self):
        dut = pe(is_b_buf=True)                          # b_vld 真但 b_rdy 假（反压）→ 不载入
        dut.b_buf = 5; dut.b_buf_vld = True
        self.tick(dut, a_in=0, a_vld=False, b_in=9, b_vld=True, b_rdy=False, acc_in=0)
        assert dut.b_buf == 5 and dut.b_buf_vld is True

    def test_active_load(self):
        dut = pe(is_b_buf=False)                         # OS/IS：b_in 进 active，b_rdy 恒真
        self.tick(dut, a_in=0, a_vld=False, b_in=7, b_vld=True, b_rdy=True, acc_in=0)
        assert dut.b == 7
        self.tick(dut, a_in=0, a_vld=False, b_in=9, b_vld=False, b_rdy=True, acc_in=0)
        assert dut.b == 7                                # b_vld=False → 保持

    def test_switch_clears_shadow_valid(self):
        dut = pe(is_b_buf=True)
        dut.b = 3; dut.b_buf = 9; dut.b_buf_vld = True
        self.tick(dut, a_in=0, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=0, b_sw=True)
        assert dut.b == 9 and dut.b_buf == 3 and dut.b_buf_vld is False  # swap 清 shadow valid

    def test_commit_required(self):
        dut = pe()
        dut.update(a_in=5, a_vld=True, b_in=0, b_vld=False, b_rdy=True, acc_in=0)
        assert dut.a == 0
        dut.commit()
        assert dut.a == 5

    def test_mac_latency1(self):
        dut = pe(latency=1)
        dut.a = 2; dut.b = 3
        self.tick(dut, a_in=0, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=10)
        assert dut.state == 16

    def test_mac_latency2(self):
        dut = pe(latency=2)
        dut.a = 2; dut.b = 3
        self.tick(dut, a_in=0, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=10)
        assert dut.state == 10 and dut.mult == 6
        self.tick(dut, a_in=0, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=20)
        assert dut.state == 26

    def test_random_mac(self):
        dut = pe(latency=1)
        for _ in range(1000):
            a = random.randint(-128, 127); b = random.randint(-128, 127)
            acc = random.randint(-10000, 10000)
            dut.a = a; dut.b = b
            self.tick(dut, a_in=0, a_vld=False, b_in=0, b_vld=False, b_rdy=True, acc_in=acc)
            assert dut.state == acc + a * b
