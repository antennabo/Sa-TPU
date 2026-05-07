from abc import ABC, abstractmethod
from ir import OpIR, MatMulIR
from hw import HardwareConfig
from result import AnalysisResult, PerfResult, MemoryResult

class Analyzer(ABC):
    @abstractmethod
    def analyze(self, op: OpIR, hw: HardwareConfig) -> AnalysisResult:
        pass

class RooflinePerfAnalyzer(Analyzer):
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
        return PerfResult(latency_ns=latency_ns,utilization=utilization)
    
class MemoryAnalyzer(Analyzer):
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

class AnalysisPipeline:
    def __init__(self, analyzers: list[Analyzer]):
        self.analyzers = analyzers

    def run(self, op, hw) -> dict:
        return {type(a).__name__: a.analyze(op, hw) for a in self.analyzers}