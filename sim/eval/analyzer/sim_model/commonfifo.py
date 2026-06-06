from collections import deque
from .fifo import FIFO
from .module import module


class CommonFIFO(module):
    """每 lane 一个 FIFO 存 tile 数据。skew 由 controller 的 read mask 提供（buffer 不补零）。
    对外只有 update：同拍做写入(DMA)和读出(到阵列)。

    update(wdata, rd):
      wdata -- DMA 本拍写入的一条深度向量 [N]（None/False 则不写）；攒满 K 条 = 一个完整 tile
      rd    -- 读 mask [N]：rd[c]=1 → lane c 出队头并推进；rd[c]=0 → 出 0
    data     -- list[N] 本拍各 lane 输出（被读=队头，未读=0），commit 后有效
    avail    -- _loaded > _started：是否还有"完整且未开始喂"的 tile（controller 用）
    """

    def __init__(self, N: int, K: int):
        super().__init__()
        self.N = N
        self.K = K
        self._fifos = [FIFO() for _ in range(N)]
        self.reset()

    def reset(self):
        for f in self._fifos:
            f.reset()
        self.data          = [0] * self.N
        self.data_next     = [0] * self.N
        self._rd           = [False] * self.N  # 本拍要 pop 的 lane（update 暂存，commit 用）
        self._wcount       = 0                 # 当前 tile 已写入的向量数
        self._loaded       = 0                 # 已写满的完整 tile 数
        self._started      = 0                 # 已开始喂的 tile 数
        self._started_next = 0
        self._heads        = deque()           # 与 lane0 队列平行的 tile-head 标记

    @property
    def avail(self):
        return self._loaded > self._started

    def update(self, wdata, rd):
        # --- 写入（DMA）：一条深度向量 / 拍 ---
        if wdata is not None and wdata is not False:
            for c in range(self.N):
                self._fifos[c].push(wdata[c])
            self._heads.append(self._wcount == 0)   # 每 tile 首条标 head
            self._wcount += 1
            if self._wcount == self.K:
                self._wcount = 0
                self._loaded += 1

        # --- 读出（到阵列），两段式 ---
        self._rd = list(rd)
        dn = [0] * self.N
        for c in range(self.N):
            if rd[c]:
                self._fifos[c].compute()
                v = self._fifos[c].next_state
                dn[c] = 0 if v is None else v
        self.data_next = dn
        # lane0 这拍 pop 的若是 tile-head → 该 tile 开始喂
        self._started_next = self._started + (1 if (rd[0] and self._heads and self._heads[0]) else 0)

    def commit(self):
        for c in range(self.N):
            if self._rd[c]:
                self._fifos[c].commit()
        if self._rd[0] and self._heads:
            self._heads.popleft()
        self.data = self.data_next
        self._started = self._started_next
