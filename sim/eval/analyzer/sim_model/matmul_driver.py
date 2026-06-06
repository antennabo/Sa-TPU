"""MatMul 驱动 / 序列器（OS dataflow）。

把任意 `A[Gm,Gk] @ B[Gk,Gn]` 按 M×N 阵列切块，用已验证的
controller + wb/ab(CommonFIFO) + spatial_array + accumulator 跑完，返回结果与周期数。

调度（最简，复用已验证路径）：输出稳定、块间流水、块内 K-chunk 无缝累加（不 spill/restore）。
  - 输出块 (mi,nj) → tile_id = mi*(Gn/N)+nj，按此序背靠背喂。
  - 块内 cpb=Gk/K 个 K-chunk 无缝累加在 PE psum（avail 恒高 → 不需 restore）。
  - new_tile 只在块间边界拍拉高：cy>0 且 cy%Gk==0 且 cy<nblocks*Gk
    （块内 K-chunk 边界 cy%Gk!=0 → new_tile=False，psum 继续累加）。
"""
import numpy as np

from .controller import Controller
from .commonfifo import CommonFIFO
from .spatial_array import spatial_array
from .accumulator import Accumulator


def run_matmul(A, B, M, N, K, latency=1):
    """跑一次分块矩阵乘，返回 (C[Gm,Gn] int32, n_cycles)。形状须被阵列整除（暂不支持 padding）。"""
    A = np.asarray(A)
    B = np.asarray(B)
    Gm, Gk = A.shape
    Gk2, Gn = B.shape
    assert Gk == Gk2, f"A、B 收缩维不匹配：{Gk} vs {Gk2}"
    assert Gm % M == 0 and Gn % N == 0 and Gk % K == 0, (
        f"形状须被阵列整除：Gm%M={Gm % M} Gn%N={Gn % N} Gk%K={Gk % K}（暂不支持 padding）")
    rows, cols, cpb = Gm // M, Gn // N, Gk // K
    nblocks = rows * cols
    assert nblocks <= Accumulator.NUM_TILES, f"块数 {nblocks} 超过 accumulator 容量 {Accumulator.NUM_TILES}"

    ctrl  = Controller(M, N, K, drain_delay=latency + 1)
    wb    = CommonFIFO(N, K)
    ab    = CommonFIFO(M, K)
    sa    = spatial_array(M, N, latency=latency)
    accum = Accumulator(num_rows=M, num_cols=N)
    for m in (ctrl, wb, ab, sa, accum):
        m.reset()
    ZEROS = [[0] * N for _ in range(M)]

    # 预载所有块的所有 K-chunk（feed 序：tile_id 升序，块内 kc 升序，各 chunk K 条深度向量）。
    #   wb lane c = B[kc*K:(kc+1)*K, nj*N+c]，ab lane r = A[mi*M+r, kc*K:(kc+1)*K]
    for mi in range(rows):
        for nj in range(cols):
            for kc in range(cpb):
                for kk in range(K):
                    kd = kc * K + kk
                    wb.update([int(B[kd, nj * N + c]) for c in range(N)], [0] * N); wb.commit()
                    ab.update([int(A[mi * M + r, kd]) for r in range(M)], [0] * M); ab.commit()

    n_run = nblocks * Gk + M + N + latency + 10
    last_write = 0
    for cy in range(n_run):
        new_tile = cy > 0 and cy % Gk == 0 and cy < nblocks * Gk
        ctrl.update(cy, wb.avail, ab.avail, new_tile=new_tile)
        wb.update(None, ctrl.read_weight)
        ab.update(None, ctrl.read_activation)
        accum.update(sa.data, ctrl.acc, ctrl.acc_read)
        sa.update(ab.data, wb.data, 1, 1, 0, 0, ZEROS, ctrl.acc_read)
        ctrl.commit(); wb.commit(); ab.commit(); accum.commit(); sa.commit()
        if any(ctrl.acc[r][c] is not None for r in range(M) for c in range(N)):
            last_write = cy

    C = np.zeros((Gm, Gn), dtype=np.int32)
    for mi in range(rows):
        for nj in range(cols):
            C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N] = np.array(accum.get_tile(mi * cols + nj))
    return C, last_write + 1
