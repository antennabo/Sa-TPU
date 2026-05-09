import numpy as np
from sim.cycle_accurate.sa_sim.pe import PE

class SystolicArray:
    # create N x N systolic array
    def __init__(self,N):
        self.N = N
        self.pes = [[PE() for _ in range(N)] for _ in range (N)]

    def load_weights(self, weight):
        for row in range(self.N):
            for col in range(self.N):
                self.pes[row][col].load_weight(weight[row][col])

    # to compute W * X, result size: N * K
    # Dataflow see the following link:
    # https://www.youtube.com/watch?v=c67L7SldkU4&list=PLmidTg12eX6TVRe8gNx9hwOAOuI5phSGA
    def compute(self, X):
        _, K = X.shape

        C = np.zeros((self.N, K), dtype=np.int32)
        
        for k in range(K):
            for one_row in self.pes:
                for pe in one_row:
                    pe.reset()

            # fix the column first, do the computation: W * X[,k], where X[,k] is one column in X 
            for j in range(self.N):
                for i in range(self.N):
                    self.pes[i][j].mac(X[j,k])
            
            # sum up horizontally
            for i in range(self.N):
                for j in range(self.N):
                    C[i,k] += self.pes[i][j].partial_sum
        
        return C
    
if __name__ == "__main__":
    sa = SystolicArray(3)
    W = np.array([[1,2,3],[4,5,6],[7,8,9]], dtype=np.int8)
    X = np.array([[1,0],[0,1],[0,0]], dtype=np.int8)
    sa.load_weights(W)
    print(sa.compute(X))

    
