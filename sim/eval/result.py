from dataclasses import dataclass
from ir import ReductionOrder

@dataclass(frozen=True)
class AnalysisResult:
    pass

@dataclass(frozen=True)
class MemoryResult(AnalysisResult):
    sram_bytes: int
    hbm_bytes: int
    fits: bool          # sram_bytes <= hw.sram_bytes

@dataclass(frozen=True)
class NumericalResult(AnalysisResult):
    max_error: float
    mean_error: float
    reduction_order_used: ReductionOrder   # 必填，记录用了哪种累加
    accum_overflow: bool
    warnings: tuple = ()   # 用 tuple 代替 list

@dataclass(frozen=True)
class PerfResult(AnalysisResult):
    latency_ns: float
    utilization: float
    # throughput_ops: float  # 可选，阶段0先不加