import torch
from torchvision import datasets, transforms
from SimpleCNN import SimpleCNN

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# 加载模型
model = SimpleCNN().to(device)
model.load_state_dict(torch.load("simple_cnn.pth",map_location=device))
model.eval()

# 取第一张图
transform = transforms.ToTensor()
test_set = datasets.MNIST('../data', train=False, download=True, transform=transform)
img, label = test_set[0]


# 推理
with torch.no_grad():
    logits = model(img.unsqueeze(0).to(device))   # [1,1,28,28]
    pred = logits.argmax(1).item()
    prob = torch.softmax(logits, dim=1).squeeze()

print(f"真实标签: {label}")
print(f"预测结果: {pred}")
print(f"置信度:   {prob[pred]:.4f}")