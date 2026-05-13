import dataclasses
from abc import ABC, abstractmethod
from frontend.ir import OpIR, ReductionOrder

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

    def transform(self, op: OpIR, tile: dict) -> OpIR:
        return dataclasses.replace(op, tile=tile)


class MappingTransform(Transform):
    name = "mapping"

    def transform(self, op: OpIR, mapping: dict, reduction_order: ReductionOrder) -> OpIR:
        return dataclasses.replace(op, mapping=mapping, reduction_order=reduction_order)
