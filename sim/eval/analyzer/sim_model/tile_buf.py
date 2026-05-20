from .fifo import FIFO


class TileBuf:
    """
    Generic ping-pong tile buffer with staggered FIFO streaming.

    load(vectors)  — DMA: stagger vectors into inactive FIFO bank
                     vectors: list of N sequences, one per FIFO lane
    swap()         — flip active ↔ inactive
    output_fifo    — active FIFO bank; pop each cycle to feed the SA
    pending        — inactive bank has data waiting for swap()
    reset()        — clear all state
    """

    def __init__(self, N: int):
        self.N          = N
        self._fifo_bank = [[FIFO() for _ in range(N)],
                           [FIFO() for _ in range(N)]]
        self._active    = 0
        self._pending   = False

    @property
    def output_fifo(self) -> list:
        """Active FIFO bank — SA pops from here each cycle."""
        return self._fifo_bank[self._active]

    def load(self, vectors: list, tail: int = None):
        """
        Stagger vectors into inactive FIFO bank.

        vectors[j] is the data sequence for lane j.
        Lane j is preceded by j zeros and followed by (tail-j) zeros.
        tail defaults to N.
        """
        if tail is None:
            tail = self.N
        for j, (fifo, vec) in enumerate(zip(self._fifo_bank[1 - self._active], vectors)):
            fifo.load([0] * j + list(vec) + [0] * (tail - j))
        self._pending = True

    def swap(self):
        """Flip active ↔ inactive. Raises if nothing is pending."""
        if not self._pending:
            raise RuntimeError("TileBuf: swap() called with no pending tile")
        self._active  = 1 - self._active
        self._pending = False

    @property
    def pending(self) -> bool:
        """True when the inactive bank has data waiting for swap()."""
        return self._pending

    def reset(self):
        self._active  = 0
        self._pending = False
        for bank in self._fifo_bank:
            for fifo in bank:
                fifo._q.clear()
