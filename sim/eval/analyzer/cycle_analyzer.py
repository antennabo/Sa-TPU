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
        self.simulate(self.instr_queue)

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
        从预切好的 .npy 文件直接构建 instr_queue，绕过 layer2ops。
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
        print(f"A_tiles: {self.A_tiles.shape}")
        print(f"B_tiles: {self.B_tiles.shape}")
        print(f"bias:    {self.bias.shape}")

    def analyze_from_tiles(self, tiles_dir: str, hw: HardwareConfig, layer: str = "layer3") -> PerfResult:
        self.hw           = hw
        self.total_cycles = 0
        self.load_tiles(tiles_dir, layer)
        self.simulate()

    @staticmethod
    def run_fifo(A_tile: np.ndarray, B_tile: np.ndarray) -> tuple[np.ndarray, int]:
        """
        脉动阵列单 tile 仿真（通用）。

        A_tile: (sizem, sizek)  每行一个独立 FIFO（标量序列）
        B_tile: (sizek, sizen)  按 k 索引直接访问

        row i 在第 i 个 cycle 才开始进入阵列（波前错开）。
        总周期 = sizek + sizem - 1

        Returns:
            acc:    (sizem, sizen)
            cycles: int
        """
        sizem, sizek = A_tile.shape

        A_fifos = [FIFO() for _ in range(sizem)]
        for i in range(sizem):
            A_fifos[i].load(A_tile[i, :])
        acc     = np.zeros((sizem, B_tile.shape[1]))
        cycles  = 0

        for t in range(sizek + sizem - 1):
            for i in range(sizem):
                k = t - i
                if 0 <= k < sizek:
                    a_scalar = A_fifos[i].popleft()
                    acc[i, :] += a_scalar * B_tile[k, :]
            cycles += 1

        return acc, cycles

    def simulate(self):
        """Serial tile execution. Total cycles = tm*tn*tk*(sizek+sizem-1)"""
        tm, tk, sizem, sizek = self.A_tiles.shape
        tk2, tn, _, sizen    = self.B_tiles.shape
        assert tk == tk2, f"A tk={tk} vs B tk={tk2}"

        self.total_cycles = 0
        self.output_tiles = np.zeros((tm, tn, sizem, sizen))

        for i_tm in range(tm):
            for i_tn in range(tn):
                acc = np.zeros((sizem, sizen))
                for i_tk in range(tk):
                    A_tile = self.A_tiles[i_tm, i_tk]
                    B_tile = self.B_tiles[i_tk, i_tn]
                    tile_acc, tile_cycles = self.run_fifo(A_tile, B_tile)
                    acc += tile_acc
                    self.total_cycles += tile_cycles
                self.output_tiles[i_tm, i_tn] = acc

        print(f"[simulate] total_cycles={self.total_cycles}  ({tm}x{tn}x{tk}x{sizek})  output_tiles={self.output_tiles.shape}")
        print(f"[simulate] output_tiles:\n{self.output_tiles}")
