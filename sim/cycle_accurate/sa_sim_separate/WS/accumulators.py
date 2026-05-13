import numpy as np

# Captures bottom-edge outputs from the SA at the correct (m, j) positions to reconstruct the output tile C[M, N].
class Accumulators:
    def __init__(self, N):
        self.N = N
        self._buffer = None
        self._M = 0
        self._cycle = 0

    def start_collect(self, M):
        self._M = M
        self._buffer = np.zeros((M, self.N), dtype=np.int32)
        self._cycle = 0


    #  SA buttom boarder
    # ----------------- cycle
    #   0,   0, c22     4         
    #   0, c21, c12     3
    # c20, c11, c02     2
    # c10, c01,   0     1
    # c00,   0,   0     0

    def step(self, psum_out_bottom):
        N = self.N
        T = self._cycle
        for j in range(N):
            m = T - (N - 1) - j
            if 0 <= m < self._M:
                self._buffer[m, j] = psum_out_bottom[j]
        self._cycle += 1

    def finish(self):
        return self._buffer

    def reset(self):
        self._buffer = None
        self._M = 0
        self._cycle = 0

    # C[m, j] appears at tick: T = m + (N-1) + j; the last output to arrive: C[M-1, N-1]
    # The last output uses the maximum values of both m and j:
    #   T_last = (M-1) + (N-1) + (N-1) = M + 2N - 3
    # Since ticks are 0-indexed, the total number of ticks needed is M + 2N - 3 + 1 = M + 2N - 2
    def cycles_needed(self, M):
        return M + 2 * self.N - 2
