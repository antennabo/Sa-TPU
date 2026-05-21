from .fifo import FIFO


class TileBuf:
    """
    Ping-pong tile buffer with per-lane bank switching.

    load(vectors)  — DMA: load vectors into each lane's inactive FIFO
    swap()         — flip active bank for lanes whose active FIFO is empty,
                     only when buffer-level pending is complete
    pending        — True when ALL lanes have inactive data loaded
    pop(j)         — pop one value from lane j's active FIFO
    pop_all()      — pop one value from every lane's active FIFO
    reset()        — clear all state

    Two states:
        steady      — all lanes on the same active bank
        transitioning — buffer pending complete; lanes switching as they drain
    """

    def __init__(self, N: int):
        self.N             = N
        self._fifo_bank    = [[FIFO() for _ in range(N)],
                              [FIFO() for _ in range(N)]]
        self._active       = [0] * N       # FIFO-level: active bank index per lane
        self._pending      = [False] * N   # FIFO-level: inactive bank has data
        self._transitioning = False        # buffer-level: swap in progress

    def load(self, vectors: list, add_head: bool = True, add_tail: bool = True,
             lanes: list = None):
        """
        Load vectors into each lane's inactive FIFO with staggered skew.

        Lane j gets:
            add_head=True  — j leading zeros  (first tile of a sequence)
            add_tail=True  — (N-j) trailing zeros  (last tile of a sequence)
            Both False     — data only, back-to-back with adjacent tiles.
        lanes: list of lane indices to load; None means all.
        """
        if lanes is None:
            lanes = range(self.N)
        for j in lanes:
            fifo = self._fifo_bank[1 - self._active[j]][j]
            vec  = vectors[j]
            head  = [0] * j            if add_head else []
            drain = [0] * (self.N - j) if add_tail else []
            fifo.load(head + list(vec) + drain)
            self._pending[j] = True

    def swap(self):
        """
        For each lane whose active FIFO is empty, switch it to the inactive bank.
        Entry condition: buffer-level pending complete.
        Continues until all lanes have switched (_transitioning cleared).
        """
        if self.pending:
            self._transitioning = True
        if not self._transitioning:
            return
        for j in range(self.N):
            if self._pending[j] and self._fifo_bank[self._active[j]][j].empty():
                self._active[j]  ^= 1
                self._pending[j]  = False
        if not any(self._pending):
            self._transitioning = False

    def pop(self, j: int):
        """Pop one element from lane j's active FIFO."""
        return self._fifo_bank[self._active[j]][j].pop()

    def pop_all(self) -> list:
        """Pop one element from every lane's active FIFO. Returns list of length N."""
        return [self.pop(j) for j in range(self.N)]

    @property
    def pending(self) -> bool:
        """Buffer-level: True when ALL lanes have inactive data loaded."""
        return all(self._pending)

    def reset(self):
        self._active        = [0] * self.N
        self._pending       = [False] * self.N
        self._transitioning = False
        for bank in self._fifo_bank:
            for fifo in bank:
                fifo._q.clear()
