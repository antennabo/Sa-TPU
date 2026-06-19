"""B8 · 导出对拍向量（per-tile 算子 + 期望，$readmemh hex）。

读 layer{n}_A_tiles/B_tiles.npy → 重建满矩阵（约定同 cycle_analyzer._reconstruct）
→ 算期望 C = A@B（int32，阵列原始输出，非 requant 后的 float）
→ 按每个输出 tile (mi,nj) 导三份 hex（A int8 / B int8 / C int32）+ manifest。

算子与期望是 **dataflow 无关**（natural row-major，RTL TB 自行 skew/倒序），OS/WS 共用一份。
hex 为二进制补码，每行一个元素，Verilog/SV $readmemh 直接读。
"""
import os
import numpy as np


def _hex(val: int, nbits: int) -> str:
    """二进制补码 hex（nbits 位 → nbits/4 位定宽）。"""
    return f"{val & ((1 << nbits) - 1):0{nbits // 4}x}"


def _write_hex(path: str, arr: np.ndarray, nbits: int):
    """row-major 每行一个元素写出（$readmemh 友好）。"""
    with open(path, "w") as f:
        for v in arr.reshape(-1):
            f.write(_hex(int(v), nbits) + "\n")


def export_cosim(tiles_dir: str, layer: str, out_dir: str) -> np.ndarray:
    """导出 layer 的对拍向量到 out_dir，返回期望 C（int32 满矩阵）。"""
    A_tiles = np.load(f"{tiles_dir}/{layer}_A_tiles.npy")  # (tm, tk, M, K)
    B_tiles = np.load(f"{tiles_dir}/{layer}_B_tiles.npy")  # (tk, tn, K, N)
    tm, tk, M, K = A_tiles.shape
    tk2, tn, K2, N = B_tiles.shape
    assert tk == tk2 and K == K2, "A/B tile 的 K 维不一致"

    # 重建满矩阵（与 cycle_analyzer._reconstruct 同约定）
    A = np.zeros((tm * M, tk * K), dtype=np.int8)
    for mi in range(tm):
        for kc in range(tk):
            A[mi * M:(mi + 1) * M, kc * K:(kc + 1) * K] = A_tiles[mi, kc]
    B = np.zeros((tk * K, tn * N), dtype=np.int8)
    for kc in range(tk):
        for nj in range(tn):
            B[kc * K:(kc + 1) * K, nj * N:(nj + 1) * N] = B_tiles[kc, nj]
    C = A.astype(np.int32) @ B.astype(np.int32)   # 期望：阵列原始 int32 输出

    os.makedirs(out_dir, exist_ok=True)
    K_full = tk * K
    lines = [f"# {layer} cosim vectors (dataflow-agnostic, natural row-major)",
             f"K_full={K_full} M_tile={M} N_tile={N} num_m={tm} num_n={tn} num_k={tk}",
             "# per tile: <tag>_A.hex (M x K_full, int8), _B.hex (K_full x N, int8), _C.hex (M x N, int32)",
             "tiles:"]
    for mi in range(tm):
        for nj in range(tn):
            tag = f"tile_m{mi}_n{nj}"
            a = A[mi * M:(mi + 1) * M, :]                       # (M, K_full) int8
            b = B[:, nj * N:(nj + 1) * N]                       # (K_full, N) int8
            c = C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N]     # (M, N) int32
            _write_hex(f"{out_dir}/{tag}_A.hex", a, 8)
            _write_hex(f"{out_dir}/{tag}_B.hex", b, 8)
            _write_hex(f"{out_dir}/{tag}_C.hex", c, 32)
            lines.append(f"  {tag}: A=({M}x{K_full}) B=({K_full}x{N}) C=({M}x{N})")
    with open(f"{out_dir}/manifest.txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    return C


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "build", "cosim")
    C = export_cosim(os.path.join(root, "build", "tiles"), "layer3", out)
    print(f"exported layer3 cosim vectors → {out}  C.shape={C.shape}")
