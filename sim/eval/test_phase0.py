import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from ir import MatMulIR, ReductionOrder
from hw import HardwareConfig
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, AnalysisPipeline
from result import AnalysisResult, PerfResult, MemoryResult
# 1. 手写 IR
op = MatMulIR(
    op_type="matmul",
    dtype="float32",
    accum_dtype="float32",
    M=1024, N=1024, K=1024,
    tile={"tm": 64, "tn": 64, "tk": 64},
    reduction_order=ReductionOrder.SEQUENTIAL,
)

# 2. 手写 HW
hw = HardwareConfig(
    mxu_dim=(8, 8),
    sram_bytes=16 * 1024 * 1024,
    hbm_bw_gbps=900.0,
    freq_mhz=1000.0,
)

# 3. 跑 Pipeline
pipeline = AnalysisPipeline([RooflinePerfAnalyzer(), MemoryAnalyzer()])
results = pipeline.run(op, hw)

for name, r in results.items():
    print(f"{name}: {r}")

def contract_test(analyzer, op, hw, expected_type):
    result = analyzer.analyze(op, hw)
    assert isinstance(result, expected_type), f"{type(analyzer).__name__} 返回了错误类型: {type(result)}"
    print(f"ContractTest passed: {type(analyzer).__name__}")

contract_test(RooflinePerfAnalyzer(), op, hw, PerfResult)
contract_test(MemoryAnalyzer(),       op, hw, MemoryResult)