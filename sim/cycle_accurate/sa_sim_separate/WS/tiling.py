import numpy as np
import math


class Tiler:
    def __init__(self, N):
        self.N = N

    # prepare weights to SA, N x N per time
    def weight_tiles(self, W):
        N = self.N
        K, N_w = W.shape
        for k_block in range(math.ceil(K / N)):
            for j_block in range(math.ceil(N_w / N)):
                tile = np.zeros((N, N), dtype=np.int8)
                k_start, k_end = k_block * N, min((k_block + 1) * N, K)
                j_start, j_end = j_block * N, min((j_block + 1) * N, N_w)
                tile[:k_end - k_start, :j_end - j_start] = W[k_start:k_end, j_start:j_end]
                yield tile, k_block, j_block

    # prepare the activation to SA, size (M, N) per time
    def act_chunk(self, A, k_block):
        N = self.N
        K = A.shape[1]
        k_start, k_end = k_block * N, min((k_block + 1) * N, K)
        chunk = np.zeros((A.shape[0], N), dtype=np.int8)
        chunk[:, :k_end - k_start] = A[:, k_start:k_end]
        return chunk

    # collect result from SA, size (M, N) per time 
    def accumulate(self, C, C_tile, j_block):
        N = self.N
        N_w = C.shape[1]
        j_start, j_end = j_block * N, min((j_block + 1) * N, N_w)
        j_width = j_end - j_start
        C[:, j_start:j_end] += C_tile[:, :j_width]

    def alloc_C(self, M, N_w):
        return np.zeros((M, N_w), dtype=np.int32)
