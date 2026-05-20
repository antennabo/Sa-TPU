import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../model"))

import torch
from torchvision import datasets, transforms

from SimpleCNN import SimpleCNN
from frontend.frontend import Frontend
from backend.backend import Backend
from backend.hw import HardwareConfig
from analyzer.simulator import Simulator
import numpy as np

# 1. 加载模型
model = SimpleCNN()
model.load_state_dict(torch.load("../model/simple_cnn.pth", map_location="cpu"))
model.eval()

# 2. Frontend: model → IR (parse + fuse + lower)
exported = torch.export.export(model, (torch.randn(1, 1, 28, 28),))
frontend = Frontend()


# 3. 取真实输入
dataset = datasets.MNIST("../data", train=False, download=False,
                         transform=transforms.ToTensor())
x_np = dataset[0][0].unsqueeze(0).numpy()  # [1, 1, 28, 28]

# 4. HW 配置
hw = HardwareConfig(mxu_dim=(8, 8), sram_bytes=16*1024*1024,
                    hbm_bw_gbps=900.0, freq_mhz=1000.0,
                    dtype="int8", accum_dtype="int32")

# 5. Frontend: model → IR
irs = frontend.run(exported)
frontend.print_irs()

# 6. Backend: IR → quantize → tile → map → compile
backend = Backend(hw)
full_irs, data = backend.run(irs, exported, x_np)
backend.print_irs()


A_tiles = np.load("./tiles/layer3_A_tiles.npy")
B_tiles = np.load("./tiles/layer3_B_tiles.npy")
output = np.load("./tiles/layer3_output.npy")
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

# --- debug: 小矩阵验证 ---
from analyzer.cycle_analyzer import CycleAccurateAnalyzer
from analyzer.sim_model.spatial_array import spatial_array
from analyzer.sim_model.tile_buf import TileBuf

M, N, K = 8, 8, 8
A = np.ones((M, K), dtype=np.int8)
B = np.ones((K, N), dtype=np.int8)
expected = A.astype(np.int32) @ B.astype(np.int32)
print("Expected (numpy):\n", expected)

ca = CycleAccurateAnalyzer()
ca.hw = hw
ca.sa = spatial_array(M, N, dtype_in=np.int8, dtype_acc=np.int32)
ca.wb = TileBuf(N)
ca.ab = TileBuf(M)

A2 = np.ones((M, K), dtype=np.int8) * 2
B2 = np.ones((K, N), dtype=np.int8) * 2
ca.weight_tile_queue.append(B)
ca.weight_tile_queue.append(B2)
ca.activation_tile_queue.append(A)
ca.activation_tile_queue.append(A2)

ca.simulate(mode="OS")