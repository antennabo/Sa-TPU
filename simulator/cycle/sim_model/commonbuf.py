from .module import module


class CommonBuf(module):
    """每 lane 一个可寻址缓冲（带读指针、自加、到顶归 0 复用）。喂料机制与 CommonFIFO 相同：controller
    只给标量 feed，skew 由内部 lane 传播 _prop 生成（最左 lane 注入、逐拍右推，lane c 延 c 拍点亮，见
    doc/common_buf_design.md §6）。与 fifo 的区别：点亮的 lane 按【读指针】取数、**不弹走**——
    指针自加、封顶 tile_num*K 时归 0（自动复用同一份激活喂下个 N-tile）。

    地址 {page, tile_id, row}：page=ping-pong 双缓冲页（switch_page 占位，后续指令解析驱动）；
    一个线性自加指针同时表 {tile_id, row}（addr // K = tile_id，addr % K = row），封顶 = tile_num*K。

    update(wdata, feed, tile_num=1):
      wdata    -- DMA 本拍写入的一条深度向量 [N]（None/False 则不写）；写当前 page，存住不消费
      feed     -- 标量：这拍喂不喂。内部 _prop：lane c 在 feed 之后第 c 拍点亮、否则出 0
      tile_num -- 当前 page 内 tile 数；读指针封顶 = tile_num*K（到顶归 0）
    data       -- list[N] 本拍各 lane 输出（点亮=读指针处的值，未点亮=0），commit 后有效
    vld        -- list[N] 各 lane 本拍是否点亮（给 sa 当 a_vld/b_vld）
    switch_page() -- ping-pong 切页（占位，后续指令解析驱动）：翻活动页、读指针归 0
    """

    def __init__(self, N: int, K: int):
        super().__init__()
        self.N = N
        self.K = K                                      # 每 tile 深度（行数）
        self.reset()

    def reset(self):
        self._buf       = [[[] for _ in range(self.N)] for _ in range(2)]  # 2 页 ping-pong，每 lane 一条 list
        self._page      = 0                             # 当前活动页（读/写同页；真 ping-pong 由指令切页后续接）
        self.data       = [0] * self.N
        self.data_next  = [0] * self.N
        self.vld        = [False] * self.N
        self._rd        = [False] * self.N              # 本拍点亮的 lane（update 暂存，commit 用）
        self._prop      = [False] * self.N              # lane 方向传播 SR：最左注入、逐拍右推 → skew
        self._prop_next = [False] * self.N
        self._ptr       = [0] * self.N                  # 每 lane 读指针（点亮时取数 + 自加，封顶归 0）
        self._ptr_next  = [0] * self.N

    def update(self, wdata, feed, tile_num: int = 1):
        # --- 写入（DMA）：一条深度向量 / 拍，存住当前页（不消费）---
        if wdata is not None and wdata is not False:
            for c in range(self.N):
                self._buf[self._page][c].append(wdata[c])

        # --- 读出（到阵列），两段式 ---
        # 标量 feed → 内部 lane 传播生成 skew：最左 lane 注入、逐拍右推，lane c 延 c 拍点亮
        self._prop_next = [bool(feed)] + self._prop[:-1]
        rd = self._prop_next
        self._rd = list(rd)
        cap = tile_num * self.K                          # 读指针封顶 = tile_num*K（到顶归 0 复用）
        page = self._page
        dn = [0] * self.N
        ptr_next = list(self._ptr)
        for c in range(self.N):
            if rd[c]:                                    # 点亮：读指针处取数（不弹走），指针自加、封顶归 0
                if self._ptr[c] < len(self._buf[page][c]):
                    dn[c] = self._buf[page][c][self._ptr[c]]
                ptr_next[c] = (self._ptr[c] + 1) % cap
        self.data_next = dn
        self._ptr_next = ptr_next

    def commit(self):
        self.data = self.data_next
        self.vld  = list(self._rd)                       # 点亮的 lane = 读了真数据
        self._ptr = self._ptr_next
        self._prop = self._prop_next

    def switch_page(self):
        # ping-pong 切页（占位，后续指令解析驱动）：翻活动页、读指针归 0
        self._page ^= 1
        self._ptr      = [0] * self.N
        self._ptr_next = [0] * self.N
