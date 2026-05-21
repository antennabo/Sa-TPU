import numpy as np
from .module import module
from .pe import pe

class spatial_array(module):
    def __init__(self, M, N, dtype_in=np.int8, dtype_acc=np.int32):
        super().__init__(dtype_state=dtype_acc)
        self.M = M
        self.N = N
        self.pes = [[pe(dtype_in=dtype_in, dtype_acc=dtype_acc) for _ in range(N)] for _ in range(M)]
        self._row_countdown = 2
        self._col_countdown = 2
        self._did_shift_row = False
        self._did_shift_col = False
        self.done = False

    def load_row(self, i, data, update_countdown=True):
        """Load weight into row i: data[j] → pe[i][j].b"""
        for j in range(self.N):
            self.pes[i][j].load_b(data[j])
        if i == 0 and update_countdown:
            self._row_countdown = self.N
            self.done = False

    def load_col(self, j, data, update_countdown=True):
        """Load activation into column j: data[i] → pe[i][j].a"""
        for i in range(self.M):
            self.pes[i][j].load_a(data[i])
        if j == 0 and update_countdown:
            self._col_countdown = self.M
            self.done = False

    def shift_row(self):
        """Shift b (weight) values top→bottom + propagate psum top→bottom."""
        b_snap     = [[self.pes[i][j].b     for j in range(self.N)] for i in range(self.M)]
        state_snap = [[self.pes[i][j].state for j in range(self.N)] for i in range(self.M)]
        for i in range(1, self.M):
            for j in range(self.N):
                self.pes[i][j].load_b(b_snap[i - 1][j])
        for j in range(self.N):
            self.pes[0][j].acc = self.pes[0][j].dtype_state(0)
        for i in range(1, self.M):
            for j in range(self.N):
                self.pes[i][j].acc = state_snap[i - 1][j]
        self._did_shift_row = True

    def shift_col(self):
        """Shift a (activation) values left→right + propagate psum left→right."""
        a_snap     = [[self.pes[i][j].a     for j in range(self.N)] for i in range(self.M)]
        state_snap = [[self.pes[i][j].state for j in range(self.N)] for i in range(self.M)]
        for i in range(self.M):
            for j in range(1, self.N):
                self.pes[i][j].load_a(a_snap[i][j - 1])
        for i in range(self.M):
            self.pes[i][0].acc = self.pes[i][0].dtype_state(0)
        for i in range(self.M):
            for j in range(1, self.N):
                self.pes[i][j].acc = state_snap[i][j - 1]
        self._did_shift_col = True

    def acc_local(self):
        """Set acc = state for all PEs (OS local accumulation)."""
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].acc = self.pes[i][j].state

    def reset(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].reset()
        self._row_countdown = 2
        self._col_countdown = 2
        self._did_shift_row = False
        self._did_shift_col = False
        self.done = False

    def compute(self):
        print("  [compute]")
        for i in range(self.M):
            a   = [int(self.pes[i][j].a)   for j in range(self.N)]
            b   = [int(self.pes[i][j].b)   for j in range(self.N)]
            acc = [int(self.pes[i][j].acc) for j in range(self.N)]
            print(f"    row{i}  a={a}  b={b}  acc={acc}")

        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].compute()

        if self._did_shift_row and self._row_countdown > 0:
            self._row_countdown -= 1
        if self._did_shift_col and self._col_countdown > 0:
            self._col_countdown -= 1

        if self._did_shift_row and self._did_shift_col:
            self.done = self._row_countdown == 0 and self._col_countdown == 0
        elif self._did_shift_row:
            self.done = self._row_countdown == 0
        elif self._did_shift_col:
            self.done = self._col_countdown == 0

        self._did_shift_row = False
        self._did_shift_col = False

    def commit(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].commit()

    def get_result(self) -> np.ndarray:
        return np.array([[self.pes[i][j].state for j in range(self.N)] for i in range(self.M)])
