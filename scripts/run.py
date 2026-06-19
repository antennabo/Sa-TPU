import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "model"))  # standalone SimpleCNN import inside .pth load

import torch
from torchvision import datasets, transforms
from torch.ao.quantization import get_default_qconfig
from torch.ao.quantization import QConfigMapping
from torch.ao.quantization.quantize_fx import prepare_fx, convert_fx
from torch.utils.data import DataLoader
from model.SimpleCNN import SimpleCNN
from compiler.frontend.frontend import Frontend
from compiler.backend import Backend
from compiler.hw import HardwareConfig
from scripts.perf_sim import Simulator

import numpy as np

# 1. 加载模型
model = SimpleCNN()
model.load_state_dict(torch.load(os.path.join(ROOT, "model/simple_cnn.pth"), map_location="cpu"))
model.eval()


# 3. 取真实输入
dataset = datasets.MNIST(os.path.join(ROOT, "build/data"), train=False, download=False,
                         transform=transforms.ToTensor())
x_np = dataset[0][0].unsqueeze(0).numpy()  # [1, 1, 28, 28]

calibration_loader = DataLoader(
    dataset,
    batch_size=32,
    shuffle=False
)
qconfig = get_default_qconfig("fbgemm")

qconfig_mapping = (
    QConfigMapping()
    .set_global(qconfig)
)
example_inputs = (
    torch.randn(1, 1, 28, 28),
)
prepared_model = prepare_fx(
    model,
    qconfig_mapping,
    example_inputs
)
with torch.no_grad():
    for images, _ in calibration_loader:
        prepared_model(images)
quantized_model = convert_fx(
    prepared_model
)
# print(quantized_model)
for name, module in quantized_model.named_modules():
    print(name, type(module))
modules = dict(quantized_model.named_modules())

conv = modules["feature.0"]
fc1  = modules["fc.1"]
fc2  = modules["fc.3"]
w = conv.weight()

print(type(w))
# print(w.int_repr())


activations = {}

def save_activation(name):
    def hook(module, inp, out):
        activations[name] = out
    return hook

conv.register_forward_hook(
    save_activation("feature.0")
)

fc1.register_forward_hook(
    save_activation("fc.1")
)

fc2.register_forward_hook(
    save_activation("fc.3")
)

# 抓 fc2(layer3) 的输入激活（= fc2 真正吃进去的 A），pre-hook 取 inp
fc2_input = {}
def save_fc2_input(module, inp):
    fc2_input["t"] = inp[0]
fc2.register_forward_pre_hook(save_fc2_input)

x = dataset[0][0].unsqueeze(0)
with torch.no_grad():
    y = quantized_model(x)

# print(activations.keys())
act = activations["feature.0"]

print(type(act))
print(act.dtype)
print(act.shape)
print(act.q_scale())
print(act.q_zero_point())
act_int8 = act.int_repr()

print(act_int8.shape)
print(act_int8)
# 4. HW 配置
hw = HardwareConfig(mxu_dim=(8, 8), sram_bytes=16*1024*1024,
                    hbm_bw_gbps=900.0, freq_mhz=1000.0,
                    dtype="int8", accum_dtype="int32")

# 2. Frontend: model → IR (parse + fuse + lower)
exported = torch.export.export(model, (torch.randn(1, 1, 28, 28),))
frontend = Frontend()

# 5. Frontend: model → IR
irs = frontend.run(exported)
frontend.print_irs()

# 6. Backend: IR → quantize → tile → map → compile
backend = Backend(hw)
full_irs, data = backend.run(irs, exported, x_np)
backend.print_irs()
# print(data.weights)

# A_tiles = np.load(os.path.join(ROOT, "build/tiles/layer3_A_tiles.npy"))
# B_tiles = np.load(os.path.join(ROOT, "build/tiles/layer3_B_tiles.npy"))
# output = np.load(os.path.join(ROOT, "build/tiles/layer3_output.npy"))
# print(A_tiles.shape)
# print(B_tiles.shape)
# print(output.shape)
# print(A_tiles[0][0])
# print(B_tiles[0][0])
# # 7. 分析
# # PyTorch 参考输出
# x_torch = torch.from_numpy(x_np)
# with torch.no_grad():
#     out_torch = model(x_torch).numpy().flatten()
# print(f"[PyTorch]   {out_torch}")

# sim = Simulator(full_irs, x_np, hw)
# sim.run_static()
# sim.run_numerical(exported)
# sim.cycle.analyze_from_tiles("./tiles", hw, layer="layer3")
# sim.run_cycle(exported)

# === 用 torch 真实量化值构造 layer3(fc2) 的 int8 tile，覆盖 backend 的量化 ===
# A: fc2 输入激活，quint8 但 zp=0、范围 0~95 → 直接当 int8；B: 权重 qint8 对称 zp=0。
TM = TN = TK = 8
A = fc2_input["t"].int_repr().numpy().astype(np.int8)   # (1, 64)
B = fc2.weight().int_repr().numpy().T.astype(np.int8)   # (64, 10)
bias = fc2.bias().detach().numpy().astype(np.float32)   # (10,)
M, K = A.shape; _, N = B.shape
num_m, num_k, num_n = -(-M // TM), -(-K // TK), -(-N // TN)

def _pad(t, shape):
    o = np.zeros(shape, dtype=np.int8); o[:t.shape[0], :t.shape[1]] = t; return o

A_tiles = [_pad(A[m:m+TM, k:k+TK], (TM, TK)) for m in range(0, M, TM) for k in range(0, K, TK)]
B_tiles = [_pad(B[k:k+TK, n:n+TN], (TK, TN)) for k in range(0, K, TK) for n in range(0, N, TN)]
tiles_dir = os.path.join(ROOT, "build/tiles")
np.save(os.path.join(tiles_dir, "layer3_A_tiles.npy"), np.array(A_tiles).reshape(num_m, num_k, TM, TK))
np.save(os.path.join(tiles_dir, "layer3_B_tiles.npy"), np.array(B_tiles).reshape(num_k, num_n, TK, TN))
np.save(os.path.join(tiles_dir, "layer3_bias.npy"), bias)

# --- 周期级模拟：从 tile 跑 OS 矩阵乘 ---
from simulator.cycle.cycle_analyzer import CycleAccurateAnalyzer
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

ca = CycleAccurateAnalyzer(hw)
ca.load_tiles(tiles_dir, layer="layer3")
C = ca.simulate(data.programs["layer3"], mode="WS")
print("cycle-accurate A shape,B shape, C shape:", A.shape, B.shape, C.shape, "total_cycles:", ca.total_cycles)
# print(C)