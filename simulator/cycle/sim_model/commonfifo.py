from collections import deque
from .fifo import FIFO
from .module import module


class CommonFIFO(module):
    """每 lane 一个 FIFO 存 tile 数据。两条读口，写口共用：

    OS —— update(wdata, feed)：controller 只给标量 feed；skew 由 fifo 内部 lane 传播生成
      （最左 lane 注入、逐拍右推，lane c 延 c 拍弹队头，见 doc/weight_fifo_design.md §9）。
    WS —— ws_update(wdata, ready, consume)：每列 valid/ready 反压，整行弹出（无 skew，见 §6）。

    update(wdata, feed):
      wdata -- DMA 本拍写入的一条深度向量 [N]（None/False 则不写）；攒满 K 条 = 一个完整 tile
      feed  -- 标量：这拍喂不喂。内部传播 _prop：lane c 在 feed 之后第 c 拍弹队头、否则出 0
    data     -- list[N] 本拍各 lane 输出（被点亮=队头，未点亮=0），commit 后有效
    vld      -- list[N] 各 lane 本拍是否点亮（给 sa 当 b_vld）
    avail    -- _loaded > _started：是否还有"完整且未开始喂"的 tile（仅 OS controller 用）
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
        self.vld           = [False] * self.N  # 各 lane 本拍是否点亮（给 sa 当 a_vld/b_vld），commit 后有效
        self._vld_next     = [False] * self.N  # 本拍点亮的 lane（update 暂存，commit 落定 vld）
        self._ws           = False             # 本拍走的是 WS 读口（ws_update）还是 OS 读口（update）
        self._rd           = [False] * self.N  # 本拍要 pop 的 lane（update 暂存，commit 用）
        self._prop         = [False] * self.N  # lane 方向传播 SR：最左注入、逐拍右推 → skew
        self._prop_next    = [False] * self.N
        self._wcount       = 0                 # 当前 tile 已写入的向量数
        self._loaded       = 0                 # 已写满的完整 tile 数
        self._started      = 0                 # 已开始喂的 tile 数
        self._started_next = 0
        self._heads        = deque()           # 与 lane0 队列平行的 tile-head 标记

    @property
    def avail(self):
        return self._loaded > self._started

    def _write(self, wdata):
        # 写入（DMA）：一条深度向量 / 拍，攒满 K 条 = 一个 tile（OS/WS 共用）
        if wdata is not None and wdata is not False:
            for c in range(self.N):
                self._fifos[c].push(wdata[c])
            self._heads.append(self._wcount == 0)   # 每 tile 首条标 head
            self._wcount += 1
            if self._wcount == self.K:
                self._wcount = 0
                self._loaded += 1

    def update(self, wdata, feed):
        self._ws = False
        self._write(wdata)

        # --- 读出（到阵列），两段式 ---
        # controller 给标量 feed；内部 lane 传播生成 skew：最左 lane 注入、逐拍右推，lane c 延 c 拍点亮
        self._prop_next = [bool(feed)] + self._prop[:-1]
        rd = self._prop_next
        self._rd = list(rd)
        dn = [0] * self.N
        for c in range(self.N):
            if rd[c]:
                self._fifos[c].compute()
                v = self._fifos[c].next_state
                dn[c] = 0 if v is None else v
        self.data_next = dn
        self._vld_next = list(rd)              # 点亮的 lane = 弹了真数据
        # lane0 这拍 pop 的若是 tile-head → 该 tile 开始喂（rd[0] = 本拍 feed）
        self._started_next = self._started + (1 if (rd[0] and self._heads and self._heads[0]) else 0)

    def ws_update(self, wdata, ready):
        # WS 读口（FWFT/show-ahead）：peek 队头组合输出 → self.data/self.vld 本拍直接可见。
        #   wdata -- DMA 写入一条深度向量 [N]（None/False 不写）；预载倒序写（见 WS_weight_design §11）
        #   ready -- list[N]：每列下游 ready（= sa 该列 shadow 能接受）
        # rden[c] = !empty[c] & ready[c]：commit 时弹出（消费完成）。输出在 sdpram + 1 深 skid
        # prefetch 的 RTL 模型下，等价于一直持有队头直到被消费，没有 1 拍 FF 滞后；这样 sa 看到
        # b_vld/b_data 与 rdy 同拍握手，不会再出现"pop 完下拍才到、shadow 已满 → 数据丢"的 overshoot。
        # 同拍 push (_write) 仍放 peek 之后：RTL 里 cy K 的 wr 在 posedge 才落到 wr_addr，cy K 的
        # !empty 检查用的是 push 前的状态。
        self._ws = True

        # peek BEFORE write —— 组合输出当前队头
        for c in range(self.N):
            if not self._fifos[c].empty():
                self._fifos[c].compute()
                v = self._fifos[c].next_state
                self.data[c] = 0 if v is None else v
                self.vld[c]  = True
                self._rd[c]  = bool(ready[c])   # consumer 接 → commit 时 pop
            else:
                self.data[c] = 0
                self.vld[c]  = False
                self._rd[c]  = False

        # write LAST —— push 这拍落到 fifo，下拍才能被 peek 看到
        self._write(wdata)

    def commit(self):
        for c in range(self.N):
            if self._rd[c]:
                self._fifos[c].commit()
        if not self._ws:                       # OS：寄存输出 + tile-head / avail / 传播寄存器推进
            self.data = self.data_next
            self.vld  = list(self._vld_next)   # 点亮的 lane = 弹了真数据
            if self._rd[0] and self._heads:
                self._heads.popleft()
            self._started = self._started_next
            self._prop = self._prop_next
        # WS：data/vld 已在 ws_update 直接写好（组合输出），commit 只负责 pop
