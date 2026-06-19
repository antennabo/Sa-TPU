"""周期级 OS 矩阵乘单跑脚本：从 build/tiles/ 载入某层 tile，跑 CycleAccurateAnalyzer，打印结果。

用法（仓库根目录下）：
    python scripts/run_cycle.py [layer]   # layer 默认 layer3

只依赖 numpy（不走 run.py 的前端/torch 全链路），平时验证 cycle_analyzer 用这个。
"""
import logging
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
sys.path.insert(0, ROOT)

from compiler.hw import HardwareConfig
from simulator.cycle.cycle_analyzer import CycleAccurateAnalyzer
from compiler.instr import emit_matmul_program

logging.basicConfig(level=logging.INFO, format="%(message)s")

layer = sys.argv[1] if len(sys.argv) > 1 else "layer3"
tiles_dir = os.path.join(ROOT, "build/tiles")

hw = HardwareConfig(mxu_dim=(8, 8), sram_bytes=16 << 20, hbm_bw_gbps=900.0, freq_mhz=1.0)
ca = CycleAccurateAnalyzer(hw)
ca.load_tiles(tiles_dir, layer=layer)

tm, tk, M, K = ca.A_tiles.shape
_, tn, _, N = ca.B_tiles.shape
print(ca.A_tiles.shape)
print(ca.A_tiles)
print(ca.B_tiles.shape)
print(ca.B_tiles)
instrs = emit_matmul_program(tm, tk, tn)               # 标准 OS 调度（is_first/accum_addr）
print(instrs)
C = ca.simulate(instrs, mode="OS")                    # [simulate] 日志会打 numeric_ok

print(f"[{layer}] grid rows×cols×cpb = {tm}×{tn}×{tk}  C.shape={C.shape}  total_cycles={ca.total_cycles}")
