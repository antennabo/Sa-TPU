"""Accumulator — per-AC 输出累加器 (镜像 rtl/accumulator.sv)。

数据面: 每拍 i_psum[c] / i_psum_vld[c]            ← SA o_out / o_out_vld
地址面: 每拍 per-column 已 deskew 的 (wr_vld, wr_addr, acc_en, out_en)  ← controller

存储: per-column 平坦 mem[col][addr]，地址 = ACC_ADDR_W 位 (无 slot/row 二级结构)。
内部 1stg→2stg pipeline:
  1stg (T):   寄存 controller / SA 信号
  2stg (T+1): wdata = acc_en ? psum + rdata : psum
              wr_en = psum_vld & wr_vld
              waddr = wr_addr (delayed 1)
              写 mem; 同时 o_rlt / o_rlt_vld 给下游 (out_en gate)

读口: 同步读 1 拍 latency (per-column i_rd_en / i_rd_addr → o_rd_data)。

D8 (2026-06-27): per-column deskew SR 上移到 controller，本模块退化为 per-AC 直收。
slot 概念取消，地址平坦化。
"""

import logging
from .module import module

logger = logging.getLogger(__name__)


class Accumulator(module):
    """构造参数：
      num_cols  — AC，per-column 数
      mem_depth — 每列 mem 深度 (= 2**ACC_ADDR_W)
    """

    def __init__(self, num_cols: int, mem_depth: int = 1024):
        super().__init__()
        self.N         = num_cols
        self.mem_depth = mem_depth
        self.reset()

    def reset(self):
        N = self.N
        # 每列独立 mem
        self._mem = [[0] * self.mem_depth for _ in range(N)]
        # 1stg 寄存 (=输入 latched)
        self._wr_vld_1stg   = [False] * N
        self._wr_addr_1stg  = [0]     * N
        self._acc_en_1stg   = [False] * N
        self._out_en_1stg   = [False] * N
        self._psum_1stg     = [0]     * N
        self._psum_vld_1stg = [False] * N
        # 2stg 寄存 + 输出
        self._wr_en_2stg    = [False] * N
        self._waddr_2stg    = [0]     * N
        self._wdata_2stg    = [0]     * N
        self.o_rlt          = [0]     * N
        self.o_rlt_vld      = [False] * N
        # 读出 (combinational rd)
        self.o_rd_data      = [0]     * N
        # next 槽
        self._next_wr_vld   = [False] * N
        self._next_wr_addr  = [0]     * N
        self._next_acc_en   = [False] * N
        self._next_out_en   = [False] * N
        self._next_psum     = [0]     * N
        self._next_psum_vld = [False] * N
        self._next_wr_en_2  = [False] * N
        self._next_waddr_2  = [0]     * N
        self._next_wdata_2  = [0]     * N
        self._next_rlt_vld  = [False] * N

    def update(self, i_psum, i_psum_vld,
               i_wr_vld, i_wr_addr, i_acc_en, i_out_en,
               i_rd_en, i_rd_addr):
        """所有输入都是 list[N]。返回值无（输出通过 o_rlt / o_rlt_vld / o_rd_data 属性读）。"""
        N = self.N

        # 1stg 直通 (FF 输入)
        self._next_wr_vld   = [bool(x) for x in i_wr_vld]
        self._next_wr_addr  = [int(x)  for x in i_wr_addr]
        self._next_acc_en   = [bool(x) for x in i_acc_en]
        self._next_out_en   = [bool(x) for x in i_out_en]
        self._next_psum     = [int(x)  for x in i_psum]
        self._next_psum_vld = [bool(x) for x in i_psum_vld]

        # 2stg 用 1stg 的当前 FF state 算
        for c in range(N):
            if self._acc_en_1stg[c]:
                self._next_wdata_2[c] = self._psum_1stg[c] + self._mem[c][self._wr_addr_1stg[c]]
            else:
                self._next_wdata_2[c] = self._psum_1stg[c]
            self._next_wr_en_2[c] = self._psum_vld_1stg[c] and self._wr_vld_1stg[c]
            self._next_waddr_2[c] = self._wr_addr_1stg[c]
            self._next_rlt_vld[c] = self._psum_vld_1stg[c] and self._out_en_1stg[c]

        # 读口 (combinational, REG_OUT=0): 当拍 i_rd_en/i_rd_addr 直接出
        for c in range(N):
            if i_rd_en[c]:
                self.o_rd_data[c] = self._mem[c][int(i_rd_addr[c])]
            # else: 保持上一拍 (sdpram 行为)

        # 暂存写入 (commit 时落盘)
        self._pending_writes = []
        for c in range(N):
            if self._next_wr_en_2[c]:
                addr = self._next_waddr_2[c] % self.mem_depth
                self._pending_writes.append((c, addr, self._next_wdata_2[c]))

    def commit(self):
        # 1stg / 2stg / 输出 寄存器锁存
        self._wr_vld_1stg   = list(self._next_wr_vld)
        self._wr_addr_1stg  = list(self._next_wr_addr)
        self._acc_en_1stg   = list(self._next_acc_en)
        self._out_en_1stg   = list(self._next_out_en)
        self._psum_1stg     = list(self._next_psum)
        self._psum_vld_1stg = list(self._next_psum_vld)
        self._wr_en_2stg    = list(self._next_wr_en_2)
        self._waddr_2stg    = list(self._next_waddr_2)
        self._wdata_2stg    = list(self._next_wdata_2)
        self.o_rlt          = list(self._wdata_2stg)
        self.o_rlt_vld      = list(self._next_rlt_vld)
        # 落 mem
        for c, addr, val in self._pending_writes:
            self._mem[c][addr] = val
        self._pending_writes = []

    def tick(self, i_psum, i_psum_vld,
             i_wr_vld, i_wr_addr, i_acc_en, i_out_en,
             i_rd_en, i_rd_addr):
        self.update(i_psum, i_psum_vld, i_wr_vld, i_wr_addr, i_acc_en, i_out_en,
                    i_rd_en, i_rd_addr)
        self.commit()

    def get_col(self, c: int):
        """直接读 col c 的 mem 内容 (调试用)。"""
        return list(self._mem[c])
