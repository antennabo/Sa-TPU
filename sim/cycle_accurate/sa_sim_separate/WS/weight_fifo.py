import numpy as np
import collections


class WeightFIFO:
    def __init__(self, N, depth=None):
        self.N = N
        self.depth = depth
        self.queue = collections.deque()  # double ended queue, which you can add items on both sides, left and right
        self._current_tile = None
        self._load_counter = 0
        self._state = 'idle'

    def push_tile(self, W_tile):
        if self.depth is not None and len(self.queue) >= self.depth:
            return False
        self.queue.append(W_tile.copy()) # append adds an item to the right side of the queue
        return True

    def step(self):
        N = self.N
        if self._state == 'idle':
            if not self.queue:
                return (np.zeros(N, dtype=np.int8), False)
            self._current_tile = self.queue.popleft()  # pop out items from the left side, pop() with pop out items from the right side
            self._state = 'loading'
            self._load_counter = 0

        row_idx = N - 1 - self._load_counter
        out_row = self._current_tile[row_idx, :].copy()
        self._load_counter += 1
        if self._load_counter == N:
            self._state = 'idle'
            self._current_tile = None
        return (out_row, True)

    # To check if the weight preload is completed.
    def is_idle(self):
        return self._state == 'idle' and len(self.queue) == 0

    def reset(self):
        self.queue.clear()
        self._current_tile = None
        self._load_counter = 0
        self._state = 'idle'
