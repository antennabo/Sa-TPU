from ir import OpIR, Conv2dIR, MatMulIR, ElementwiseIR
from transform import Transform

class LoweringTransform(Transform):
    name = "lowering"

    def transform(self, op: OpIR, **kwargs) -> OpIR:
        # Conv2dIR 和 MatMulIR 保留不变
        # ElementwiseIR 保留不变
        # 未知类型报错
        if isinstance(op, (Conv2dIR, MatMulIR, ElementwiseIR)):
            return op
        raise NotImplementedError(f"不支持的 IR 类型: {type(op)}")

def lower_graph(irs: list) -> list:
    t = LoweringTransform()
    return [t.run(op) for op in irs]