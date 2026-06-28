from .module import module


class CommonBuf(module):
    """纯被动 lane buffer（对齐 rtl/activation_buf.sv）：N 个 lane，每 lane 一条 list（深度 DEPTH）。

    模块内无控制状态，地址完全外部驱动。
    写口、读口都是 per-lane (en, addr, data)，跟 RTL activation_buf 一一对应。
    输出 data/vld 寄存 1 拍：本拍 update 触发的读 → commit 后 data/vld 反映出来（1 个 FF 延迟，
    对齐 sdpram REG_OUT=0 + rd_vld FF）。

    地址生成（per-lane offset 自加、start_addr 跳变重置）由外部 AbufRdAddrGen 完成；ping-pong
    page 等"多段数据"语义也由外部通过 start_addr 表达，本模块不感知。
    """

    def __init__(self, N, DEPTH):
        super().__init__()
        self.N     = N
        self.DEPTH = DEPTH
        self.reset()

    def reset(self):
        self._mem       = [[0] * self.DEPTH for _ in range(self.N)]
        self.data       = [0] * self.N
        self.vld        = [False] * self.N
        self._data_next = [0] * self.N
        self._vld_next  = [False] * self.N

    def update(self, wr_en, wr_addr, wr_data, rd_en, rd_addr):
        # 写口：本拍 wr_en[c]==True 即写 _mem[c][wr_addr[c]] = wr_data[c]
        for c in range(self.N):
            if wr_en[c]:
                self._mem[c][int(wr_addr[c])] = wr_data[c]
        # 读口：本拍 rd_en[c]==True → 下拍 commit 后 data[c]/vld[c] 反映读出
        data_next = [0] * self.N
        vld_next  = [False] * self.N
        for c in range(self.N):
            if rd_en[c]:
                data_next[c] = self._mem[c][int(rd_addr[c])]
                vld_next[c]  = True
        self._data_next = data_next
        self._vld_next  = vld_next

    def commit(self):
        self.data = list(self._data_next)
        self.vld  = list(self._vld_next)
