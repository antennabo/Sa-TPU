import numpy as np
from tiling import tile_matmul


def matmul(W, X, N=8):
    """
    Compute W @ X using an N x N systolic array (Weight Stationary).

    Args:
        W: INT8 weight matrix, shape (M, K)
        X: INT8 activation matrix, shape (K, P)
        N: systolic array size (default 8)

    Returns:
        INT32 result matrix, shape (M, P)
    """

    W = np.asarray(W, dtype=np.int8)
    X = np.asarray(X, dtype=np.int8)
    return tile_matmul(W, X, N)