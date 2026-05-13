import numpy as np
from pe import PE

# Use the convention C = X @ W, where X is the activation and W is the weight.
class SystolicArray:
    def __init__(self, N):
        self.N = N
        self.pes = [[PE() for _ in range(N)] for _ in range(N)]

    def tick(self, weight_in_top, load_enable, act_in_left, trace=False):
        N = self.N
        pes = self.pes

        # Sample all neighbor values BEFORE any pe.tick() — critical for cycle-accuracy
        act_ins    = np.empty((N, N), dtype=np.int8)
        psum_ins   = np.empty((N, N), dtype=np.int32)
        weight_ins = np.empty((N, N), dtype=np.int8)

        for i in range(N):
            for j in range(N):
                act_ins[i, j]    = act_in_left[i]       if j == 0 else pes[i][j-1].act_out
                psum_ins[i, j]   = np.int32(0)       if i == 0 else pes[i-1][j].psum_out
                weight_ins[i, j] = weight_in_top[j]     if i == 0 else pes[i-1][j].weight

        for i in range(N):
            for j in range(N):
                pes[i][j].tick(act_ins[i, j], psum_ins[i, j], weight_ins[i, j], load_enable)

        result = {
            'psum_out_bottom':  np.array([pes[N-1][j].psum_out for j in range(N)], dtype=np.int32),
            'act_out_right':    np.array([pes[i][N-1].act_out  for i in range(N)], dtype=np.int8),
            'weight_out_bottom':np.array([pes[N-1][j].weight   for j in range(N)], dtype=np.int8),
        }

        if trace:
            result['trace'] = {
                'weight':   np.array([[pes[i][j].weight   for j in range(N)] for i in range(N)], dtype=np.int8),
                'act_out':  np.array([[pes[i][j].act_out  for j in range(N)] for i in range(N)], dtype=np.int8),
                'psum_out': np.array([[pes[i][j].psum_out for j in range(N)] for i in range(N)], dtype=np.int32),
            }

        return result

    def reset(self):
        for row in self.pes:
            for pe in row:
                pe.reset()

    def snapshot(self):
        N = self.N
        return {
            'weight':   np.array([[self.pes[i][j].weight   for j in range(N)] for i in range(N)], dtype=np.int8),
            'act_out':  np.array([[self.pes[i][j].act_out  for j in range(N)] for i in range(N)], dtype=np.int8),
            'psum_out': np.array([[self.pes[i][j].psum_out for j in range(N)] for i in range(N)], dtype=np.int32),
        }
