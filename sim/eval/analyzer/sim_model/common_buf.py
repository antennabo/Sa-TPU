from .fifo import FIFO
from .module import module


class CommonBuf(module):
    """
    Ping-pong tile buffer with per-lane bank switching.
    Follows compute/commit pattern (same as pe/spatial_array).

    load(vectors)  -- DMA: load vectors into each lane's inactive FIFO
    compute()      -- 相当于 request_read: peek post-swap output -> next_state
    commit()       -- 相当于 output: pop, apply swap, state = next_state
    state          -- list[N]: per-lane outputs (valid after commit())
    pending        -- True when ALL lanes have inactive data loaded
    reset()        -- clear all state

    Lane j skew: add_head=True -> j leading zeros; add_tail=True -> (N-j) trailing zeros.
    """

    def __init__(self, N: int):
        super().__init__()
        self.N              = N
        self._fifo_bank     = [[FIFO() for _ in range(N)], [FIFO() for _ in range(N)]]
        self._active        = [0] * N
        self._pending       = [False] * N
        self._transitioning = False
        self.state          = [None] * N
        self.next_state     = None

        self._next_active        = None
        self._next_pending       = None
        self._next_transitioning = None

    def load(self, vectors: list, add_head: bool = True, add_tail: bool = True,
             lanes: list = None):
        """Load vectors into each lane's inactive FIFO with staggered skew."""
        if lanes is None:
            lanes = range(self.N)
        for j in lanes:
            fifo = self._fifo_bank[1 - self._active[j]][j]
            vec  = vectors[j]
            head  = [0] * j            if add_head else []
            drain = [0] * (self.N - j) if add_tail else []
            fifo.load(head + list(vec) + drain)
            self._pending[j] = True

    def compute(self):
        """相当于 request_read: peek post-swap bank, 结果写入 next_state."""
        next_active        = list(self._active)
        next_pending       = list(self._pending)
        next_transitioning = self._transitioning

        if self.pending:
            next_transitioning = True
        if next_transitioning:
            for j in range(self.N):
                if next_pending[j] and self._fifo_bank[next_active[j]][j].empty():
                    next_active[j]  ^= 1
                    next_pending[j]  = False
            if not any(next_pending):
                next_transitioning = False

        self._next_active        = next_active
        self._next_pending       = next_pending
        self._next_transitioning = next_transitioning

        for j in range(self.N):
            self._fifo_bank[next_active[j]][j].compute()
        self.next_state = [self._fifo_bank[next_active[j]][j].next_state for j in range(self.N)]

    def commit(self):
        """相当于 output: pop post-swap bank, apply swap, state = next_state."""
        for j in range(self.N):
            self._fifo_bank[self._next_active[j]][j].commit()
        self.state          = self.next_state
        self._active        = self._next_active
        self._pending       = self._next_pending
        self._transitioning = self._next_transitioning
        self.next_state          = None
        self._next_active        = None
        self._next_pending       = None
        self._next_transitioning = None

    @property
    def pending(self) -> bool:
        """True when ALL lanes have inactive data loaded."""
        return all(self._pending)

    def reset(self):
        self._active        = [0] * self.N
        self._pending       = [False] * self.N
        self._transitioning = False
        self.state          = [None] * self.N
        self.next_state     = None
        self._next_active        = None
        self._next_pending       = None
        self._next_transitioning = None
        for bank in self._fifo_bank:
            for fifo in bank:
                fifo.reset()
