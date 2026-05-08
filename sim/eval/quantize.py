import dataclasses
from transform import Transform
from ir import OpIR, Conv2dIR, MatMulIR, ElementwiseIR

class QuantizationTransform(Transform):
    name = "quantization"

    def __init__(self, dtype="int8", accum_dtype="int32"):
        self.dtype = dtype
        self.accum_dtype = accum_dtype

    def transform(self, op: OpIR, **kwargs) -> OpIR:
        if isinstance(op, (Conv2dIR, MatMulIR)):
            return dataclasses.replace(op, dtype=self.dtype, accum_dtype=self.accum_dtype)
        return op  # ElementwiseIR 不量化，原样返回

def quantize_graph(irs: list, dtype="int8", accum_dtype="int32") -> list:
    t = QuantizationTransform(dtype, accum_dtype)
    return [t.run(op) for op in irs]
