from abc import ABC, abstractmethod
from compiler.frontend.ir import OpIR, MatMulIR, Conv2dIR
from compiler.hw import HardwareConfig
from .result import AnalysisResult, PerfResult, MemoryResult

class Analyzer(ABC):
    name: str  # 子类定义，例如 "roofline" / "memory"
    debug: bool = False

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

    @staticmethod
    def _dtype_bytes(dtype):
        if dtype in ("fp16", "int16"): return 2
        if dtype == "int8":            return 1
        if dtype == "int4":            return 0.5
        return 4
    
    # def analyze(self, op, hw):
    #     if op.dtype in ("fp16", "int16"):
    #         dtype_bytes = 2
    #     elif op.dtype == "int8":
    #         dtype_bytes = 1
    #     elif op.dtype == "int4":
    #         dtype_bytes = 0.5
    #     else:
    #         dtype_bytes = 4
    #     # 每个输出元素需要K次乘法和K次加法
    #     # output 元素数 = M × N
    #     # 每个元素的运算 = K × (multiply + add) = 2K FLOPs
    #     ops   =  2 * op.M * op.N * op.K         # MACs × 2
    #     bytes_num = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes
    #     arithmetic_intensity = ops / bytes_num

    #     peak_flops_per_ns = hw.freq_mhz * 1e6 * hw.mxu_dim[0] * hw.mxu_dim[1] * 2 / 1e9
    #     peak_bw_per_ns    = hw.hbm_bw_gbps / 8          # GB/s → bytes/ns

    #     bound_flop = ops / peak_flops_per_ns
    #     bound_mem  = bytes_num / peak_bw_per_ns
    #     latency_ns = max(bound_flop, bound_mem)
    #     utilization = bound_flop / latency_ns        # 越接近1越好
    #     return PerfResult(
    #         latency_ns=latency_ns,
    #         utilization=utilization,
    #         throughput_ops=ops / latency_ns,
    #         model_name="roofline",
    #         confidence="approximate",
    #     )

    def analyze(self, op, hw):
        if isinstance(op, Conv2dIR):
            return self.analyze_conv(op, hw)
        elif isinstance(op, MatMulIR):
            return self.analyze_linear(op, hw)
        else:
            raise NotImplementedError(f"Not supported {type(op).__name__}")
    
    def analyze_linear(self, op, hw):
        dtype_bytes = self._dtype_bytes(op.dtype)
        ops       = 2 * op.M * op.N * op.K      # ×2: each MAC = 1 multiply + 1 add
        bytes_num = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes

        # MHz×1e6 → Hz, ÷1e9 → per-ns; the following is also the GFLOPs
        peak_flops_per_ns = hw.freq_mhz * 1e6 * hw.mxu_dim[0] * hw.mxu_dim[1] * 2 / 1e9
        # G and n cancel so Gbps/8 = bytes/ns directly
        peak_bw_per_ns    = hw.hbm_bw_gbps / 8

        bound_flop  = ops / peak_flops_per_ns
        bound_mem   = bytes_num / peak_bw_per_ns
        latency_ns  = max(bound_flop, bound_mem)        # bottleneck determines actual latency
        utilization = bound_flop / latency_ns           # <1 means memory-bound, MXU stalls waiting for data
        return PerfResult(
            latency_ns=latency_ns,
            utilization=utilization,
            throughput_ops=ops / latency_ns,
            model_name="roofline",
            confidence="approximate",
        )

    # def analyze_conv(self, op, hw):

    #     if op.dtype in ("fp16", "int16"):
    #         dtype_bytes = 2
    #     elif op.dtype == "int8":
    #         dtype_bytes = 1
    #     elif op.dtype == "int4":
    #         dtype_bytes = 0.5
    #     else:
    #         dtype_bytes = 4

    #     H_out = (op.H + 2*op.padding - op.R) // op.stride + 1#(op.padding)
    #     W_out = (op.W + 2*op.padding - op.S) // op.stride + 1

    #     ops = H_out * W_out * op.R * op.S * op.C * op.K * op.N
    #     # op.N*op.K*H_out*W_out 输出 feature map
    #     # K*C*R*S 卷积核 大小
    #     # N*C*H*W 输入Feature Map
    #     # bytes     = (N*C*H*W + K*C*R*S + op.N*op.K*H_out*W_out) * dtype_bytes
    def analyze_conv(self, op, hw):
        dtype_bytes = self._dtype_bytes(op.dtype)
        H_out = (op.H + 2*op.padding - op.R) // op.stride + 1
        W_out = (op.W + 2*op.padding - op.S) // op.stride + 1

        ops       = 2 * op.N * op.K * H_out * W_out * op.C * op.R * op.S
        bytes_num = (op.N*op.C*op.H*op.W + op.K*op.C*op.R*op.S + op.N*op.K*H_out*W_out) * dtype_bytes

        peak_flops_per_ns = hw.freq_mhz * 1e6 * hw.mxu_dim[0] * hw.mxu_dim[1] * 2 / 1e9
        peak_bw_per_ns    = hw.hbm_bw_gbps / 8

        bound_flop  = ops / peak_flops_per_ns
        bound_mem   = bytes_num / peak_bw_per_ns
        latency_ns  = max(bound_flop, bound_mem)
        utilization = bound_flop / latency_ns
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

    # def analyze(self, op, hw):
    #     if op.dtype in ("fp16", "int16"):
    #         dtype_bytes = 2
    #     elif op.dtype == "int8":
    #         dtype_bytes = 1
    #     elif op.dtype == "int4":
    #         dtype_bytes = 0.5
    #     else:
    #         dtype_bytes = 4
    #     # M*K A矩阵
    #     # K*N B矩阵
    #     # M*N 输出矩阵
    #     hbm_bytes = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes
    #     if op.tile == None:
    #         sram_bytes = hbm_bytes
    #     else:
    #         sram_bytes = (op.tile["tm"]*op.tile["tk"] + op.tile["tk"]*op.tile["tn"] + op.tile["tm"]*op.tile["tn"]) * dtype_bytes
    #     fits = sram_bytes <= hw.sram_bytes
    #     return MemoryResult(sram_bytes=sram_bytes,hbm_bytes=hbm_bytes,fits=fits)

    def analyze(self, op, hw):
        if isinstance(op, Conv2dIR):
            return self._analyze_conv(op, hw)
        elif isinstance(op, MatMulIR):
            return self._analyze_linear(op, hw)
        else:
            raise NotImplementedError(f"不支持 {type(op).__name__}")
        
    @staticmethod
    def _dtype_bytes(dtype):
        if dtype in ("fp16", "int16"): return 2
        if dtype == "int8":            return 1
        if dtype == "int4":            return 0.5
        return 4

    def _analyze_linear(self, op, hw):
        dtype_bytes = self._dtype_bytes(op.dtype)
        hbm_bytes = (op.M*op.K + op.K*op.N + op.M*op.N) * dtype_bytes
        if op.tile is None:
            sram_bytes = hbm_bytes
        else:
            sram_bytes = (op.tile["tm"]*op.tile["tk"] + op.tile["tk"]*op.tile["tn"] + op.tile["tm"]*op.tile["tn"]) * dtype_bytes
        return MemoryResult(sram_bytes=sram_bytes, hbm_bytes=hbm_bytes, fits=sram_bytes <= hw.sram_bytes)

    def _analyze_conv(self, op, hw):
        dtype_bytes = self._dtype_bytes(op.dtype)
        H_out = (op.H + 2*op.padding - op.R) // op.stride + 1
        W_out = (op.W + 2*op.padding - op.S) // op.stride + 1
        hbm_bytes = (op.N*op.C*op.H*op.W + op.K*op.C*op.R*op.S + op.N*op.K*H_out*W_out) * dtype_bytes
        # Conv tile 键未定义，暂时无 tile 时用全量作为 sram 估计
        sram_bytes = hbm_bytes
        return MemoryResult(sram_bytes=sram_bytes, hbm_bytes=hbm_bytes, fits=sram_bytes <= hw.sram_bytes)


class AnalysisPipeline:
    def __init__(self, analyzers: list[Analyzer]):
        self.analyzers = analyzers

    def run(self, op, hw) -> dict:
        results = {}
        for a in self.analyzers:
            try:
                results[type(a).__name__] = a.run(op, hw)
            except (NotImplementedError, AttributeError, ValueError):
                results[type(a).__name__] = None
        return results

    def run_graph(self, ops: list, hw: HardwareConfig) -> dict:
        results = {}
        for i, op in enumerate(ops):
            try:
                results[f"{op.op_type}_{i}"] = self.run(op, hw)
            except (NotImplementedError, AttributeError, ValueError):
                results[f"{op.op_type}_{i}"] = None
        return results