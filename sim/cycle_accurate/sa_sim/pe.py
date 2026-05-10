import numpy as np
from abc import ABC, abstractmethod

class PE(ABC):
    """Abstract base class — defines the interface only."""

    @abstractmethod
    def mac(self, *args):
        ...

    @abstractmethod
    def reset(self):
        ...

class WSPE(PE):
    """Weight Stationary PE.
    Weight is preloaded and stationary.
    Partial sum flows left -> right, activation flows top -> bottom.
    """

    def __init__(self):        
        self.weight = np.int8(0)

    def load_weight(self, w):
        self.weight = np.int8(w)

    def mac(self, act_in: np.int8, partial_sum: np.int32):
        """Args:
            act_in:      activation from the top border or the PE above.
            partial_sum: accumulated sum from the left border or the PE to the left.
        Returns:
            (next_act_for_PE_below, next_psum_for_PE_right)
        """
        next_psum = partial_sum + np.int32(self.weight) * np.int32(act_in)        
        return act_in, next_psum

    def reset(self):
        self.weight = np.int8(0)


class OSPE(PE):
    """Output Stationary PE.
    Each PE accumulates one output element.
    Weight flows left -> right, activation flows top -> bottom.
    """

    def __init__(self):        
        self.partial_sum = np.int32(0)
        self._partial_sum_next = np.int32(0)        
    
    def _non_block_assignment(self):
        self.partial_sum = self._partial_sum_next        

    def _compute_next_and_commit(self, act_in: np.int8, weight_in: np.int8):
        self._partial_sum_next = self.partial_sum + np.int32(weight_in) * np.int32(act_in)        

    def mac(self, act_in: np.int8, weight_in: np.int8):
        """Args:
            act_in:    activation from the top border or the PE above.
            weight_in: weight from the left border or the PE to the left.
        Returns:
            (next_act_for_PE_below, next_weight_for_PE_right)
        """

        self._non_block_assignment()
        self._compute_next_and_commit(act_in=act_in, weight_in=weight_in)       

        return act_in, weight_in

    def reset(self):        
        self.partial_sum = np.int32(0)
        self._partial_sum_next = np.int32(0)

if __name__ == "__main__":
    # ── WSPE tests ──────────────────────────────────────────────────────

    # Test 1: single PE, one cycle
    # weight=2, act=3, initial psum=0  →  next_psum = 2*3 = 6
    pe = WSPE()
    pe.load_weight(np.int8(2))
    act_out, psum_out = pe.mac(np.int8(3), np.int32(0))
    assert act_out  == np.int8(3),   f"act pass-through failed: {act_out}"
    assert psum_out == np.int32(6),  f"psum wrong: {psum_out}"
    print("WSPE test 1 passed: single PE")

    # Test 2: two PEs in a row (horizontal partial sum accumulation)
    # PE[0][0]: weight=2, act=3  →  psum = 0 + 2*3 = 6
    # PE[0][1]: weight=5, act=4  →  psum = 6 + 5*4 = 26
    pe0, pe1 = WSPE(), WSPE()
    pe0.load_weight(np.int8(2))
    pe1.load_weight(np.int8(5))
    _, psum0 = pe0.mac(np.int8(3), np.int32(0))   # 6
    _, psum1 = pe1.mac(np.int8(4), psum0)          # 6 + 20 = 26
    assert psum1 == np.int32(26), f"horizontal accumulation failed: {psum1}"
    print("WSPE test 2 passed: horizontal psum accumulation")

    # ── OSPE tests ──────────────────────────────────────────────────────

    # Test 3: single PE accumulates over 2 cycles
    # C[0][0] = W[0][0]*X[0][0] + W[0][1]*X[1][0]
    #         = 2*3 + 5*4 = 6 + 20 = 26
    pe = OSPE()
    pe.mac(np.int8(3), np.int8(2))   # cycle 0: _next = 0 + 2*3 = 6
    pe.mac(np.int8(4), np.int8(5))   # cycle 1: psum=6, _next = 6 + 5*4 = 26
    pe._non_block_assignment()        # final commit (read-out cycle)
    assert pe.partial_sum == np.int32(26), f"OS accumulation failed: {pe.partial_sum}"
    print("OSPE test 3 passed: 2-cycle accumulation")

    # Test 4: verify act and weight pass-through
    pe = OSPE()
    act_out, wgt_out = pe.mac(np.int8(7), np.int8(3))
    assert act_out == np.int8(7), f"act pass-through failed: {act_out}"
    assert wgt_out == np.int8(3), f"weight pass-through failed: {wgt_out}"
    print("OSPE test 4 passed: act/weight pass-through")
