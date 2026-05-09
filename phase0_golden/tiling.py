import numpy as np
from systolic_array import SystolicArray

# 1. firstly, it needs to pad the W and X such that they are divisible by N, except for the horizontal dimension of the activation.
# 2. Split 2 matrices such that they can fit into the systolic arrays
# Assumption: the sizes of 2 matrices are matched
def tile_matmul(W, X, N=2):
    M, K = W.shape
    _, P = X.shape

    #e.g.: N is 4, M is 9
    # M_pad is of the range 0 to N-1
    # The outer most N is to avoid M%N = 0, without the outer most N, the result would be N
    # , exceeding the range.
    M_pad = (N - M % N) % N
    K_pad = (N - K % N) % N

    #add M_pad rows below, filled with zero, and K_pad columns to the right
    W_pad = np.pad(W, ((0, M_pad), (0, K_pad))).astype(np.int8)  
    X_pad = np.pad(X, ((0, K_pad), (0, 0))).astype(np.int8)

    M_tot = M + M_pad
    K_tot = K + K_pad
    C = np.zeros((M_tot, P), dtype=np.int32)

    sa = SystolicArray(N)

    # Compute block by block
    # Systolic array uses weight stationary, so we need to consider that the size of
    #  weight loaded into the PEs should match the size of the SA
    for i in range(0, M_tot, N):
        for kt in range(0, K_tot, N):
            sa.load_weights(W_pad[i:i+N, kt:kt+N])
            C[i:i+N, :] += sa.compute(X_pad[kt:kt+N, :])

    return C[:M, :]

if __name__ == "__main__":
    W = np.array([[1,2,3],[4,5,6]], dtype=np.int8)   # 2×3，not divisible by 2
    X = np.array([[1,0],[0,1],[1,1]], dtype=np.int8)  # 3×2
    result = tile_matmul(W, X, N=2)
    ref = (W.astype(np.int32) @ X.astype(np.int32))
    print("tiling:", result)
    print("numpy: ", ref)
    print("match: ", np.array_equal(result, ref))