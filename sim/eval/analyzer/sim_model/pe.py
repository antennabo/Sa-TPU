import numpy as np
from .module import module


class pe(module):
    """Systolic PE：寄存 a(横向流) / b(纵向流) / state(psum)。两段式：update 做 MAC 并把
    流入的 a_in/b_in 暂存到 *_next；commit 锁存。路由（取邻居/边缘/restore）由 spatial_array 定后传入。

    latency:
      1 — 组合 MAC：state_next = acc_in + a*b（一拍出结果）
      2 — 两级流水：乘积先打一拍寄存(mult)，下一拍再累加：
            mult_next  = a*b
            state_next = acc_in + mult   (上一拍的乘积)
          a/b 仍每拍一格，只有 MAC/psum 路径多一拍延迟（硬件拆 mult/add 关键路径）。
    """

    def __init__(self, dtype_in=np.int8, dtype_acc=np.int32, latency=1):
        super().__init__(dtype_state=dtype_acc)
        self.dtype_in = dtype_in
        self.latency  = latency
        self.reset()

    def update(self, a_in, b_in, acc_in):
        prod = self.dtype_state(self.a) * self.dtype_state(self.b)
        if self.latency == 2:
            self.state_next = self.dtype_state(acc_in) + self.mult  # 累加上一拍乘积
            self.mult_next  = prod                                  # 本拍乘积入流水寄存器
        else:
            self.state_next = self.dtype_state(acc_in) + prod
        self.a_next = self.dtype_in(a_in)
        self.b_next = self.dtype_in(b_in)

    def commit(self):
        self.a     = self.a_next
        self.b     = self.b_next
        self.state = self.state_next
        if self.latency == 2:
            self.mult = self.mult_next

    def reset(self):
        self.a          = self.dtype_in(0)
        self.b          = self.dtype_in(0)
        self.state      = self.dtype_state(0)
        self.a_next     = self.dtype_in(0)
        self.b_next     = self.dtype_in(0)
        self.state_next = self.dtype_state(0)
        self.mult       = self.dtype_state(0)
        self.mult_next  = self.dtype_state(0)
