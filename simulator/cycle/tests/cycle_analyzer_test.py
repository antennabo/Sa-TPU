"""CycleAccurateAnalyzer 集成数值正确性测试（OS 步 1a 非重叠）。

验证 simulate() 把 controller + commonfifo(wb/ab) + spatial_array + accumulator 按指令流
（emit_matmul_program 的 is_first/accum_addr）串成的逐块 OS 计算 == A @ B。

注：依赖仓库根在 import path 上（from compiler.hw / simulator.cycle...），
由仓库根的 conftest.py 注入。
"""
import numpy as np
import pytest

from compiler.hw import HardwareConfig
from simulator.cycle.cycle_analyzer import CycleAccurateAnalyzer
from compiler.instr import emit_matmul_program


def _run(tm, tk, tn, S, seed):
    """合成 (tm,tk,S,S) / (tk,tn,S,S) tile（方阵 S=阵列边长），跑 simulate，返回 (C, ref, cycles)。"""
    rng = np.random.default_rng(seed)
    A_tiles = rng.integers(-3, 4, size=(tm, tk, S, S)).astype(np.int8)
    B_tiles = rng.integers(-3, 4, size=(tk, tn, S, S)).astype(np.int8)
    hw = HardwareConfig(mxu_dim=(S, S), sram_bytes=16 << 20, hbm_bw_gbps=900.0, freq_mhz=1.0)
    ca = CycleAccurateAnalyzer(hw)
    ca.A_tiles, ca.B_tiles = A_tiles, B_tiles
    instrs = emit_matmul_program(num_m=tm, num_k=tk, num_n=tn)
    C = ca.simulate(instrs, mode="OS")
    A, B = ca._reconstruct()
    ref = A.astype(np.int32) @ B.astype(np.int32)
    return C, ref, ca.total_cycles


@pytest.mark.parametrize("tm,tk,tn", [
    (1, 1, 1),    # 单块单 K-chunk
    (1, 8, 2),    # layer3 形状：单行、cpb=8、2 列块
    (2, 2, 2),    # 行×列分块 + K-chunk
    (1, 4, 3),    # 多列块 + 多 K-chunk
    (3, 1, 1),    # 仅行分块
])
def test_simulate_numeric_os(tm, tk, tn):
    C, ref, cycles = _run(tm, tk, tn, S=8, seed=tm * 100 + tk * 10 + tn)
    np.testing.assert_array_equal(C, ref)
    assert cycles > 0


def test_cycles_sum_of_blocks():
    # 步 1a 非重叠：total_cycles = nblocks 块各自周期之和（块越多周期成倍增）
    _, _, c1 = _run(1, 2, 1, S=8, seed=1)      # 1 块
    _, _, c2 = _run(2, 2, 1, S=8, seed=1)      # 2 块（同形状，行分块）
    assert c2 == 2 * c1
