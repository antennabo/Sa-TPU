from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod
from typing import Optional

# --- 枚举 ---
class ReductionOrder(Enum):
    SEQUENTIAL = "sequential"# systolic array
    TREE       = "tree" # do not support now

# --- 抽象基类（只放共性字段，不放方法）---
@dataclass(frozen=True)
class OpIR(ABC):
    op_type: str
    dtype: str # "fp32" / "int8"

# --- 子类---
@dataclass(frozen=True)
class MatMulIR(OpIR):
    M: int
    N: int
    K: int
    accum_dtype: str # "fp32" / "int32"
    tile:   Optional[dict] = None #{"tm":64,"tn":64,"tk":64}
    reduction_order: Optional[ReductionOrder] = None
    mapping:         Optional[dict] = None   # MXU 维度绑定
    schedule:        None           = None   # cycle-accurate 预留

@dataclass(frozen=True)
class Conv2dIR(OpIR):
    accum_dtype: str # "fp32" / "int32"
    N: int # batch size
    H: int # hight
    W: int # width
    C: int # channels

    K: int # filter num
    R: int # kernel height
    S: int # kernel width
    
    padding: int = 1
    stride:  int = 1
    tile:            Optional[dict]           = None
    reduction_order: Optional[ReductionOrder] = None
    mapping:         Optional[dict]           = None
    schedule:        None                     = None

@dataclass(frozen=True)
class ElementwiseIR(OpIR):
    op:     str          # "relu" / "maxpool" / "flatten"
    shape:  tuple        # 输入 shape，例如 (1, 8, 28, 28)

# TODO: FusedConvReluIR
@dataclass(frozen=True)
class FusedConvReluIR(Conv2dIR):
    pass