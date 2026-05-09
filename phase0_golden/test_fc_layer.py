import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest
from torchvision import datasets, transforms

from matmul_api import matmul
from activation import relu
from load_mnist_weights import load_mnist_weights

DATA_DIR = "data/mnist"
WEIGHTS_PATH = "phase0_golden/mnist_weights.npz"
ACCURACY_THRESHOLD = 0.85
N_TEST = 1000


def _quantize_int8(x_f32):
    scale = float(np.abs(x_f32).max()) / 127.0
    if scale == 0.0:
        return np.zeros_like(x_f32, dtype=np.int8), 1.0
    return np.clip(np.round(x_f32 / scale), -128, 127).astype(np.int8), scale


def _forward(weights, X_f32):
    """
    INT8 2-layer FC forward pass.

    X_f32 : (784, N) float32
    returns: (N,) predicted labels
    """
    fc1_w = weights["fc1_w_int8"]   # (128, 784)
    fc2_w = weights["fc2_w_int8"]   # (10,  128)

    X_int8, _ = _quantize_int8(X_f32)              # (784, N) int8
    out1 = relu(matmul(fc1_w, X_int8))             # (128, N) int32, ReLU applied

    out1_int8, _ = _quantize_int8(out1.astype(np.float32))  # (128, N) int8
    out2 = matmul(fc2_w, out1_int8)                # (10,  N) int32

    return np.argmax(out2, axis=0)                 # (N,)


def test_fc_layer_accuracy():
    weights = load_mnist_weights(DATA_DIR, WEIGHTS_PATH)

    transform = transforms.Compose([transforms.ToTensor()])
    test_set = datasets.MNIST(DATA_DIR, train=False, download=True, transform=transform)

    images, labels = zip(*[(img.numpy().reshape(784), lbl)
                            for img, lbl in list(test_set)[:N_TEST]])
    X_f32 = np.stack(images, axis=1)   # (784, N_TEST)
    labels = np.array(labels)          # (N_TEST,)

    preds = _forward(weights, X_f32)
    accuracy = (preds == labels).mean()

    print(f"\nINT8 FC accuracy: {accuracy:.2%}  ({(preds==labels).sum()}/{N_TEST})")
    assert accuracy >= ACCURACY_THRESHOLD, (
        f"accuracy {accuracy:.2%} below threshold {ACCURACY_THRESHOLD:.2%}"
    )
