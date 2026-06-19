import numpy as np

class module:
    def __init__(self, dtype_state=np.int32):
        self.dtype_state = dtype_state
        self.state       = dtype_state(0)
        self.next_state  = dtype_state(0)

    def update(self):
        pass

    def commit(self):
        self.state = self.next_state

    def reset(self):
        self.state      = self.dtype_state(0)
        self.next_state = self.dtype_state(0)