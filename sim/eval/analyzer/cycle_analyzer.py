import numpy as np
from .analyzer import Analyzer
from frontend.ir import OpIR, MatMulIR, Conv2dIR
from backend.hw import HardwareConfig
from .result import PerfResult
from .sim_model.spatial_array import spatial_array
from .sim_model.fifo import FIFO

class CycleAccurateAnalyzer(Analyzer):
    name = "cycle_accurate"

    def __init__(self):
        self.total_cycles = 0
        self.all_modules  = []   # [spatial_array, ...]; set per tile in simulate()
        self.instr_queue  = []   # tile-level MatMul instructions
        self.hw           = None
        self._ops         = 0    # total FLOPs, set in layer2ops()
        self.A_tiles = []
        self.B_tiles = []

    def analyze(self, op: OpIR, hw: HardwareConfig, A: np.ndarray, B: np.ndarray) -> PerfResult:
        self.hw           = hw
        self.instr_queue  = []
        self.total_cycles = 0

        # self.load_tiles(op, A, B)
        # self.simulate(self.instr_queue)

        # return PerfResult(
        #     model_name="cycle_accurate",
        #     confidence="accurate",
        #     latency_ns=latency_ns,
        #     utilization=peak_cycles / self.total_cycles,
        #     throughput_ops=self._ops / latency_ns,
        #     pipeline_bubbles=0,
        # )

    def load_tiles(self, tiles_dir: str, layer: str = "layer3"):
        """
        A_tiles 原始: (tm, tk, sizem, sizek) → reshape → (tm*tk, sizem, sizek)
        B_tiles 原始: (tk, tn, sizek, sizen) → reshape → (tk*tn, sizek, sizen)
        """
        self.A_tiles = np.load(f"{tiles_dir}/{layer}_A_tiles.npy")  # (tm, tk, sizem, sizek)
        self.B_tiles = np.load(f"{tiles_dir}/{layer}_B_tiles.npy")  # (tk, tn, sizek, sizen)
        self.bias = np.load(f"{tiles_dir}/{layer}_bias.npy")

        # reshape 到 3D
        # self.A_tiles = A_4d.reshape(tm * tk, sizem, sizek)   # (tm*tk, sizem, sizek)
        # self.B_tiles = B_4d.reshape(tk * tn, sizek, sizen)   # (tk*tn, sizek, sizen)

        # print(f"A_tiles: {A_4d.shape} → {self.A_tiles.shape}")
        # print(f"B_tiles: {B_4d.shape} → {self.B_tiles.shape}")
        # print(f"A_tiles: {self.A_tiles.shape}")
        # print(f"B_tiles: {self.B_tiles.shape}")
        # print(f"bias:    {self.bias.shape}")

    def analyze_from_tiles(self, tiles_dir: str, hw: HardwareConfig, layer: str = "layer3") -> PerfResult:
        self.hw           = hw
        self.total_cycles = 0
        self.load_tiles(tiles_dir, layer)

        dtype_map = {"int8": np.int8, "int16": np.int16, "int32": np.int32, "float32": np.float32}
        M, N = hw.mxu_dim
        self.sa         = spatial_array(M, N, dtype_in=dtype_map[hw.dtype], dtype_acc=dtype_map[hw.accum_dtype], mode="OS")
        self.row_fifos  = [FIFO() for _ in range(M)]
        self.col_fifos  = [FIFO() for _ in range(N)]
        self.all_modules = [self.sa] + self.row_fifos + self.col_fifos

        self.simulate(self.A_tiles[0, 0], self.B_tiles[0, 0], mode="OS")

    def init_fifos(self, A_tile: np.ndarray, B_tile: np.ndarray, mode: str = "OS"):
        """
        将 tile 数据错排后写入 FIFO，支持脉动阵列三种数据流。
        A_tile: (M, K)  — activation，按行送入 row_fifos
        B_tile: (K, N)  — weight，  按列送入 col_fifos

        WS: row i 前插 i 个 0，col_fifos 不使用
        IS: col j 前插 j 个 0，row_fifos 不使用
        OS: row i 前插 i 个 0，col j 前插 j 个 0
        """
        M, N = self.hw.mxu_dim
        tail = M + N - 2  # 尾部补零量（最大 i/j=0 时）

        if mode in ("WS", "OS"):
            for i, fifo in enumerate(self.row_fifos):
                fifo.load([0] * i + list(A_tile[i, :]) + [0] * (tail - i))

        if mode in ("IS", "OS"):
            for j, fifo in enumerate(self.col_fifos):
                fifo.load([0] * j + list(B_tile[:, j]) + [0] * (tail - j))

    def control(self):
        M, N = self.hw.mxu_dim
        self.sa.control()

        for j in range(N):
            val = self.col_fifos[j].pop()
            if val is not None:
                self.sa.pes[0][j].load_a(val)
                self.sa._row_countdown = 2
                self.sa.done = False

        for i in range(M):
            val = self.row_fifos[i].pop()
            if val is not None:
                self.sa.pes[i][0].load_b(val)
                self.sa._col_countdown = 2
                self.sa.done = False

    def simulate(self, A_tile: np.ndarray, B_tile: np.ndarray, mode: str = "OS"):
        self.sa.reset()
        self.init_fifos(A_tile, B_tile, mode)

        # while any(not f.empty() for f in self.row_fifos + self.col_fifos) or not self.sa.done:
        while not self.sa.done:
            self.control()

            # Compute phase
            self.sa.compute()

            # Commit phase
            self.sa.commit()

            self.total_cycles += 1
            # print(f"[simulate] result:\n{self.sa.get_result()}")
            print(f"next cycle ->")

        print(f"[simulate] total_cycles={self.total_cycles}")
        print(f"[simulate] result:\n{self.sa.get_result()}")
