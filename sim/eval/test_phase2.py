import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../model"))

import torch
from SimpleCNN import SimpleCNN
from frontend import export_to_ir
from hw import HardwareConfig
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, AnalysisPipeline
from ir import MatMulIR

# 1. 加载模型
model = SimpleCNN()
model.load_state_dict(torch.load("../model/simple_cnn.pth", map_location="cpu"))
model.eval()

# 2. Frontend → IR
exported = torch.export.export(model, (torch.randn(1, 1, 28, 28),))
irs = export_to_ir(exported)

# 3. HW
hw = HardwareConfig(mxu_dim=(8,8), sram_bytes=16*1024*1024,
                    hbm_bw_gbps=900.0, freq_mhz=50.0)

# 4. Pipeline（只跑 MatMulIR，Conv2dIR 支持留到后续）
pipeline = AnalysisPipeline([RooflinePerfAnalyzer(), MemoryAnalyzer()])

for ir in irs:
    if isinstance(ir, MatMulIR):
        print(f"\n--- {ir.op_type} M={ir.M} N={ir.N} K={ir.K} ---")
        results = pipeline.run(ir, hw)
        for name, r in results.items():
            print(f"  {name}: {r}")