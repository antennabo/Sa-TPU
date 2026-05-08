import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from pathlib import Path


class MnistFC(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(784, 128, bias=False)
        self.fc2 = nn.Linear(128, 10, bias=False)

    def forward(self, x):
        x = x.view(-1, 784)
        x = torch.relu(self.fc1(x))
        return self.fc2(x)


def _train(model, loader, epochs=3):
    optimizer = optim.Adam(model.parameters())
    criterion = nn.CrossEntropyLoss()
    model.train()
    for epoch in range(epochs):
        for images, labels in loader:
            optimizer.zero_grad()
            loss = criterion(model(images), labels)
            loss.backward()
            optimizer.step()
        print(f"  epoch {epoch + 1}/{epochs} done")


def _quantize_int8(weight_f32):
    """Symmetric per-tensor: scale = max(|W|) / 127"""
    scale = float(weight_f32.abs().max()) / 127.0
    w_int8 = (weight_f32 / scale).round().clamp(-128, 127).to(torch.int8)
    return w_int8.numpy(), scale


def load_mnist_weights(
    data_dir="data/mnist",
    out_path="phase0_golden/mnist_weights.npz",
    retrain=False,
):
    """
    Train a 2-layer MNIST FC model and export INT8 weights.

    Saved arrays in out_path (.npz):
        fc1_w_int8  (128, 784)  int8
        fc1_scale   scalar      float64   dequant scale for fc1
        fc2_w_int8  (10, 128)   int8
        fc2_scale   scalar      float64   dequant scale for fc2

    Returns:
        dict with the four arrays above
    """
    out_path = Path(out_path)

    if out_path.exists() and not retrain:
        print(f"Loading cached weights from {out_path}")
        data = np.load(out_path)
        return {k: data[k] for k in data}

    transform = transforms.Compose([transforms.ToTensor()])
    train_set = datasets.MNIST(data_dir, train=True, download=True, transform=transform)
    loader = torch.utils.data.DataLoader(train_set, batch_size=256, shuffle=True)

    model = MnistFC()
    print("Training MNIST FC model...")
    _train(model, loader, epochs=3)

    fc1_int8, fc1_scale = _quantize_int8(model.fc1.weight.data)
    fc2_int8, fc2_scale = _quantize_int8(model.fc2.weight.data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_path,
             fc1_w_int8=fc1_int8, fc1_scale=fc1_scale,
             fc2_w_int8=fc2_int8, fc2_scale=fc2_scale)
    print(f"Weights saved to {out_path}")

    return {"fc1_w_int8": fc1_int8, "fc1_scale": fc1_scale,
            "fc2_w_int8": fc2_int8, "fc2_scale": fc2_scale}


if __name__ == "__main__":
    weights = load_mnist_weights()
    for k, v in weights.items():
        arr = np.asarray(v)
        print(f"  {k}: shape={arr.shape}  dtype={arr.dtype}")
