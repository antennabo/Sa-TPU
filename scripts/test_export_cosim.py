"""B8 · 对拍向量导出测试：hex 能还原回整数值，且期望 C == A@B。"""
import os

import numpy as np

from scripts.export_cosim import export_cosim, _hex


TILES = os.path.join(os.path.dirname(__file__), "..", "build", "tiles")


def _read_hex(path, nbits):
    """读回 $readmemh hex → int 列表（二进制补码还原符号）。"""
    sign = 1 << (nbits - 1)
    out = []
    with open(path) as f:
        for line in f:
            v = int(line.strip(), 16)
            out.append(v - (1 << nbits) if v & sign else v)
    return out


def test_hex_twos_complement():
    assert _hex(-3, 8) == "fd" and _hex(5, 8) == "05"
    assert _hex(-3, 32) == "fffffffd" and _hex(127, 32) == "0000007f"


def test_export_roundtrip_and_golden(tmp_path):
    out = str(tmp_path)
    C = export_cosim(TILES, "layer3", out)

    A_tiles = np.load(f"{TILES}/layer3_A_tiles.npy")
    B_tiles = np.load(f"{TILES}/layer3_B_tiles.npy")
    tm, tk, M, K = A_tiles.shape
    _, tn, _, N = B_tiles.shape
    K_full = tk * K

    for mi in range(tm):
        for nj in range(tn):
            tag = f"tile_m{mi}_n{nj}"
            a = np.array(_read_hex(f"{out}/{tag}_A.hex", 8)).reshape(M, K_full)
            b = np.array(_read_hex(f"{out}/{tag}_B.hex", 8)).reshape(K_full, N)
            c = np.array(_read_hex(f"{out}/{tag}_C.hex", 32)).reshape(M, N)
            # hex 还原的算子相乘 == 导出的期望 tile == 全局 C 的对应块
            assert np.array_equal(a.astype(np.int32) @ b.astype(np.int32), c)
            assert np.array_equal(c, C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N])
    assert os.path.exists(f"{out}/manifest.txt")
