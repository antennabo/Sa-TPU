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
full_irs = backend.run(irs)
backend.print_irs()

# 7. 分析
sim = Simulator(full_irs, x_np, hw)
sim.run_static()
sim.run_numerical(exported)
sim.run_cycle(exported)
