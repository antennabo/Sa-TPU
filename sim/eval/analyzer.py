from abc import ABC, abstractmethod
from ir import OpIR, MatMulIR, ReductionOrder
from hw import HardwareConfig
from result import AnalysisResult, PerfResult, MemoryResult, NumericalResult
import math
import random

class Analyzer(ABC):
    name: str  # 子类定义，例如 "roofline" / "memory"

    def validate_input(self, op: OpIR, hw: HardwareConfig) -> None:
        # 基础检查：op 是 OpIR 子类，hw 是 HardwareConfig
        if not isinstance(op, OpIR):
            raise TypeError(f"期望 OpIR，收到 {type(op)}")
        if not isinstance(hw, HardwareConfig):
            raise TypeError(f"期望 HardwareConfig，收到 {type(hw)}")

    @abstractmethod
    def analyze(self, op: OpIR, hw: HardwareConfig) -> AnalysisResult:
        pass

    def run(self, op: OpIR, hw: HardwareConfig) -> AnalysisResult:
        self.validate_input(op, hw)
        return self.analyze(op, hw)

class RooflinePerfAnalyzer(Analyzer):
    name = "roofline"
    
    def analyze(self, op, hw):
        if op.dtype in ("fp16", "int16"):
            dtype_bytes = 2
        elif op.dtype == "int8":
            dtype_bytes = 1
        elif op.dtype == "int4":
            dtype_bytes = 0.5
        else:
            dtype_bytes = 4
        # 每个输出元素需要K次乘法和K次加法
        # output 元素数 = M × N
        # 每个元素的运算 = K × (multiply + add) = 2K FLOPs
        ops   =  2 * op.M * op.N * op.K         # MACs × 2
        bytes_num = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes
        arithmetic_intensity = ops / bytes_num

        peak_flops_per_ns = hw.freq_mhz * 1e6 * hw.mxu_dim[0] * hw.mxu_dim[1] * 2 / 1e9
        peak_bw_per_ns    = hw.hbm_bw_gbps / 8          # GB/s → bytes/ns

        bound_flop = ops / peak_flops_per_ns
        bound_mem  = bytes_num / peak_bw_per_ns
        latency_ns = max(bound_flop, bound_mem)
        utilization = bound_flop / latency_ns        # 越接近1越好
        return PerfResult(
            latency_ns=latency_ns,
            utilization=utilization,
            throughput_ops=ops / latency_ns,
            model_name="roofline",
            confidence="approximate",
        )
    
class MemoryAnalyzer(Analyzer):
    # TODO: 跨层数据复用分析 - 中间 tensor 若能驻留 SRAM，hbm_bytes 可减少
    # 当前 hbm_bytes 为悲观估计（假设每层都从 HBM 读写）
    # 举个例子，SimpleCNN 的数据流：
    # Conv2d 输出 [1,8,28,28] → ReLU → MaxPool 输出 [1,8,14,14] → Linear
    # 如果 Conv 输出能留在 SRAM 里直到 MaxPool 用完，就省了一次 HBM 写+读。
    # 分析的问题就是：这个中间 tensor 能放进 SRAM 吗？
    # Conv 输出大小 = 1×8×28×28×4 bytes = 25088 bytes ≈ 25 KB
    # SRAM = 16 MB
    # 25KB << 16MB，完全放得下，可以复用。
    name = "memory"

    def analyze(self, op, hw):
        if op.dtype in ("fp16", "int16"):
            dtype_bytes = 2
        elif op.dtype == "int8":
            dtype_bytes = 1
        elif op.dtype == "int4":
            dtype_bytes = 0.5
        else:
            dtype_bytes = 4
        # M*K A矩阵
        # K*N B矩阵
        # M*N 输出矩阵
        hbm_bytes = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes
        if op.tile == None:
            sram_bytes = hbm_bytes
        else:
            sram_bytes = (op.tile["tm"]*op.tile["tk"] + op.tile["tk"]*op.tile["tn"] + op.tile["tm"]*op.tile["tn"]) * dtype_bytes
        fits = sram_bytes <= hw.sram_bytes
        return MemoryResult(sram_bytes=sram_bytes,hbm_bytes=hbm_bytes,fits=fits)


class StaticNumericalAnalyzer(Analyzer):
    name = "numerical"

    def analyze(self, op, _hw):
        if not hasattr(op, "input_weight") or op.input_weight is None:
            raise NotImplementedError(f"{type(op).__name__} 没有 input_weight，无法分析")
        return self._analyze_real(op)

    @staticmethod
    def _analyze_real(op):
        import numpy as np
        from numpy_ops import np_linear, conv2d, quantize_int8
        from ir import MatMulIR, Conv2dIR

        W = op.input_weight.astype(np.float32)
        b = op.bias.astype(np.float32) if op.bias is not None else 0

        # 根据 op.dtype 决定量化方式
        if op.dtype == "int8":
            _, W_dq, _ = quantize_int8(W)
        elif op.dtype == "fp16":
            W_dq = W.astype(np.float16).astype(np.float32)
        else:
            W_dq = W  # fp32，误差为 0

        if isinstance(op, MatMulIR):
            if op.input_data is None:
                raise ValueError("MatMulIR 缺少 input_data")
            x = op.input_data.astype(np.float32)
            out_fp32 = np_linear(x, W, b)
            out_q = np_linear(x, W_dq, b)

        elif isinstance(op, Conv2dIR):
            if op.input_data is None:
                raise ValueError("Conv2dIR 缺少 input_data")
            x = op.input_data.astype(np.float32)
            out_fp32 = conv2d(x, W, b)
            out_q = conv2d(x, W_dq, b)

        else:
            raise NotImplementedError(f"不支持 {type(op).__name__}")

        error = np.abs(out_fp32 - out_q)
        return NumericalResult(
            max_error=float(error.max()),
            mean_error=float(error.mean()),
            reduction_order_used=op.reduction_order or ReductionOrder.SEQUENTIAL,
            reference_order=ReductionOrder.SEQUENTIAL,
            accum_overflow=False,
            warnings=(),
        )
    #     if op.reduction_order == ReductionOrder.SEQUENTIAL:
    #         result = StaticNumericalAnalyzer._sequential_sum(samples, op.dtype)
    #     elif op.reduction_order == ReductionOrder.TREE:
    #         result = StaticNumericalAnalyzer._tree_sum(samples, op.dtype)
    #     else:
    #         raise NotImplementedError(f"暂不支持 {op.reduction_order}")

    #     error = abs(result - ref)

    #     # int8 溢出检查：累加 K 个 int8 最大值是否超出 int32 范围
    #     accum_overflow = (op.dtype == "int8" and op.K * 127 > 2**31 - 1)

    #     warnings = ()
    #     if op.dtype in ("fp16", "bf16") and op.K > 1024:
    #         warnings = (f"fp16 累加 K={op.K} 步，精度风险较高",)

    #     return NumericalResult(
    #         max_error=error,
    #         mean_error=error,
    #         reduction_order_used=op.reduction_order,
    #         reference_order=ReductionOrder.SEQUENTIAL,
    #         accum_overflow=accum_overflow,
    #         warnings=warnings,
    #     )

class AnalysisPipeline:
    def __init__(self, analyzers: list[Analyzer]):
        self.analyzers = analyzers

    def run(self, op, hw) -> dict:
        return {type(a).__name__: a.run(op, hw) for a in self.analyzers}
    
    def run_graph(self, ops: list, hw: HardwareConfig) -> dict:
        results = {}
        for i, op in enumerate(ops):
            try:
                results[f"{op.op_type}_{i}"] = self.run(op, hw)
            except (NotImplementedError, AttributeError):
                results[f"{op.op_type}_{i}"] = None  # 暂不支持
        return results