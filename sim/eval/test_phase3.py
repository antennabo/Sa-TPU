# 模型 → Frontend → IR列表 → 激活捕获 → QuantizationTransform → AnalysisPipeline → 结果

import sys, os, dataclasses
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../model"))

import torch
import numpy as np
from torchvision import datasets, transforms
from SimpleCNN import SimpleCNN
from frontend import export_to_ir
from quantize import quantize_graph
from hw import HardwareConfig
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, StaticNumericalAnalyzer, AnalysisPipeline
from ir import MatMulIR, Conv2dIR, ElementwiseIR
from numpy_ops import conv2d, np_relu, maxpool, np_linear

# 1. 加载模型
model = SimpleCNN()
model.load_state_dict(torch.load("../model/simple_cnn.pth", map_location="cpu"))
model.eval()

# 2. Frontend → IR
exported = torch.export.export(model, (torch.randn(1, 1, 28, 28),))
irs = export_to_ir(exported)

# 3. 取真实 MNIST 输入
dataset = datasets.MNIST("../data", train=False, download=False,
                         transform=transforms.ToTensor())
x_torch, label = dataset[0]
x = x_torch.unsqueeze(0).numpy()  # [1, 1, 28, 28]

# 4. 逐层传播，填入 input_data
filled_irs = []
for ir in irs:
    if isinstance(ir, Conv2dIR):
        filled_irs.append(dataclasses.replace(ir, input_data=x))
        x = np_relu(conv2d(x, ir.input_weight, ir.bias))
    elif isinstance(ir, ElementwiseIR) and ir.op == "maxpool":
        filled_irs.append(ir)
        x = maxpool(x)
    elif isinstance(ir, ElementwiseIR) and ir.op == "flatten":
        filled_irs.append(ir)
        x = x.flatten()
    elif isinstance(ir, MatMulIR):
        filled_irs.append(dataclasses.replace(ir, input_data=x))
        x = np_relu(np_linear(x, ir.input_weight, ir.bias))
    else:
        filled_irs.append(ir)

# 5. 量化
quantized_irs = quantize_graph(filled_irs,"fp32","fp32")

# 6. HW + Pipeline
hw = HardwareConfig(mxu_dim=(8,8), sram_bytes=16*1024*1024,
                    hbm_bw_gbps=900.0, freq_mhz=1000.0)
pipeline = AnalysisPipeline([RooflinePerfAnalyzer(), MemoryAnalyzer(), StaticNumericalAnalyzer()])

# 7. 跑 Pipeline
print(f"真实标签: {label}")
print("\n=== int8 分析结果 ===")
results = pipeline.run_graph(quantized_irs, hw)
for layer, r in results.items():
    if r is None:
        print(f"  {layer}: 跳过")
    else:
        for name, result in r.items():
            print(f"  {layer} | {name}: {result}")
