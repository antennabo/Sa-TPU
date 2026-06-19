from collections import deque
from .module import module


class FIFO(module):
    """
    Cycle-accurate FIFO model.
    Follows compute/commit pattern.

    push(item)  -- write to tail immediately (DMA-side input)
    load(items) -- batch push
    compute()   -- peek head -> next_state
    commit()    -- pop head, state = next_state
    state       -- head value from last commit (None if was empty)
    full()      -- combinational: queue at capacity
    empty()     -- combinational: queue has no elements
    """

    def __init__(self, depth=None):
        super().__init__()
        self.depth      = depth
        self._q         = deque()
        self.state      = None
        self.next_state = None

    def push(self, item):
        if self.depth is not None and len(self._q) >= self.depth:
            raise OverflowError(f"FIFO full (depth={self.depth})")
        self._q.append(item)

    def load(self, items):
        for item in items:
            self.push(item)

    def compute(self):
        self.next_state = self._q[0] if self._q else None

    def commit(self):
        if self._q:
            self._q.popleft()
        self.state      = self.next_state
        self.next_state = None

    def full(self):
        return self.depth is not None and len(self._q) >= self.depth

    def empty(self):
        return len(self._q) == 0

    def __len__(self):
        return len(self._q)

    def reset(self):
        self._q.clear()
        self.state      = None
        self.next_state = None
