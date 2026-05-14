import numpy as np
from .module import module
from .pe import pe

class spatial_array(module):
    def __init__(self, M, N, dtype_in=np.int8, dtype_acc=np.int32, mode="OS"):
        super().__init__(dtype_state=dtype_acc)
        self.M = M
        self.N = N
        self.mode = mode
        self.pes = [[pe(dtype_in=dtype_in, dtype_acc=dtype_acc) for _ in range(N)] for _ in range(M)]
        self._row_countdown = 0
        self._col_countdown = 0
        self.done = True

    def load_row(self, i, data):
        """Load activations into row i: data[j] → pe[i][j].a"""
        for j in range(self.N):
            self.pes[i][j].load_a(data[j])
        # print(f"  load_row({i}): a = {[self.pes[i][j].a for j in range(self.N)]}")
        if self.mode in ("WS", "OS"):
            self._row_countdown = self.N
            self.done = False

    def load_col(self, j, data):
        """Load weight into column j: data[i] → pe[i][j].b"""
        for i in range(self.M):
            self.pes[i][j].load_b(data[i])
        # print(f"  load_col({j}): b = {[self.pes[i][j].b for i in range(self.M)]}")
        if self.mode in ("IS", "OS"):
            self._col_countdown = self.M
            self.done = False

    def reset(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].reset()
        self._row_countdown = 0
        self._col_countdown = 0
        self.done = False

    def control(self):
        """Shift data through the array: activations left→right, weights top→bottom."""
        print("  [control]")
        for i in range(self.M):
            a   = [int(self.pes[i][j].a)   for j in range(self.N)]
            b   = [int(self.pes[i][j].b)   for j in range(self.N)]
            acc = [int(self.pes[i][j].acc) for j in range(self.N)]
            print(f"    row{i}  a={a}  b={b}  acc={acc}")
        if self.mode in ("WS", "OS"):
            # a enters row 0, shifts top→bottom
            a_snap = [[self.pes[i][j].a for j in range(self.N)] for i in range(self.M)]
            for i in range(1, self.M):
                for j in range(self.N):
                    self.pes[i][j].load_a(a_snap[i - 1][j])

        if self.mode in ("IS", "OS"):
            # b enters col 0, shifts left→right
            b_snap = [[self.pes[i][j].b for j in range(self.N)] for i in range(self.M)]
            for i in range(self.M):
                for j in range(1, self.N):
                    self.pes[i][j].load_b(b_snap[i][j - 1])

        if self.mode == "OS":
            for i in range(self.M):
                for j in range(self.N):
                    self.pes[i][j].acc = self.pes[i][j].state

    def compute(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].compute()

        if self._row_countdown > 0:
            self._row_countdown -= 1
        if self._col_countdown > 0:
            self._col_countdown -= 1

        if self.mode == "WS":
            self.done = self._row_countdown == 0
        elif self.mode == "IS":
            self.done = self._col_countdown == 0
        else:  # OS
            self.done = self._row_countdown == 0 and self._col_countdown == 0

    def commit(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].commit()

    def get_result(self) -> np.ndarray:
        return np.array([[self.pes[i][j].state for j in range(self.N)] for i in range(self.M)])

    
