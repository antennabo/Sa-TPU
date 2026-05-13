import numpy as np
from .analyzer import Analyzer
from frontend.ir import OpIR, MatMulIR, Conv2dIR
from backend.hw import HardwareConfig
from .result import PerfResult
from .sim_model.spatial_array import spatial_array


class CycleAccurateAnalyzer(Analyzer):
    name = "cycle_accurate"

    def __init__(self):
        self.total_cycles = 0
        self.all_modules  = []   # [spatial_array, ...]; set per tile in simulate()
        self.instr_queue  = []   # tile-level MatMul instructions
        self.hw           = None
        self._ops         = 0    # total FLOPs, set in layer2ops()

    def analyze(self, op: OpIR, hw: HardwareConfig, A: np.ndarray, B: np.ndarray) -> PerfResult:
        self.hw           = hw
        self.instr_queue  = []
        self.total_cycles = 0

        self.layer2ops(op, A, B)
        self.simulate(self.instr_queue)

        # return PerfResult(
        #     model_name="cycle_accurate",
        #     confidence="accurate",
        #     latency_ns=latency_ns,
        #     utilization=peak_cycles / self.total_cycles,
        #     throughput_ops=self._ops / latency_ns,
        #     pipeline_bubbles=0,
        # )

    def layer2ops(self, op: OpIR, A: np.ndarray, B: np.ndarray):
        """
        Convert IR op to tile-level MatMul instructions, append to self.instr_queue.
        Each instruction: {"A_tile": (tm, K), "B_tile": (K, tn)}
        A, B: 已由调用方从 exported 和 x_np 提取好的 int8 矩阵
        """
        mxu_m, mxu_n = self.hw.mxu_dim

        if isinstance(op, MatMulIR):
            self._ops = 2 * op.M * op.N * op.K

        elif isinstance(op, Conv2dIR):
            H_out = (op.H + 2*op.padding - op.R) // op.stride + 1
            W_out = (op.W + 2*op.padding - op.S) // op.stride + 1
            self._ops = 2 * op.N * op.K * H_out * W_out * op.C * op.R * op.S

        else:
            raise NotImplementedError(f"不支持 {type(op).__name__}")

        M, K = A.shape
        _, N  = B.shape
        for m0 in range(0, M, mxu_m):
            for n0 in range(0, N, mxu_n):
                m1 = min(m0 + mxu_m, M)
                n1 = min(n0 + mxu_n, N)
                self.instr_queue.append({
                    "A_tile": A[m0:m1, :],   # (tm, K)
                    "B_tile": B[:, n0:n1],   # (K, tn)
                })

    def simulate(self, instr_queue):
        """
        Execute tile-level instructions. Per tile: K cycles.
        Per cycle protocol (§4.0):
            1. dispatch  -- write inputs to modules  (Phase A: inline)
            2. compute() -- read state → next_state
            3. commit()  -- next_state → state
        """
        for instr in instr_queue:
            A_tile = instr["A_tile"]   # (tm, K)
            B_tile = instr["B_tile"]   # (K, tn)
            tm, K  = A_tile.shape
            tn     = B_tile.shape[1]

            arr = spatial_array(tm, tn)
            self.all_modules = [arr]

            for k in range(K):
                # ── dispatch (Phase A placeholder; Controller will own this) ──
                for i in range(tm):
                    arr.load_row(i, [A_tile[i, k]] * tn)
                for j in range(tn):
                    arr.load_col(j, [B_tile[k, j]] * tm)
                for i in range(tm):
                    for j in range(tn):
                        arr.pes[i][j].load_acc(arr.pes[i][j].state)

                # ── compute + commit ───────────────────────────────────────
                for module in self.all_modules:
                    module.compute()
                for module in self.all_modules:
                    module.commit()
                self.total_cycles += 1