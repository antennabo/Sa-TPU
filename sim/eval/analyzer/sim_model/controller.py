class Controller:
    """
    Cycle-accurate controller: drives SA inputs and manages drain → accumulator.

    Call control(cycle) each cycle before the compute phase.
    """

    _MODE_FLAGS = {
        "OS": (True,  True),
        "WS": (True,  False),
        "IS": (False, True),
    }

    def __init__(self, sa, wb, ab, accum, K: int, mode: str = "OS"):
        self.sa    = sa
        self.wb    = wb
        self.ab    = ab
        self.accum = accum
        self._K    = K
        self._mode = mode
        self._comp_start = None
        self._tile_id    = 0

    def control(self, cycle: int):
        shift_row, shift_col = self._MODE_FLAGS[self._mode]

        has_row = any(v is not None for v in self.wb.state) if shift_row else False
        has_col = any(v is not None for v in self.ab.state) if shift_col else False

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

        # track when first valid data enters SA
        if has_row and has_col and self._comp_start is None:
            self._comp_start = cycle

        # drain: row i readable at elapsed = K + N - 1 + i
        # (pe[i][N-1] commits at elapsed K+N-2+i; control reads it one cycle later)
        if self._comp_start is not None:
            elapsed = cycle - self._comp_start
            mask = [False] * self.sa.M
            data = [None]  * self.sa.M
            for i in range(self.sa.M):
                if elapsed == self._K + self.sa.N - 1 + i:
                    mask[i] = True
                    data[i] = [self.sa.pes[i][j].state for j in range(self.sa.N)]
            if any(mask):
                print(f"  [drain] cycle={cycle} tile={self._tile_id} mask={mask}")
                self.accum.load_write(self._tile_id, data, mask)
                if mask[self.sa.M - 1]:
                    self._tile_id    += 1
                    self._comp_start  = None

    def reset(self):
        self._comp_start = None
        self._tile_id    = 0
