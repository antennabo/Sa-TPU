import numpy as np


def relu(x):
    """
    ReLU activation. Operates on INT32, returns INT32.

    Args:
        x: numpy array, dtype int32

    Returns:
        numpy array, same shape, dtype int32, negative values clamped to 0
    """
    return np.maximum(x, 0, dtype=np.int32)


if __name__ == "__main__":
    x = np.array([[-5, 0, 3], [100, -1, 7]], dtype=np.int32)
    print(relu(x))
