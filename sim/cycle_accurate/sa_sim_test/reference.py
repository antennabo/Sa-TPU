import numpy as np


def ref_matmul(W, X):
    """
    NumPy ground truth for INT8 x INT8 -> INT32 matrix multiply.

    Args:
        W: weight matrix, shape (M, K), cast to int8
        X: activation matrix, shape (K, P), cast to int8

    Returns:
        INT32 result matrix, shape (M, P)
    """
    W = np.asarray(W, dtype=np.int8)
    X = np.asarray(X, dtype=np.int8)
    return W.astype(np.int32) @ X.astype(np.int32)


if __name__ == "__main__":
    W = np.array([[1, 2], [3, 4]], dtype=np.int8)
    X = np.array([[5, 6], [7, 8]], dtype=np.int8)
    print(ref_matmul(W, X))
