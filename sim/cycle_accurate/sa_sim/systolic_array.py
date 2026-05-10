import numpy as np
from abc import ABC, abstractmethod
from pe import WSPE, OSPE


class SystolicArray(ABC):
    """Abstract base class for systolic array implementations."""

    def __init__(self, N: int):
        self.N = N

    @abstractmethod
    def compute(self, *args, **kwargs):
        ...

    @abstractmethod
    def reset(self):
        ...

class OSSystolicArray(SystolicArray):
    """Output Stationary Systolic Array.

    Computes C = W @ X where:
    - W [N, K]: rows flow left -> right (skewed: row i enters at cycle i+k).
    - X [K, N]: columns flow top -> bottom (skewed: col j enters at cycle j+k).
    - PE[i][j] accumulates C[i][j] = sum_k W[i][k] * X[k][j].

    At cycle t: PE[i][j] processes k = t - i - j (if 0 <= k < K).
    Total compute cycles = K + 2*N - 2, plus 1 read-out cycle (OSPE 1-cycle delay).

    SA pipeline state:
      act_state[i][j]   : act value at PE[i][j]'s OUTPUT after previous cycle.
      weight_state[i][j]: weight value at PE[i][j]'s OUTPUT after previous cycle.
    """

    def __init__(self, N: int):
        super().__init__(N)
        self.pes = [[OSPE() for _ in range(N)] for _ in range(N)]

    def reset(self):
        for row in self.pes:
            for pe in row:
                pe.reset()

    def compute(self, W: np.ndarray, X: np.ndarray, record_trace: bool = False):
        """
        W: int8 [N, K]
        X: int8 [K, N]
        Returns C = W @ X: int32 [N, N]

        psum_trace: list of partial_sum snapshots [N, N] per cycle.
          Records committed partial_sum (1-cycle delayed from _partial_sum_next).
          Length = K + 2*N - 1 (compute cycles + read-out cycle).
        """
        N = self.N
        _, K = W.shape
        total_cycles = K + 2 * N - 2

        self.reset()
        act_state    = np.zeros((N, N), dtype=np.int8)
        weight_state = np.zeros((N, N), dtype=np.int8)
        trace = [] if record_trace else None

        for t in range(total_cycles):

            # Phase 1: gather inputs for all PEs using current pipeline state
            act_in_buf    = np.zeros((N, N), dtype=np.int8)
            weight_in_buf = np.zeros((N, N), dtype=np.int8)

            for i in range(N):
                for j in range(N):
                    k = t - i - j

                    if j == 0:
                        # Left border: SA injects W[i][k] (skewed by row i)
                        weight_in_buf[i][j] = np.int8(W[i, k]) if 0 <= k < K else np.int8(0)
                    else:
                        # Interior: propagate from left neighbour's pipeline output
                        weight_in_buf[i][j] = weight_state[i][j-1]

                    if i == 0:
                        # Top border: SA injects X[k][j] (skewed by col j)
                        act_in_buf[i][j] = np.int8(X[k, j]) if 0 <= k < K else np.int8(0)
                    else:
                        # Interior: propagate from top neighbour's pipeline output
                        act_in_buf[i][j] = act_state[i-1][j]

            # Phase 2: call mac for all PEs, collect pipeline outputs
            new_act_state    = np.zeros((N, N), dtype=np.int8)
            new_weight_state = np.zeros((N, N), dtype=np.int8)

            for i in range(N):
                for j in range(N):
                    a_out, w_out = self.pes[i][j].mac(
                        act_in_buf[i][j], weight_in_buf[i][j]
                    )
                    new_act_state[i][j]    = a_out
                    new_weight_state[i][j] = w_out

            # Phase 3: update pipeline state for next cycle
            act_state    = new_act_state
            weight_state = new_weight_state

            if record_trace:
                trace.append(np.array(
                    [[self.pes[i][j].partial_sum for j in range(N)] for i in range(N)],
                    dtype=np.int32
                ))

        # Read-out cycle: commit final _partial_sum_next -> partial_sum (OSPE 1-cycle delay)
        for i in range(N):
            for j in range(N):
                self.pes[i][j]._non_block_assignment()

        if record_trace:
            trace.append(np.array(
                [[self.pes[i][j].partial_sum for j in range(N)] for i in range(N)],
                dtype=np.int32
            ))

        C = np.array(
            [[self.pes[i][j].partial_sum for j in range(N)] for i in range(N)],
            dtype=np.int32
        )
        return (C, trace) if record_trace else C
    
class WSSystolicArray(SystolicArray):
    """Weight Stationary Systolic Array.

    Computes C = W_tile @ X where:
    - W_tile [N, N]: PE[i][j] holds W[i][j], stationary.
    - X [N, M]: col j enters top border at t = j + p (skewed by col index).
    - Activation flows top -> bottom (registered, 1 cycle/row).
    - Psum flows left -> right (registered, 1 cycle/col), 0 injected at left border.
    - C[i][p] exits from PE[i][N-1] at t = N - 1 + i + p (0-indexed).

    Total cycles = 2*N + M - 2.
    """

    def __init__(self, N: int):
        super().__init__(N)
        self.pes = [[WSPE() for _ in range(N)] for _ in range(N)]

    def load_weights(self, W_tile: np.ndarray):
        for i in range(self.N):
            for j in range(self.N):
                self.pes[i][j].load_weight(W_tile[i][j])

    def reset(self):
        pass  # no pipeline state persists between compute() calls; weights stay loaded

    def compute(self, X: np.ndarray, record_trace: bool = False):
        """
        X: int8 [N, M]
        Returns C = W_tile @ X: int32 [N, M]

        Skewing: X[j, p] enters top of col j at cycle t = j + p.
        At PE[i][j], cycle t processes output column p = t - i - j (same k formula as OS).
        C[i, p] readable from output register at t = N + i + p (1-cycle register delay).
        trace length = 2*N + M - 1.
        """
        N = self.N
        _, M = X.shape

        act_state  = np.zeros((N, N), dtype=np.int8)
        psum_state = np.zeros((N, N), dtype=np.int32)
        C     = np.zeros((N, M), dtype=np.int32)
        trace = [] if record_trace else None
        total_cycles = 2 * N + M - 1

        for t in range(total_cycles):
            # Output register: psum_state[i][N-1] holds PE[i][N-1]'s registered output from the previous cycle
            for i in range(N):
                out_col = t - N - i
                if 0 <= out_col < M:
                    C[i, out_col] = psum_state[i][N - 1]

            if record_trace:
                trace.append(C.copy())

            # Phase 1: gather inputs
            act_in_buf  = np.zeros((N, N), dtype=np.int8)       #act_in for all PEs
            psum_in_buf = np.zeros((N, N), dtype=np.int32)      #partial_sum_in for all PEs
            for i in range(N):
                for j in range(N):
                    if i == 0:
                        act_col = t - j
                        act_in_buf[0][j] = np.int8(X[j, act_col]) if 0 <= act_col < M else np.int8(0)
                    else:
                        act_in_buf[i][j] = act_state[i-1][j]
                    psum_in_buf[i][j] = np.int32(0) if j == 0 else psum_state[i][j-1]

            # Phase 2: mac all PEs
            new_act_state  = np.zeros((N, N), dtype=np.int8)
            new_psum_state = np.zeros((N, N), dtype=np.int32)
            for i in range(N):
                for j in range(N):
                    act_out, psum_out = self.pes[i][j].mac(act_in_buf[i][j], psum_in_buf[i][j])
                    new_act_state[i][j]  = act_out
                    new_psum_state[i][j] = psum_out

            # Phase 3: update pipeline state
            act_state  = new_act_state
            psum_state = new_psum_state

        return (C, trace) if record_trace else C
    
if __name__ == "__main__":
    # ── OSSystolicArray ───────────────────────────────────────────────────

    # Test 1: basic 2×2 (K = N = 2)
    os_sa = OSSystolicArray(N=2)
    W = np.array([[1, 2], [3, 4]], dtype=np.int8)
    X = np.array([[5, 6], [7, 8]], dtype=np.int8)
    C = os_sa.compute(W, X)
    expected = np.array([[19, 22], [43, 50]], dtype=np.int32)
    assert np.array_equal(C, expected), f"OS test 1 failed:\n{C}"
    print("OS test 1 passed: 2×2 matmul")

    # Test 2: non-square weight (K=3, N=2)
    W3 = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int8)   # [N=2, K=3]
    X3 = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.int8) # [K=3, N=2]
    C3 = os_sa.compute(W3, X3)
    expected3 = W3.astype(np.int32) @ X3.astype(np.int32)
    assert np.array_equal(C3, expected3), f"OS test 2 failed:\n{C3}"
    print("OS test 2 passed: non-square K=3")

    # Test 3: trace length = K + 2*N - 1, last snapshot = final C
    _, K = W.shape
    C, trace = os_sa.compute(W, X, record_trace=True)
    assert len(trace) == K + 2*2 - 1, f"OS trace length: {len(trace)}"
    assert np.array_equal(trace[-1], expected), f"OS trace[-1] wrong"
    print(f"OS test 3 passed: trace length={len(trace)}")

    # ── WSSystolicArray ───────────────────────────────────────────────────

    ws_sa = WSSystolicArray(N=2)
    W_tile = np.array([[1, 2], [3, 4]], dtype=np.int8)
    ws_sa.load_weights(W_tile)

    # Test 4: M=2 output columns
    X_ws = np.array([[5, 6], [7, 8]], dtype=np.int8)  # [N=2, M=2]
    C = ws_sa.compute(X_ws)
    expected_ws = np.array([[19, 22], [43, 50]], dtype=np.int32)
    assert np.array_equal(C, expected_ws), f"WS test 4 failed:\n{C}"
    print("WS test 4 passed: 2×2 matmul (M=2)")

    # Test 5: M=1 single output column
    X_m1 = np.array([[5], [7]], dtype=np.int8)  # [N=2, M=1]
    C_m1 = ws_sa.compute(X_m1)
    assert np.array_equal(C_m1, np.array([[19], [43]], dtype=np.int32)), \
        f"WS test 5 failed:\n{C_m1}"
    print("WS test 5 passed: M=1 single column")

    # Test 6: weights persist across compute() calls
    assert np.array_equal(ws_sa.compute(X_ws), expected_ws), "WS test 6 failed: weights lost"
    print("WS test 6 passed: weights persist")

    # Test 7: trace length = 2*N + M - 1, last snapshot = final C
    _, M = X_ws.shape
    C, trace = ws_sa.compute(X_ws, record_trace=True)
    assert len(trace) == 2*2 + M - 1, f"WS trace length: {len(trace)}"
    assert np.array_equal(trace[-1], expected_ws), f"WS trace[-1] wrong"
    print(f"WS test 7 passed: trace length={len(trace)}")

