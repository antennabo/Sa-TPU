import logging
import numpy as np
from collections import deque
from simulator.functional.analyzer import Analyzer

logger = logging.getLogger(__name__)
from compiler.frontend.ir import OpIR
from compiler.hw import HardwareConfig
from simulator.functional.result import PerfResult
from .sim_model.spatial_array import spatial_array
from .sim_model.commonfifo import CommonFIFO
from .sim_model.accumulator import Accumulator
from .sim_model.controller import Controller

class CycleAccurateAnalyzer(Analyzer):
    name = "cycle_accurate"

    def __init__(self, hw: HardwareConfig = None):
        self.total_cycles = 0
        self.all_modules  = []
        self.instr_queue: deque = deque()  # ISA-level instruction queue
        self._ops         = 0
        self.A_tiles = []
        self.B_tiles = []
        self.weight_tile_queue:     deque = deque()
        self.activation_tile_queue: deque = deque()
        self.hw    = None
        self.sa    = None
        self.wb    = None
        self.ab    = None
        self.accum = None
        self.ctrl  = None
        self._K    = None
        if hw is not None:
            self._setup(hw)

    def _setup(self, hw: HardwareConfig):
        dtype_map = {"int8": np.int8, "int16": np.int16, "int32": np.int32, "float32": np.float32}
        M, N = hw.mxu_dim
        self.hw         = hw
        self.M, self.N  = M, N
        self._dtype_in  = dtype_map[hw.dtype]
        self._dtype_acc = dtype_map[hw.accum_dtype]
        self._latency   = getattr(hw, "pe_latency", 2)  # PE 流水级数（drain/restore 延迟 = latency+1）
        # module 在 simulate() 拿到 tile 的 K 后再建（K=sizek，_setup 时未知）

    def analyze(self, op: OpIR, hw: HardwareConfig, A: np.ndarray, B: np.ndarray) -> PerfResult:
        self.hw           = hw
        self.instr_queue  = []
        self.total_cycles = 0

    def load_tiles(self, tiles_dir: str, layer: str = "layer3"):
        """
        A_tiles 原始: (tm, tk, sizem, sizek) → reshape → (tm*tk, sizem, sizek)
        B_tiles 原始: (tk, tn, sizek, sizen) → reshape → (tk*tn, sizek, sizen)
        """
        self.A_tiles = np.load(f"{tiles_dir}/{layer}_A_tiles.npy")  # (tm, tk, sizem, sizek)
        self.B_tiles = np.load(f"{tiles_dir}/{layer}_B_tiles.npy")  # (tk, tn, sizek, sizen)
        self.bias = np.load(f"{tiles_dir}/{layer}_bias.npy")

    def _reconstruct(self):
        """把 4D tile 拼回完整 A[Gm,Gk]、B[Gk,Gn]。
        A_tiles (tm,tk,M,K)：A[mi*M:.., kc*K:..]=A_tiles[mi,kc]
        B_tiles (tk,tn,K,N)：B[kc*K:.., nj*N:..]=B_tiles[kc,nj]
        """
        tm, tk, M, K = self.A_tiles.shape
        tk2, tn, K2, N = self.B_tiles.shape
        assert tk == tk2 and K == K2, f"A/B tile 的 K 维不一致：A tk={tk},K={K} vs B tk={tk2},K={K2}"
        A = np.zeros((tm * M, tk * K), dtype=self.A_tiles.dtype)
        for mi in range(tm):
            for kc in range(tk):
                A[mi * M:(mi + 1) * M, kc * K:(kc + 1) * K] = self.A_tiles[mi, kc]
        B = np.zeros((tk * K, tn * N), dtype=self.B_tiles.dtype)
        for kc in range(tk):
            for nj in range(tn):
                B[kc * K:(kc + 1) * K, nj * N:(nj + 1) * N] = self.B_tiles[kc, nj]
        return A, B

    def _run_block(self, A_blk, B_blk, M, N, K, slot, accum):
        """单个输出块（M×N）的周期级 OS 计算（步 1a 非重叠，与 os_multitile_e2e._run_block 同款）：
        fresh ctrl/ab/wb/sa（= acc_clr 把上块 psum 彻底清空），块内 cpb 个 K-chunk 无缝累加在 PE psum，
        drain mux 出 sa.output[N] → accum 的 slot 槽。返回本块用掉的周期数（last_write+1）。"""
        L = self._latency
        Gk = A_blk.shape[1]
        sa = spatial_array(M, N, dtype_in=self._dtype_in, dtype_acc=self._dtype_acc, latency=L,
                           is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0)
        ab = CommonFIFO(N=M, K=K)                   # 激活：M lane(行)
        wb = CommonFIFO(N=N, K=K)                   # 权重：N lane(列)
        ctrl = Controller(M, N, K, mode="OS", latency=L)
        for kd in range(Gk):                        # 预载本块全部 K-chunk（feed 序，各 K 深）
            ab.update([int(A_blk[r, kd]) for r in range(M)], False); ab.commit()
            wb.update([int(B_blk[kd, c]) for c in range(N)], False); wb.commit()

        last_write = 0
        for cy in range(Gk + M + N + 2 * K + L + 12):
            avail = cy < Gk                         # 喂 cpb*K 拍 → DRAIN
            ctrl.update(cy, weight_available=avail, activ_available=avail,
                        new_tile=(cy == 0), tile_id=slot)
            ab.update(None, ctrl.feed); wb.update(None, ctrl.feed)
            sa.update(list(ab.data), [True] * M, list(wb.data), [True] * N,
                      output_sel=list(ctrl.output_sel))
            sel = [r for r in range(M) if ctrl.output_sel[r]]   # output_sel ≤1 True → 标量行
            vld = len(sel) == 1
            accum.update(list(sa.output), vld, sel[0] if vld else 0, slot, False)
            ctrl.commit(); ab.commit(); wb.commit(); sa.commit(); accum.commit()
            if vld:
                last_write = cy
        return last_write + 1

    def simulate(self, instrs, mode: str = "OS"):
        """从 load_tiles 的 tile 跑周期级 OS 矩阵乘（步 1a 非重叠）。块边界/槽号由 backend 产出的指令流
        instrs 驱动（is_first=新块、accum_addr=accum 槽；backend.compile → data.programs[layer]）。
        每个输出块独立跑（_run_block），共享 accumulator；total_cycles = 各块周期之和（块间不重叠）。"""
        assert mode == "OS" or "WS", f"cycle_analyzer 暂只支持 OS，收到 {mode}"
        self._mode = mode
        tm, tk, M, K = self.A_tiles.shape
        tkb, tn, Kb, N = self.B_tiles.shape
        assert tk == tkb, f"A/B 的 tk 不一致：{tk} vs {tkb}"
        assert M == self.M and K == self.N, f"A tile {M}x{K} 与阵列 {self.M}x{self.N} 不符"
        assert Kb == self.M and N == self.N, f"B tile {Kb}x{N} 与阵列 {self.M}x{self.N} 不符"
        self._K = K
        rows, cols, cpb = tm, tn, tk
        nblocks = rows * cols
        assert nblocks <= Accumulator.NUM_TILES, f"块数 {nblocks} 超过 accumulator 容量 {Accumulator.NUM_TILES}"
        assert len(instrs) == nblocks * cpb, (
            f"指令数 {len(instrs)} 与 tile 网格 nblocks×cpb={nblocks}×{cpb} 不符")

        A, B = self._reconstruct()
        accum = Accumulator(num_rows=M, num_cols=N, cap_delay=1)

        # 按 is_first 把指令流切成输出块（每块 cpb 条 K-chunk），slot=accum_addr 决定 (mi,nj)
        self.total_cycles = 0
        c = 0
        while c < len(instrs):
            assert instrs[c].is_first, f"块首指令 {c} 的 is_first 应为 True"
            slot = instrs[c].accum_addr
            blk = instrs[c:c + cpb]
            assert all(i.accum_addr == slot for i in blk), f"块 slot={slot} 的 accum_addr 不一致"
            assert blk[-1].is_last, f"块 slot={slot} 末指令 is_last 应为 True"
            mi, nj = slot // cols, slot % cols
            A_blk = A[mi * M:(mi + 1) * M, :]
            B_blk = B[:, nj * N:(nj + 1) * N]
            self.total_cycles += self._run_block(A_blk, B_blk, M, N, K, slot, accum)
            c += cpb

        self.accum = accum
        # 拼回结果 + 自检（基准 = 拼回的 int32 A@B；out.npy 是反量化后的最终输出，不在此比）
        C = np.zeros((rows * M, cols * N), dtype=np.int32)
        for mi in range(rows):
            for nj in range(cols):
                C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N] = np.array(accum.get_tile(mi * cols + nj))
        self.result = C
        ref = A.astype(np.int32) @ B.astype(np.int32)
        logger.info("[simulate] total_cycles=%d  numeric_ok=%s  C.shape=%s",
                    self.total_cycles, bool(np.array_equal(C, ref)), C.shape)
        return C
