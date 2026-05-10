# 模型 → Frontend → IR列表 → QuantizationTransform → AnalysisPipeline + NumericalAnalyzer

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../model"))

import torch
import numpy as np
from torchvision import datasets, transforms
from SimpleCNN import SimpleCNN
from frontend import export_to_ir
from quantize import quantize_graph
from hw import HardwareConfig
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, NumericalAnalyzer, AnalysisPipeline

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
x_np = x_torch.unsqueeze(0).numpy()  # [1, 1, 28, 28]

# 4. 量化
quantized_irs = quantize_graph(irs, dtype="int8", accum_dtype="int32")

# 5. HW
hw = HardwareConfig(mxu_dim=(8,8), sram_bytes=16*1024*1024,
                    hbm_bw_gbps=900.0, freq_mhz=1000.0)

# 6. Perf + Memory Pipeline
from transform import fill_activations
filled_irs = fill_activations(quantized_irs, x_np)   # 填 input_data
pipeline = AnalysisPipeline([RooflinePerfAnalyzer(), MemoryAnalyzer(), NumericalAnalyzer()])

print("\n=== Perf / Memory 分析 ===")
results = pipeline.run_graph(filled_irs, hw)
for key, r in results.items():
    if r is None:
        print(f"  {key}: 跳过")
    elif isinstance(r, dict):
        for name, result in r.items():
            print(f"  {key} | {name}: {result}")
    else:
        print(f"  {key}: {r}")

# 7. 数值分析（独立于 Pipeline）
numerical = NumericalAnalyzer()
num_result = numerical.analyze_graph(quantized_irs, x_np)
print(f"\n=== 全模型数值分析（int8 vs fp32 golden）===")
print(f"  max_error:  {num_result.max_error:.6f}")
print(f"  mean_error: {num_result.mean_error:.6f}")
