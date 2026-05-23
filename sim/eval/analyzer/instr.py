"""
Sa-TPU ISA instruction definitions (doc/isa.txt).
Each instruction is 64-bit; fields match the ISA spec.
"""
from dataclasses import dataclass
from enum import IntEnum


class Opcode(IntEnum):
    LOAD_WGT = 0
    MATMUL   = 1
    ACTIVATE = 2


@dataclass
class LoadWgtInstr:
    """LOAD_WGT: DDR → Weight FIFO
    [63:56] opcode | [55:24] dram_addr | [23:20] length | [19:0] reserved
    """
    dram_addr: int          # 32-bit, byte-aligned DRAM address
    length:    int = 1      # 4-bit, number of tiles (max 16)
    opcode:    int = Opcode.LOAD_WGT


@dataclass
class MatMulInstr:
    """MATMUL: UB x Weight FIFO → MXU → Accumulators
    [63:56] opcode | [55:40] ub_addr | [39:36] accum_addr | [35:32] length
    | [31] is_first | [30] is_last | [29:0] reserved
    """
    ub_addr:    int          # 16-bit, activation start addr in UB
    accum_addr: int          # 4-bit, result start addr in Accumulator (0..15)
    length:     int = 1      # 4-bit, number of tiles to compute (max 16)
    is_first:   bool = True  # clear Accumulator before accumulating
    is_last:    bool = True  # last K-tile for this output tile
    opcode:     int = Opcode.MATMUL


@dataclass
class ActivateInstr:
    """ACTIVATE: Accumulators → (nonlinear) → UB
    [63:56] opcode | [55:40] ub_addr | [39:36] accum_addr | [35:32] func | [31:0] reserved
    """
    ub_addr:    int          # 16-bit, output start addr in UB
    accum_addr: int          # 4-bit, input start addr in Accumulator (0..15)
    func:       int = 0      # 4-bit activation function (0=Linear, 1=ReLU, ...)
    opcode:     int = Opcode.ACTIVATE
