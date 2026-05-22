from .module import module


class SRAM(module):
    """
    Cycle-accurate SRAM following compute/commit pattern.

    Per-cycle usage:
      write(addr, data)   -- schedule write (before compute)
      request_read(addr)  -- schedule read (before compute)
      compute()           -- advance read pipeline → next_state
      commit()            -- apply pending write, state = next_state
      state               -- data from last completed read, or None
    """

    def __init__(self, depth: int, read_latency: int = 1):
        assert read_latency >= 1
        super().__init__()
        self.depth        = depth
        self.read_latency = read_latency
        self._mem           = [None] * depth
        self._read_pipeline = []    # list of [cycles_remaining, addr]
        self._pending_write = None  # (addr, data)
        self.state          = None
        self.next_state     = None

    def request_read(self, addr: int):
        assert 0 <= addr < self.depth, f"addr {addr} out of range [0, {self.depth})"
        self._read_pipeline.append([self.read_latency, addr])

    def write(self, addr: int, data):
        assert 0 <= addr < self.depth, f"addr {addr} out of range [0, {self.depth})"
        self._pending_write = (addr, data)

    def compute(self):
        for entry in self._read_pipeline:
            entry[0] -= 1
        ready = [e for e in self._read_pipeline if e[0] == 0]
        self._read_pipeline = [e for e in self._read_pipeline if e[0] > 0]
        self.next_state = self._mem[ready[-1][1]] if ready else None

    def commit(self):
        if self._pending_write is not None:
            addr, data = self._pending_write
            self._mem[addr] = data
            self._pending_write = None
        self.state      = self.next_state
        self.next_state = None

    def reset(self):
        self._mem           = [None] * self.depth
        self._read_pipeline = []
        self._pending_write = None
        self.state          = None
        self.next_state     = None
