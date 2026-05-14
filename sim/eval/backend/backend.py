from backend.hw import HardwareConfig
from backend.quantize import QuantizationTransform
from backend.transform import TilingTransform, MappingTransform
from backend.lowering import LoweringTransform
from backend.data import BackendData
from frontend.ir import Conv2dIR, MatMulIR, ElementwiseIR

class Backend:
    def __init__(self, hw: HardwareConfig):
        self.hw           = hw
        self.quantize_t   = QuantizationTransform(dtype=hw.dtype, accum_dtype=hw.accum_dtype)
        self.tiling_t     = TilingTransform()
        self.mapping_t    = MappingTransform()
        self.lowering     = LoweringTransform()
        self.irs          = []
        self.data         = BackendData()

    def run(self, irs: list, exported, x_np=None) -> tuple:
        self.fill_data(exported, x_np)
        self.irs, self.data = self.quantize(irs, self.data)
        self.irs, self.data = self.tile(self.irs, self.data)
        self.irs = self.map(self.irs)
        self.irs = self.compile(self.irs)
        self.print_data()
        return self.irs, self.data

    def fill_data(self, exported, x_np=None):
        """从 exported 提取权重，存入原始输入"""
        self.data.input_data = x_np
        param_map = {
            spec.arg.name: spec.target
            for spec in exported.graph_signature.input_specs
            if spec.kind.name == "PARAMETER"
        }
        state_dict = exported.state_dict
        for node in exported.graph.nodes:
            if node.op != "call_function":
                continue
            name = node.target.__name__ if hasattr(node.target, "__name__") else str(node.target)
            if "conv2d" in name or "linear" in name:
                W_node = node.args[1]
                b_node = node.args[2] if len(node.args) > 2 else None
                W = state_dict[param_map[W_node.target]].detach().numpy()
                b = state_dict[param_map[b_node.target]].detach().numpy() if b_node else None
                self.data.weights.append((W, b))

    def quantize(self, irs: list, data) -> list:
        irs, data = self.quantize_t.quantize_all(irs, data)
        return irs, data

    def tile(self, irs: list, data) -> tuple:
        irs, data = self.tiling_t.tile_all(irs, data, self.hw)
        return irs, data

    def map(self, irs: list) -> list:
        # TODO: 从 hw 自动推导 mapping 参数
        return irs

    def compile(self, irs: list) -> list:
        # TODO: Compiler.layer2ops → instr_queue
        return irs

    def print_data(self):
        print(f"=== Backend Data ===")
        print(f"  instr_queue: {len(self.data.instr_queue)} tiles")
        for i, instr in enumerate(self.data.instr_queue):
            print(f"    [{i}] A_tile={instr['A_tile'].shape} B_tile={instr['B_tile'].shape}")

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