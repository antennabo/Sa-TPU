from abc import ABC, abstractmethod
import dataclasses
from ir import OpIR, ReductionOrder

class Transform(ABC):
    name: str

    def validate_input(self, op: OpIR) -> None:
        if not isinstance(op, OpIR):
            raise TypeError(f"期望 OpIR，收到 {type(op)}")

    @abstractmethod
    def transform(self, op: OpIR, **kwargs) -> OpIR:
        pass

    def run(self, op: OpIR, **kwargs) -> OpIR:
        self.validate_input(op)
        return self.transform(op, **kwargs)
    
class TilingTransform(Transform):
    name = "tiling"

    def transform(self, op, tile: dict) -> OpIR:
        return dataclasses.replace(op, tile=tile)
    
class MappingTransform(Transform):
    name = "mapping"

    def transform(self, op, mapping: dict, reduction_order: ReductionOrder) -> OpIR:
        return dataclasses.replace(op, mapping=mapping, reduction_order=reduction_order)


import dataclasses, numpy as np
from numpy_ops import conv2d, np_linear, np_relu, maxpool
from ir import Conv2dIR, MatMulIR, ElementwiseIR

def fill_activations(irs: list, x) -> list:
    """逐层传播激活，填入每个计算层的 input_data"""
    result = []
    x = x.astype(np.float32)
    for ir in irs:
        if isinstance(ir, Conv2dIR):
            result.append(dataclasses.replace(ir, input_data=x))
            x = np_relu(conv2d(x, ir.input_weight, ir.bias))
        elif isinstance(ir, MatMulIR):
            result.append(dataclasses.replace(ir, input_data=x))
            x = np_relu(np_linear(x, ir.input_weight, ir.bias))
        elif isinstance(ir, ElementwiseIR):
            result.append(ir)
            if ir.op == "relu":    x = np_relu(x)
            elif ir.op == "maxpool": x = maxpool(x)
            elif ir.op == "flatten": x = x.flatten()
        else:
            result.append(ir)
    return result
