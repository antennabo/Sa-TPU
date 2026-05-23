from .fifo import FIFO
from .module import module


class CommonFIFO(module):
    """
    Per-lane FIFO buffer with staggered skew. Replaces CommonBuf ping-pong.

    load(vectors, add_head)  -- push vectors into each lane's FIFO with diagonal skew
    request_read()           -- peek head of each lane -> next_state
    output()                 -- pop head, state = next_state
    state                    -- list[N]: per-lane current values (None if empty)
    pending                  -- True when all lanes have data
    reset()

    Lane j skew: add_head=True -> j leading zeros; add_tail=True -> (N-j) trailing zeros.
    """

    def __init__(self, N: int):
        super().__init__()
        self.N      = N
        self._fifos = [FIFO() for _ in range(N)]
        self.state      = [None] * N
        self.next_state = None

    def load(self, vectors: list, add_head: bool = False, add_tail: bool = False,
             lanes: list = None):
        if lanes is None:
            lanes = range(self.N)
        for j in lanes:
            head = [0] * j            if add_head else []
            tail = [0] * (self.N - 1 - j) if add_tail else []
            self._fifos[j].load(head + list(vectors[j]) + tail)

    @property
    def pending(self) -> bool:
        return all(not f.empty() for f in self._fifos)

    def request_read(self):
        for f in self._fifos:
            f.compute()
        self.next_state = [f.next_state for f in self._fifos]

    def output(self):
        for f in self._fifos:
            f.commit()
        self.state      = self.next_state
        self.next_state = None

    def reset(self):
        for f in self._fifos:
            f.reset()
        self.state      = [None] * self.N
        self.next_state = None
