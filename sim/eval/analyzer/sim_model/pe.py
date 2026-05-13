import numpy as np
from .module import module

class pe(module):
    def __init__(self, dtype_in=np.int8, dtype_acc=np.int32):
        super().__init__(dtype_state=dtype_acc)  # PE 用 dtype_acc 作为 state 类型
        self.dtype_in = dtype_in
        self.a = self.dtype_in(0)          # input activation
        self.b = self.dtype_in(0)          # input weight (stationary)
        self.acc = self.dtype_state(0)

    def load_a(self, a_in):
        self.a = self.dtype_in(a_in)

    def load_b(self, b_in):
        self.b = self.dtype_in(b_in)

    def load_acc(self, acc_in):
        self.acc = self.dtype_state(acc_in)

    def compute(self):
        # cast to int32 before multiply to avoid int8 overflow
        self.next_state = self.dtype_state(self.b) * self.dtype_state(self.a) + self.dtype_state(self.acc)

    def reset(self):
        super().reset()
        self.acc = self.dtype_state(0)
        self.a = self.dtype_in(0)
        self.b = self.dtype_in(0)