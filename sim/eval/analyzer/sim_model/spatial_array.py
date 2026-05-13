import numpy as np
from .module import module
from .pe import pe

class spatial_array(module):
    def __init__(self, M, N, dtype_in=np.int8, dtype_acc=np.int32):
        super().__init__(dtype_state=dtype_acc)
        self.M = M
        self.N = N
        self.pes = [[pe(dtype_in=dtype_in, dtype_acc=dtype_acc) for _ in range(N)] for _ in range(M)]

    def load_row(self, i, data):
        """Load activations into row i: data[j] → pe[i][j].a"""
        for j in range(self.N):
            self.pes[i][j].load_a(data[j])

    def load_col(self, j, data):
        """Load weight into column j: data[i] → pe[i][j].b"""
        for i in range(self.M):
            self.pes[i][j].load_b(data[i])

    def compute(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].compute()

    def commit(self):
        for i in range(self.M):
            for j in range(self.N):
                self.pes[i][j].commit()

    
