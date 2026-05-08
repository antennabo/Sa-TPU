import torch
import sys
from torchvision import datasets, transforms
import numpy as np
from numpy_ops import conv2d, maxpool, np_linear, np_relu, quantize_int8
sys.path.insert(0, "../model")
from SimpleCNN import SimpleCNN

model = SimpleCNN()
model.load_state_dict(torch.load("../model/simple_cnn.pth", map_location="cpu"))
model.eval()

# for name, param in model.named_parameters():
#     print(name, param.shape)

# 取一张测试图
dataset = datasets.MNIST("../data", train=False, download=False,
                         transform=transforms.ToTensor())
x, label = dataset[0]
x = x.unsqueeze(0)  # [1, 1, 28, 28]

# PyTorch 逐层输出
with torch.no_grad():
    out_conv  = model.feature[0](x)        # Conv2d
    out_relu1 = model.feature[1](out_conv)  # ReLU
    out_pool  = model.feature[2](out_relu1) # MaxPool2d
    out_flat  = out_pool.flatten(1)         # Flatten
    out_fc1   = model.fc[1](out_flat)       # Linear1
    out_relu2 = model.fc[2](out_fc1)        # ReLU
    out_fc2   = model.fc[3](out_relu2)      # Linear2

print("PyTorch 最终输出:", out_fc2.numpy())
print("预测类别:", out_fc2.argmax().item(), "真实标签:", label)

# 提取权重为 numpy
# conv
W_conv  = model.feature[0].weight.detach().numpy()  # [8, 1, 3, 3]
B_conv  = model.feature[0].bias.detach().numpy()    # [8]
# fc 1
W_fc1   = model.fc[1].weight.detach().numpy()       # [64, 1568]
B_fc1   = model.fc[1].bias.detach().numpy()         # [64]
# fc 2
W_fc2   = model.fc[3].weight.detach().numpy()       # [10, 64]
B_fc2   = model.fc[3].bias.detach().numpy()         # [10]

def quantize_int8(W):
    scale = np.max(np.abs(W)) / 127
    W_int8 = np.round(W / scale).astype(np.int8)
    W_dequant = W_int8.astype(np.float32) * scale
    return W_int8, W_dequant, scale

def np_linear(x, W, b):
    return x @ W.T + b

def np_relu(x):
    return np.maximum(0, x)

def conv2d(x, W, b, padding=1):
    N, C, H_in, W_in = x.shape
    out_ch, in_ch, kH, kW = W.shape
    x_pd = np.pad(x, ((0,0), (0,0), (padding,padding), (padding,padding)), mode='constant', constant_values=0)
    H_out = H_in + 2 * padding - kH + 1
    W_out = W_in + 2 * padding - kW + 1
    out = np.zeros((N, out_ch, H_out, W_out))
    for n in range(N):
        for c in range(out_ch):
            for i in range(H_out):
                for j in range(W_out):
                    patch = x_pd[n,:,i:i+kH,j:j+kW]
                    out[n,c,i,j] = np.sum(patch * W[c]) + b[c]
    return out

def maxpool(x, kernel=2):
    N, C, H_in, W_in = x.shape
    H_out = int(H_in / kernel)
    W_out = int(W_in / kernel)
    out = np.zeros((N, C, H_out, W_out))
    for n in range(N):
        for c in range(C):
            for h in range(H_out):
                for w in range(W_out):
                    out[n,c,h,w] = np.max(x[n, c, h*kernel:h*kernel+kernel, w*kernel:w*kernel+kernel])
    return out# pass


np_con = conv2d(x, W_conv, B_conv)
np_con_relu = np_relu(np_con)
np_out_pool = maxpool(np_con_relu)
np_out_flat = np_out_pool.flatten()
np_out_fc1 = np_linear(np_out_flat, W_fc1, B_fc1)
np_relu1 = np_relu(np_out_fc1)
np_out_fc2 = np_linear(np_relu1, W_fc2, B_fc2)
print("np 最终输出:", np_out_fc2)
print("最大误差:", np.max(np.abs(np_out_fc2 - out_fc2.numpy())))
print("预测类别:", np_out_fc2.argmax().item(), "真实标签:", label)

_, W_conv_qint8,  s_conv  = quantize_int8(W_conv)
_, W_fc1_qint8,   s_fc1   = quantize_int8(W_fc1)
_, W_fc2_qint8,   s_fc2   = quantize_int8(W_fc2)

np_con_qint8 = conv2d(x, W_conv_qint8, B_conv)
np_con_relu_qint8 = np_relu(np_con_qint8)
np_out_pool_qint8 = maxpool(np_con_relu_qint8)
np_out_flat_qint8 = np_out_pool_qint8.flatten()
np_out_fc1_qint8 = np_linear(np_out_flat_qint8, W_fc1_qint8, B_fc1)
np_relu1_qint8 = np_relu(np_out_fc1_qint8)
np_out_fc2_qint8 = np_linear(np_relu1_qint8, W_fc2_qint8, B_fc2)

print("np int8 最终输出:", np_out_fc2_qint8)
print("最大误差:", np.max(np.abs(np_out_fc2_qint8 - out_fc2.numpy())))
print("预测类别:", np_out_fc2_qint8.argmax().item(), "真实标签:", label)


import torch
from SimpleCNN import SimpleCNN

model = SimpleCNN()
model.load_state_dict(torch.load("../model/simple_cnn.pth", map_location="cpu"))
model.eval()

sample_input = torch.randn(1, 1, 28, 28)
exported = torch.export.export(model, (sample_input,))
print(exported)