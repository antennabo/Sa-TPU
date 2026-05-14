import dataclasses
from backend.transform import Transform
from backend.data import BackendData
from frontend.ir import OpIR, Conv2dIR, MatMulIR, ElementwiseIR
from utils.utils import quantize_int8

class QuantizationTransform(Transform):
    name = "quantization"

    def __init__(self, dtype="int8", accum_dtype="int32"):
        self.dtype = dtype
        self.accum_dtype = accum_dtype

    def transform(self, op: OpIR, **kwargs) -> OpIR:
        if isinstance(op, (Conv2dIR, MatMulIR)):
            return dataclasses.replace(op, dtype=self.dtype, accum_dtype=self.accum_dtype)
        return op  # ElementwiseIR 不量化，原样返回

    def quantize_all(self, irs: list, data: BackendData) -> tuple:
        quantized_irs = []
        quantized_weights = []
        wi = 0
        for op in irs:
            if isinstance(op, (Conv2dIR, MatMulIR)):
                W, b = data.weights[wi]; wi += 1
                W_int8, _, scale_w = quantize_int8(W)
                quantized_weights.append((W_int8, b, scale_w))
                quantized_irs.append(dataclasses.replace(op, dtype=self.dtype, accum_dtype=self.accum_dtype))
            else:
                quantized_irs.append(op)
        data.weights = quantized_weights
        return quantized_irs, data

def quantize_graph(irs: list, dtype="int8", accum_dtype="int32") -> list:
    t = QuantizationTransform(dtype, accum_dtype)
    return [t.run(op) for op in irs]
