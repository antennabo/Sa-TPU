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
    
    @staticmethod
    def _kahan_sum(vals: list[float]) -> float:
        """fp64 + Kahan 求和，作为黄金基准"""
        s, c = 0.0, 0.0
        for v in vals:
            y = v - c
            t = s + y
            c = (t - s) - y
            s = t
        return s
    
    @staticmethod
    def _sequential_sum(vals, dtype):
        """线性累加，模拟 fp16/int8 精度"""
        # 用 Python float 模拟，后续可换成 numpy fp16
        s = 0.0
        for v in vals:
            s += v
        return s

    @staticmethod
    def _tree_sum(vals, dtype):
        if len(vals) == 1:
            return vals[0]
        mid = len(vals) // 2
        return StaticNumericalAnalyzer._tree_sum(vals[:mid], dtype) + \
               StaticNumericalAnalyzer._tree_sum(vals[mid:], dtype)
    
    def analyze(self, op: OpIR, _hw: HardwareConfig) -> AnalysisResult:
        # TODO: 阶段2替换为真实输入数据
        rng = random.Random(hash((op.M, op.N, op.K, op.dtype)))
        samples = [rng.gauss(0, 1) for _ in range(op.K)]

        ref = StaticNumericalAnalyzer._kahan_sum(samples)

        if op.reduction_order == ReductionOrder.SEQUENTIAL:
            result = StaticNumericalAnalyzer._sequential_sum(samples, op.dtype)
        elif op.reduction_order == ReductionOrder.TREE:
            result = StaticNumericalAnalyzer._tree_sum(samples, op.dtype)
        else:
            raise NotImplementedError(f"暂不支持 {op.reduction_order}")

        error = abs(result - ref)

        # int8 溢出检查：累加 K 个 int8 最大值是否超出 int32 范围
        accum_overflow = (op.dtype == "int8" and op.K * 127 > 2**31 - 1)

        warnings = ()
        if op.dtype in ("fp16", "bf16") and op.K > 1024:
            warnings = (f"fp16 累加 K={op.K} 步，精度风险较高",)

        return NumericalResult(
            max_error=error,
            mean_error=error,
            reduction_order_used=op.reduction_order,
            reference_order=ReductionOrder.SEQUENTIAL,
            accum_overflow=accum_overflow,
            warnings=warnings,
        )

class AnalysisPipeline:
    def __init__(self, analyzers: list[Analyzer]):
        self.analyzers = analyzers

    def run(self, op, hw) -> dict:
        return {type(a).__name__: a.run(op, hw) for a in self.analyzers}