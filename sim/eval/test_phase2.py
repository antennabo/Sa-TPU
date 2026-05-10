import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../model"))

import torch
from SimpleCNN import SimpleCNN
from frontend import export_to_ir
from hw import HardwareConfig
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, AnalysisPipeline
from ir import MatMulIR, ReductionOrder
from transform import TilingTransform, MappingTransform

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

# matmul_ir = [ir for ir in irs if isinstance(ir, MatMulIR)][0]
# # TilingTransform
# tiled_ir = TilingTransform().run(matmul_ir, tile={"tm":1,"tn":32,"tk":256})
# print("\nTiled IR:", tiled_ir.tile)

# # MappingTransform
# mapped_ir = MappingTransform().run(tiled_ir,
#     mapping={"M":"batch","N":"mxu_col","K":"mxu_row"},
#     reduction_order=ReductionOrder.SEQUENTIAL)
# print("Mapped IR reduction_order:", mapped_ir.reduction_order)

# # 再跑 Pipeline
# results = pipeline.run(mapped_ir, hw)
# for name, r in results.items():
#     print(f"  {name}: {r}")

# results = pipeline.run_graph(irs, hw)
# for layer, r in results.items():
#     print(f"{layer}: {r}")

from lowering import lower_graph
lowered = lower_graph(irs)
print("Lowered IR 数量:", len(lowered))
# print("Lowered IR 数量:", lowered)

from quantize import quantize_graph

quantized_irs = quantize_graph(irs)
for ir in quantized_irs:
    print(ir.op_type, ir.dtype)