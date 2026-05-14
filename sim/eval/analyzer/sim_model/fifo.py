from collections import deque


class FIFO:
    """
    通用硬件 FIFO 模型。

    push(item)  — 写入一个元素
    pop()       — 读出一个元素（空时返回 None）
    peek()      — 查看队头，不消耗
    load(items) — 批量写入
    """

    def __init__(self):
        self._q = deque()

    def push(self, item):
        self._q.append(item)

    def pop(self):
        return self._q.popleft() if self._q else None

    def peek(self):
        return self._q[0] if self._q else None

    def load(self, items):
        self._q.extend(items)

    def empty(self):
        return len(self._q) == 0

    def __len__(self):
        return len(self._q)
