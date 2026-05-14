from collections import deque


class FIFO:
    """
    通用硬件 FIFO 模型。

    depth       — 最大容量（None 表示无限）
    push(item)  — 写入一个元素；满时抛出 OverflowError
    pop()       — 读出一个元素；空时返回 None
    peek()      — 查看队头，不消耗
    load(items) — 批量写入
    full()      — 是否已满
    empty()     — 是否为空
    """

    def __init__(self, depth=None):
        self.depth = depth
        self._q = deque()

    def push(self, item):
        if self.depth is not None and len(self._q) >= self.depth:
            raise OverflowError(f"FIFO full (depth={self.depth})")
        self._q.append(item)

    def pop(self):
        return self._q.popleft() if self._q else None

    def peek(self):
        return self._q[0] if self._q else None

    def load(self, items):
        for item in items:
            self.push(item)

    def full(self):
        return self.depth is not None and len(self._q) >= self.depth

    def empty(self):
        return len(self._q) == 0

    def __len__(self):
        return len(self._q)
