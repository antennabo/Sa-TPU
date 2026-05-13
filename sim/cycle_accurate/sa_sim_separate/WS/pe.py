import numpy as np


class PE:
    def __init__(self):
        self.weight   = np.int8(0)
        self.act_out  = np.int8(0)
        self.psum_out = np.int32(0)

    def tick(self, act_in, psum_in, weight_in, load_enable):
        product       = np.int32(self.weight) * np.int32(act_in)  # read weight first
        self.psum_out = np.int32(psum_in) + product
        self.act_out  = np.int8(act_in)
        self.weight   = np.int8(weight_in) if load_enable else self.weight  # write weight last

    def reset(self):
        self.weight   = np.int8(0)
        self.act_out  = np.int8(0)
        self.psum_out = np.int32(0)
