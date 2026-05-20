import numpy as np
from collections import deque
from .analyzer import Analyzer
from frontend.ir import OpIR, MatMulIR, Conv2dIR
from backend.hw import HardwareConfig
from .result import PerfResult
from .sim_model.spatial_array import spatial_array
from .sim_model.tile_buf import TileBuf

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
        self.weight_tile_queue:     deque = deque()  # 临时 weight tile 队列（最多缓存2块）
        self.activation_tile_queue: deque = deque()  # 临时 activation tile 队列（最多缓存2块）

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
        self.sa        = spatial_array(M, N, dtype_in=dtype_map[hw.dtype], dtype_acc=dtype_map[hw.accum_dtype])
        self.wb = TileBuf(N)
        self.ab = TileBuf(M)

        # 将前两块 weight / activation tile 入队
        self.weight_tile_queue.clear()
        tn = self.B_tiles.shape[1]
        for j in range(min(2, tn)):
            self.weight_tile_queue.append(self.B_tiles[0, j])

        self.activation_tile_queue.clear()
        tm = self.A_tiles.shape[0]
        for i in range(min(2, tm)):
            self.activation_tile_queue.append(self.A_tiles[i, 0])

        self.simulate(mode="OS")

    _MODE_FLAGS = {
        "OS": (True,  True),
        "WS": (True,  False),
        "IS": (False, True),
    }

    def load_activation(self):
        """Pop next tile from activation_tile_queue into ab inactive bank (if free)."""
        M = self.hw.mxu_dim[0]
        if not self.ab.pending and self.activation_tile_queue:
            tile = self.activation_tile_queue.popleft()
            self.ab.load([tile[i, :] for i in range(M)], tail=M)

    def load_weight(self):
        """Pop next tile from weight_tile_queue into wb inactive bank (if free)."""
        M, N = self.hw.mxu_dim
        if not self.wb.pending and self.weight_tile_queue:
            tile = self.weight_tile_queue.popleft()
            self.wb.load([tile[:, j] for j in range(N)], tail=M)

    def control(self):
        M, N = self.hw.mxu_dim
        shift_row, shift_col = self._MODE_FLAGS[self._mode]

        if shift_row:
            self.sa.shift_row()
            col_data = [self.ab.output_fifo[j].pop() for j in range(N)]
            if any(v is not None for v in col_data):
                self.sa.load_row(0, [v if v is not None else 0 for v in col_data])

        if shift_col:
            self.sa.shift_col()
            row_data = [self.wb.output_fifo[i].pop() for i in range(M)]
            if any(v is not None for v in row_data):
                self.sa.load_col(0, [v if v is not None else 0 for v in row_data])

        if shift_row and shift_col:
            self.sa.acc_local()

    def simulate(self, mode: str = "OS"):
        self._mode = mode
        self.sa.reset()
        self.wb.reset()
        self.ab.reset()
        shift_row, shift_col = self._MODE_FLAGS[mode]

        # 初始装载：第一块入 inactive → swap 为 active；第二块入 inactive（pending）
        if shift_col:
            self.load_weight()   # tile[0] → inactive bank
            self.wb.swap()       # inactive → active
            self.load_weight()   # tile[1] → inactive bank（若队列还有）
        if shift_row:
            self.load_activation()   # tile[0] → inactive bank
            self.ab.swap()           # inactive → active
            self.load_activation()   # tile[1] → inactive bank（若队列还有）

        while self.total_cycles < 50:
            self.control()

            # wb active bank drain 后切换到下一块
            if shift_col and all(f.empty() for f in self.wb.output_fifo):
                if self.wb.pending:
                    self.wb.swap()
                    print(f"[buf] wb swap at cycle {self.total_cycles}")
                self.load_weight()  # 尝试将队列下一块装入 inactive

            # ab active bank drain 后切换到下一块
            if shift_row and all(f.empty() for f in self.ab.output_fifo):
                if self.ab.pending:
                    self.ab.swap()
                    print(f"[buf] ab swap at cycle {self.total_cycles}")
                self.load_activation()  # 尝试将队列下一块装入 inactive

            self.sa.compute()
            self.sa.commit()
            self.total_cycles += 1
            print(f"next cycle ->")

        print(f"[simulate] total_cycles={self.total_cycles}")
        print(f"[simulate] result:\n{self.sa.get_result()}")
