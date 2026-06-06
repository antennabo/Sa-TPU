import logging
import numpy as np
from collections import deque
from .analyzer import Analyzer

logger = logging.getLogger(__name__)
from frontend.ir import OpIR, MatMulIR, Conv2dIR
from backend.hw import HardwareConfig
from .result import PerfResult
from .sim_model.spatial_array import spatial_array
from .sim_model.commonfifo import CommonFIFO
from .sim_model.accumulator import Accumulator
from .sim_model.controller import Controller
from .instr import MatMulInstr

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
        self._latency   = getattr(hw, "pe_latency", 1)  # PE 流水级数（drain/restore 延迟 = latency+1）
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

    def simulate(self, mode: str = "OS"):
        """从 load_tiles 的 tile 跑周期级 OS 矩阵乘。沿用已验证的调度（块间流水、块内 K-chunk
        无缝累加、new_tile 只在块间边界拍）。结果留在 self.accum（get_tile 可逐块查）。"""
        self._mode = mode
        A, B = self._reconstruct()
        tm, tk, M, K = self.A_tiles.shape
        _, tn, _, N = self.B_tiles.shape
        assert M == self.M and N == self.N, f"tile 尺寸 {M}x{N} 与阵列 {self.M}x{self.N} 不符"
        self._K = K
        rows, cols, cpb = tm, tn, tk
        Gk, nblocks = tk * K, rows * cols
        assert nblocks <= Accumulator.NUM_TILES, f"块数 {nblocks} 超过 accumulator 容量 {Accumulator.NUM_TILES}"

        lat = self._latency
        self.ctrl  = Controller(M, N, K, drain_delay=lat + 1)
        self.wb    = CommonFIFO(N, K)
        self.ab    = CommonFIFO(M, K)
        self.sa    = spatial_array(M, N, dtype_in=self._dtype_in, dtype_acc=self._dtype_acc, latency=lat)
        self.accum = Accumulator(num_rows=M, num_cols=N)
        for m in (self.ctrl, self.wb, self.ab, self.sa, self.accum):
            m.reset()
        ZEROS = [[0] * N for _ in range(M)]

        # 预载所有块所有 K-chunk（tile_id 升序：mi 外、nj 内、kc 内）
        for mi in range(rows):
            for nj in range(cols):
                for kc in range(cpb):
                    for kk in range(K):
                        kd = kc * K + kk
                        self.wb.update([int(B[kd, nj * N + c]) for c in range(N)], [0] * N); self.wb.commit()
                        self.ab.update([int(A[mi * M + r, kd]) for r in range(M)], [0] * M); self.ab.commit()

        n_run = nblocks * Gk + M + N + lat + 10
        last_write = 0
        for cy in range(n_run):
            new_tile = cy > 0 and cy % Gk == 0 and cy < nblocks * Gk
            self.ctrl.update(cy, self.wb.avail, self.ab.avail, new_tile=new_tile)
            self.wb.update(None, self.ctrl.read_weight)
            self.ab.update(None, self.ctrl.read_activation)
            self.accum.update(self.sa.data, self.ctrl.acc, self.ctrl.acc_read)
            self.sa.update(self.ab.data, self.wb.data, 1, 1, 0, 0, ZEROS, self.ctrl.acc_read)
            self.ctrl.commit(); self.wb.commit(); self.ab.commit(); self.accum.commit(); self.sa.commit()
            if any(self.ctrl.acc[r][c] is not None for r in range(M) for c in range(N)):
                last_write = cy
        self.total_cycles = last_write + 1

        # 拼回结果 + 自检（基准 = 拼回的 int32 A@B；out.npy 是反量化后的最终输出，不在此比）
        C = np.zeros((rows * M, cols * N), dtype=np.int32)
        for mi in range(rows):
            for nj in range(cols):
                C[mi * M:(mi + 1) * M, nj * N:(nj + 1) * N] = np.array(self.accum.get_tile(mi * cols + nj))
        self.result = C
        ref = A.astype(np.int32) @ B.astype(np.int32)
        logger.info("[simulate] total_cycles=%d  numeric_ok=%s  C.shape=%s",
                    self.total_cycles, bool(np.array_equal(C, ref)), C.shape)
        return C
