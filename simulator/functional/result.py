from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class AnalysisResult:
    pass

@dataclass(frozen=True)
class MemoryResult(AnalysisResult):
    sram_bytes: int
    hbm_bytes: int
    fits: bool          # sram_bytes <= hw.sram_bytes

@dataclass(frozen=True)
class PerfResult(AnalysisResult):
    model_name: str              # "roofline" / "cycle_accurate"
    confidence: str              # "approximate" / "accurate"
    latency_ns: float
    utilization: float
    throughput_ops: float        # ops / latency_ns，单位 GOPS
    # 可选 cycle 字段（占位）
    pipeline_bubbles: Optional[int] = None
    bank_conflicts:   Optional[int] = None

