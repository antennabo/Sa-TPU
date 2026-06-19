import numpy as np
from .module import module


class pe(module):
    """单个 PE：恒用 active 权重 b 做乘加。

    a 与其 valid 同行右传（纯 valid，无反压）。
    载入握手：b_vld（上游有效）& b_rdy（本格 ready）才载入。is_b_buf 是构造参数（= Verilog parameter）：
      - False（OS/IS）：b_in 逐拍载入 active b（b_rdy 恒真，不反压）。
      - True （WS）   ：active 驻留；b_buf 是 valid/ready 反压式影子 FIFO 的一格——
                        b_rdy 由 sa 的每列 ready 链给，b_vld&b_rdy 时载入并置 b_buf_vld；b_sw 把
                        active↔shadow 一拍互换并清 b_buf_vld（shadow 让出、可再填）。
    """

    def __init__(self, dtype_in=np.int8, dtype_acc=np.int32, latency=2, is_b_buf=True):
        super().__init__(dtype_state=dtype_acc)
        self.dtype_in = dtype_in
        self.latency  = latency
        self.is_b_buf = is_b_buf
        self.reset()

    def update(self, a_in, a_vld, b_in, b_vld, b_rdy, acc_in, b_sw=False):
        prod = self.dtype_state(self.a) * self.dtype_state(self.b)  # 恒用 active 权重 b
        if self.latency == 2:
            self.state_next = self.dtype_state(acc_in) + self.mult  # 累加上一拍乘积
            self.mult_next  = prod
        else:
            self.state_next = self.dtype_state(acc_in) + prod

        self.a_next     = self.dtype_in(a_in) if a_vld else self.a  # a 与其 valid 右传
        self.a_vld_next = bool(a_vld)

        b_load = b_vld and b_rdy                    # 握手：上游有效 & 本格 ready 才载入
        if self.is_b_buf:                          # WS：active 驻留，shadow 反压 FIFO
            if b_sw:                               # swap：active↔shadow，shadow 清空可再填
                self.b_next         = self.b_buf
                self.b_buf_next     = self.b
                self.b_buf_vld_next = False
            elif b_load:                           # shadow 载入（b_rdy 来自 ready 链）
                self.b_next         = self.b
                self.b_buf_next     = self.dtype_in(b_in)
                self.b_buf_vld_next = True
            else:                                  # 反压保持
                self.b_next         = self.b
                self.b_buf_next     = self.b_buf
                self.b_buf_vld_next = self.b_buf_vld
        else:                                      # OS/IS：active 逐拍下流（b_rdy 恒真，无反压）
            self.b_next         = self.dtype_in(b_in) if b_load else self.b
            self.b_buf_next     = self.b_buf
            self.b_buf_vld_next = self.b_buf_vld

    def commit(self):
        self.a          = self.a_next
        self.a_vld      = self.a_vld_next
        self.b          = self.b_next
        self.b_buf      = self.b_buf_next
        self.b_buf_vld  = self.b_buf_vld_next
        self.state      = self.state_next
        if self.latency == 2:
            self.mult = self.mult_next

    def reset(self):
        self.a          = self.dtype_in(0)
        self.a_vld      = False
        self.b          = self.dtype_in(0)
        self.b_buf      = self.dtype_in(0)
        self.b_buf_vld  = False
        self.state      = self.dtype_state(0)
        self.a_next          = self.dtype_in(0)
        self.a_vld_next      = False
        self.b_next          = self.dtype_in(0)
        self.b_buf_next      = self.dtype_in(0)
        self.b_buf_vld_next  = False
        self.state_next      = self.dtype_state(0)
        self.mult            = self.dtype_state(0)
        self.mult_next       = self.dtype_state(0)
