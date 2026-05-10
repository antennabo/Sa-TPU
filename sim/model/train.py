import torch
import torch.nn as nn
import torch.optim as optim
from SimpleCNN import SimpleCNN
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
from tqdm import tqdm


raw_loader = DataLoader(datasets.MNIST('../data', train=True, download=True,
                        transform=transforms.ToTensor()), batch_size=1000)

imgs = torch.cat([x for x, _ in raw_loader])  # [60000, 1, 28, 28]
mean = imgs.mean().item()
std  = imgs.std().item()

transform = transforms.Compose([
    transforms.ToTensor(),                        # PIL图片 → Tensor，像素值从[0,255]→[0.0,1.0]
    transforms.Normalize((mean,), (std,)),   # 标准化：(x - mean) / std
])

train_loader = DataLoader(datasets.MNIST('../data', train=True,  download=True, transform=transform), batch_size=64, shuffle=True)
test_loader  = DataLoader(datasets.MNIST('../data', train=False, download=True, transform=transform), batch_size=64)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = SimpleCNN().to(device)
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
criterion = nn.CrossEntropyLoss()

for epoch in range(100):
    model.train()
    loop = tqdm(train_loader, desc=f"Epoch {epoch+1}")
    for x, y in loop:
        x, y = x.to(device), y.to(device)
        optimizer.zero_grad()
        loss = criterion(model(x), y)
        loss.backward()
        optimizer.step()
        loop.set_postfix(loss=f"{loss.item():.4f}")

    correct = 0
    total = 0

    model.eval()
    with torch.no_grad():
        for x, y in test_loader:
            x, y = x.to(device), y.to(device)
            pred = model(x).argmax(1)
            correct += pred.eq(y).sum().item()
            total += y.size(0)

    print(f"Accuracy: {correct / total * 100:.2f}%")


torch.save(model.state_dict(), "simple_cnn.pth")