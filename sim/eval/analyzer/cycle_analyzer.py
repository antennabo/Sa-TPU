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
        self.hw    = hw
        self.sa    = spatial_array(M, N, dtype_in=dtype_map[hw.dtype], dtype_acc=dtype_map[hw.accum_dtype])
        self.wb    = CommonFIFO(N)
        self.ab    = CommonFIFO(M)
        self.accum = Accumulator(num_rows=M, num_cols=N)

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
        self.total_cycles = 0
        self._setup(hw)
        self.load_tiles(tiles_dir, layer)
        self._K = self.A_tiles.shape[3]

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

    def simulate(self, mode: str = "OS"):
        self._mode = mode
        shift_row, shift_col = Controller._MODE_FLAGS[mode]
        M, N = self.hw.mxu_dim

        self.sa.reset()
        self.wb.reset()
        self.ab.reset()
        self.accum.reset()
        self.total_cycles = 0

        if self._K is None and self.weight_tile_queue:
            self._K = self.weight_tile_queue[0].shape[0]

        self.ctrl = Controller(M, N, 0, mode)

        num_issued = 0

        while self.instr_queue or self.total_cycles < 50:
            # Fetch and issue
            if self.instr_queue:
                instr = self.instr_queue.popleft()
                if isinstance(instr, MatMulInstr):
                    self.ctrl.issue(instr)
                    num_issued += 1

            # Compute controller inputs
            has_row = any(v is not None for v in self.wb.state) if shift_row else False
            has_col = any(v is not None for v in self.ab.state) if shift_col else False

            # Control: compute next state + output signals
            self.ctrl.control(
                has_row=has_row,
                has_col=has_col,
                wb_pending=self.wb.pending,
                ab_pending=self.ab.pending,
                weight_available=bool(self.weight_tile_queue),
                activ_available=bool(self.activation_tile_queue),
            )

            # Execute control signals
            if self.ctrl.load_weight:
                tile = self.weight_tile_queue.popleft()
                self.wb.load([tile[:, j] for j in range(N)],
                             add_head=self.ctrl.weight_add_head)

            if self.ctrl.load_activation:
                tile = self.activation_tile_queue.popleft()
                self.ab.load([tile[i, :] for i in range(M)],
                             add_head=self.ctrl.activ_add_head)

            pe_ready  = self.ctrl.pe_result_ready
            data      = [self.sa.pes[pe_ready[j]][j].state if pe_ready[j] is not None else None
                         for j in range(N)]
            self.accum.load_write(self.ctrl.drain_tile_id, pe_ready, data)
            logger.debug("[drain  ] cy=%d row_list=%s data=%s", self.total_cycles, pe_ready, data)

            if self.ctrl.drive_sa:
                if shift_row:
                    self.sa.shift_row()
                    self.sa.load_row(0, [v if v is not None else 0 for v in self.wb.state],
                                     update_countdown=has_row)
                if shift_col:
                    self.sa.shift_col()
                    self.sa.load_col(0, [v if v is not None else 0 for v in self.ab.state],
                                     update_countdown=has_col)
                if shift_row and shift_col:
                    self.sa.acc_local()

            if self.ctrl.buf_advance:
                if shift_col:
                    self.wb.request_read()
                if shift_row:
                    self.ab.request_read()
            self.sa.compute()
            self.accum.compute()

            # Commit
            self.ctrl.commit()
            if self.ctrl.buf_advance:
                if shift_col:
                    self.wb.output()
                if shift_row:
                    self.ab.output()
            self.sa.commit()
            self.accum.commit()

            self.total_cycles += 1
            logger.debug("cycle %d", self.total_cycles)

        logger.info("[simulate] total_cycles=%d", self.total_cycles)
        for t in range(self.ctrl._tile_id):
            logger.info("[simulate] accum tile %5d:", t)
            for i, row in enumerate(self.accum.get_tile(t)):
                logger.info("  row %" \
                "d: %5s", i, row)
