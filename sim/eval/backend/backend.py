from backend.hw import HardwareConfig
from backend.quantize import QuantizationTransform
from backend.transform import TilingTransform, MappingTransform
from backend.lowering import LoweringTransform
from frontend.ir import Conv2dIR, MatMulIR, ElementwiseIR

class Backend:
    def __init__(self, hw: HardwareConfig):
        self.hw           = hw
        self.quantize_t   = QuantizationTransform(dtype=hw.dtype, accum_dtype=hw.accum_dtype)
        self.tiling_t     = TilingTransform()
        self.mapping_t    = MappingTransform()
        self.lowering     = LoweringTransform()
        self.irs          = []

    def run(self, irs: list) -> list:
        self.irs = self.quantize(irs)
        self.irs = self.tile(self.irs)
        self.irs = self.map(self.irs)
        self.irs = self.compile(self.irs)
        return self.irs

    def quantize(self, irs: list) -> list:
        return [self.quantize_t.run(op) for op in irs]

    def tile(self, irs: list) -> list:
        # TODO: 从 hw 自动推导 tile 参数
        return irs

    def map(self, irs: list) -> list:
        # TODO: 从 hw 自动推导 mapping 参数
        return irs

    def compile(self, irs: list) -> list:
        # TODO: Compiler.layer2ops → instr_queue
        return irs

    def print_irs(self):
        print(f"=== Backend IR ({len(self.irs)} ops) ===")
        for i, op in enumerate(self.irs):
            if isinstance(op, Conv2dIR):
                print(f"  [{i}] Conv2d  N={op.N} H={op.H} W={op.W} C={op.C} K={op.K} R={op.R} S={op.S} dtype={op.dtype}/{op.accum_dtype}")
            elif isinstance(op, MatMulIR):
                print(f"  [{i}] MatMul  M={op.M} N={op.N} K={op.K} dtype={op.dtype}/{op.accum_dtype}")
            elif isinstance(op, ElementwiseIR):
                print(f"  [{i}] {op.op:<10} shape={op.shape}")

    # def fuse(self, irs: list) -> list:
    #     # TODO: FusedConvReluIR pattern matching
    #     return irs

    # def lower(self, irs: list) -> list:
    #     return lower_graph(irs)