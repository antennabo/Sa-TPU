import numpy as np

# load the activation; shape (M, N)
class DataSetup:
    def __init__(self, N):
        self.N = N
        self._chunk = None
        self._cycle = 0
        self._M = 0

    def load_chunk(self, A_chunk):
        self._chunk = A_chunk
        self._M = A_chunk.shape[0]
        self._cycle = 0

    def step(self):
        N = self.N
        T = self._cycle
        out = np.zeros(N, dtype=np.int8)

        # shape:
        #   4,   3,   2,   1,   0 ---> cycles
        #   0,   0, a20, a10, a00 | SA left boarder
        #   0, a21, a11, a01,   0 | SA left boarder
        # a22, a12, a02,   0,   0 | SA left boarder
        for i in range(N):
            m = T - i
            if 0 <= m < self._M:
                out[i] = self._chunk[m, i]
        self._cycle += 1
        return out

    def reset(self):
        self._chunk = None
        self._cycle = 0
        self._M = 0

    def cycles_needed(self, M):
        return M + self.N - 1
