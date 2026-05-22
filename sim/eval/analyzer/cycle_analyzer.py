import numpy as np
from collections import deque
from .analyzer import Analyzer
from frontend.ir import OpIR, MatMulIR, Conv2dIR
from backend.hw import HardwareConfig
from .result import PerfResult
from .sim_model.spatial_array import spatial_array
from .sim_model.common_buf import CommonBuf
from .sim_model.accumulator import Accumulator
from .sim_model.controller import Controller

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
        self.accum = None
        self.ctrl  = None
        self._K    = None   # inner dimension, set before simulate()

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

    def analyze_from_tiles(self, tiles_dir: str, hw: HardwareConfig, layer: str = "layer3") -> PerfResult:
        self.hw           = hw
        self.total_cycles = 0
        self.load_tiles(tiles_dir, layer)

        dtype_map = {"int8": np.int8, "int16": np.int16, "int32": np.int32, "float32": np.float32}
        M, N = hw.mxu_dim
        self.sa    = spatial_array(M, N, dtype_in=dtype_map[hw.dtype], dtype_acc=dtype_map[hw.accum_dtype])
        self.wb    = CommonBuf(N)
        self.ab    = CommonBuf(M)
        self.accum = Accumulator(num_lanes=M)
        self._K    = self.A_tiles.shape[3]

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

    def load_activation(self, add_head: bool = False, add_tail: bool = False) -> bool:
        """Pop next tile from activation_tile_queue into ab inactive bank (if free).
        Returns True if a tile was loaded."""
        M = self.hw.mxu_dim[0]
        if not self.ab.pending and self.activation_tile_queue:
            tile = self.activation_tile_queue.popleft()
            self.ab.load([tile[i, :] for i in range(M)], add_head=add_head, add_tail=add_tail)
            return True
        return False

    def load_weight(self, add_head: bool = False, add_tail: bool = False) -> bool:
        """Pop next tile from weight_tile_queue into wb inactive bank (if free).
        Returns True if a tile was loaded."""
        _, N = self.hw.mxu_dim
        if not self.wb.pending and self.weight_tile_queue:
            tile = self.weight_tile_queue.popleft()
            self.wb.load([tile[:, j] for j in range(N)], add_head=add_head, add_tail=add_tail)
            return True
        return False

    def simulate(self, mode: str = "OS"):
        self._mode = mode
        self.sa.reset()
        self.wb.reset()
        self.ab.reset()
        self.accum.reset()
        self.ctrl = Controller(self.sa, self.wb, self.ab, self.accum, self._K, mode)
        shift_row, shift_col = Controller._MODE_FLAGS[mode]

        first_wb = True
        first_ab = True
        while self.total_cycles < 50:
            # DMA load into inactive bank
            if shift_col:
                if self.load_weight(add_head=first_wb):
                    first_wb = False
            if shift_row:
                if self.load_activation(add_head=first_ab):
                    first_ab = False

            # control (before compute phase)
            self.ctrl.control(self.total_cycles)

            # compute phase
            if shift_col:
                self.wb.compute()
            if shift_row:
                self.ab.compute()
            self.sa.compute()
            self.accum.compute()

            # commit phase
            if shift_col:
                self.wb.commit()
            if shift_row:
                self.ab.commit()
            self.sa.commit()
            self.accum.commit()
            self.total_cycles += 1
            print(f"next cycle ->")

        print(f"[simulate] total_cycles={self.total_cycles}")
        for t in range(self.ctrl._tile_id):
            print(f"[simulate] accum tile {t}:")
            for i, row in enumerate(self.accum.get_tile(t)):
                print(f"  row {i}: {row}")
