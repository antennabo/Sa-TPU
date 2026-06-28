> ⚠ **DEPRECATED** — activation buf 早期设计稿（CommonBuf 模型）。当前 RTL
> `rtl/activation_buf.sv` 是纯被动的 per-lane RAM（外部驱动地址、无 page / 自动 skew）。
> 行 skew + 地址生成都由 controller 内的 `offset[AR]` SR 完成（见
> [../controller_ws.md](../controller_ws.md)）。保留为历史参考；新工作请读 doc/ 根下的新版。

---

# common buf 设计（CommonBuf：可寻址缓冲，存一整层数据、标量 feed 斜读、可重读复用）

本文档写给**第一次接触本项目**的读者，自顶向下：先讲 common buf 要解决什么，再讲整体、接口、细节。
它和 [weight_fifo_design.md](weight_fifo_design.md) 配套——两者都是"喂到阵列嘴边的料源"，但一个**消费即弃**（fifo）、
一个**存住可重读**（buf）。

> `CommonBuf` 与 `CommonFIFO` 并列：fifo 给 OS 的 weight/activation、WS 的 weight（消费即弃）；
> **buf 给 WS 的 activation、IS 的 weight**——这些是**流动且跨 tile 复用**的操作数。

---

# 第一部分 · 需求与目标

## 1. common buf 要解决什么

WS 下权重驻留、激活**斜着流入**、M 行走时间。一个 K-chunk 的同一份激活，会被**多个 N-tile 复用**
（见 [WS_weight_design.md §0.2](WS_weight_design.md)：`A[:,0:2]` 先配 `W00`、后又配 `W01`）。

```
   DMA 搬运           common buf             阵列边缘
  (载一整层) ──写─▶  [存住整层激活]  ──斜读─▶  (每拍一行激活)
                      └ 可重读：同一份激活喂给下一个 N-tile，不必重新 DMA
```

- **存住**：把**一个网络层的全部激活**直接缓存住（当前 buf 容量 = 一层；不像 fifo 逐向量攒）。
- **斜读**：和 fifo 一样，按 controller 的标量 feed、内部生成 skew 斜着推给阵列。
- **可重读（复用）**：同一份激活跨 N-tile 重读，省掉重新 DMA。

## 2. 为什么是 buf 不是 fifo

| | fifo（CommonFIFO）| buf（CommonBuf）|
|---|---|---|
| 读 | 出队头、**消费即弃** | 按**地址**读、**数据留住** |
| 复用 | 要再用得**重新 DMA** | **重读**即可，免重载 |
| 用于 | OS weight/activation、WS weight | **WS activation、IS weight**（流动且复用）|

复用要"同一份数据多次读"，fifo 消费即弃做不到——所以驻留侧之外、**流动且复用**的操作数用 buf。

---

# 第二部分 · 整体设计

## 3. common buf 在系统里的位置

```
        ┌─────────────┐
        │     DMA      │  载入一整层激活（本阶段：直接存整层）
        └──────┬──────┘
               │ write
               ▼
   ┌───────────────────────┐     controller: 标量 feed
   │      common buf        │ ◀──（这拍喂不喂；skew 由 buf 内部传播）
   │  lane0 lane1 ... laneAR│
   │   存整层 + 每 lane 读指针│ ──斜读──▶  阵列边缘 a_data[AR] (+ a_vld)
   └───────────────────────┘
        重读：复用同一层喂下个 N-tile（策略见 §7，待定）
```

- **写口（DMA → buf）**：载入一整层激活（本阶段直接存整层；写入粒度/节奏**待定**，同 weight fifo）。
- **读口（buf → 阵列）**：controller 给标量 feed，buf 内部 lane 传播出 skew，斜着推给阵列边缘 `a_data[AR]`。
- **复用**：同一层激活可被多个 N-tile 重读（读指针 rewind），免重新 DMA。何时 rewind / 何时换层见 §7（待定）。

## 4. 与 weight fifo（CommonFIFO）的异同

| | 相同 | 不同 |
|---|---|---|
| 读控制 | 都收 controller **标量 feed**，**skew 由源内部 lane 传播生成**（[weight_fifo_design.md](weight_fifo_design.md) §9）| —— |
| 数据 | 都按 lane 切（lane = 阵列一行/列）| fifo 出队头消费；buf 按**读指针/地址**读、**数据留住可重读** |
| 复用 | —— | buf 能 **rewind 重读**（复用），fifo 不能 |
| 存法 | —— | 本阶段 buf **直接存整层**；fifo 逐深度向量攒 |

> 一句话：**common buf = "带读指针、可重读"的 common fifo**。喂料的标量 feed + 内部 skew 完全一样，
> 只是把"出队头消费"换成"按指针读、读完不丢"，于是能复用。

---

# 第三部分 · 接口

## 5. 写口（DMA 载入一整层）

```
update(wdata, feed, tile_num=1):
  wdata    -- DMA 本拍写入的一条深度向量 [N]（None/False = 这拍不写）；写当前活动 page
  feed     -- 标量：这拍喂不喂（读控制，§6）
  tile_num -- 当前 page 内 tile 数；读指针封顶 = tile_num*K（§6）
```
- 本阶段**直接存当前 page**：buf 一页持有一批激活（`tile_num` 个 tile），供按指针自加重读、跨 N-tile 复用。
- **写入粒度 / 节奏待定**——取决于 DMA 设计（低优先级，PIO v1），不影响读出 / 复用语义。

## 6. 读口：标量 feed + 内部 lane 传播（skew）+ 自加读指针

和 weight fifo 完全一样的喂料机制（[weight_fifo_design.md](weight_fifo_design.md) §9）：
- controller 给**一个标量** `feed`（这拍喂不喂）。**控制只给 feed，读地址全由 buf 内部自管。**
- buf 内部一条 **lane 方向传播寄存器**：最左 lane 注入、逐拍右推，lane `k` 延 `k` 拍点亮 → **skew 在 buf 内部生成**。
- 点亮的 lane 按**读指针**取本拍该读的那一深度，推到阵列边缘 `a_data[AR]`（+ `a_vld`）。

**读地址 `{page, tile_id, row}`**（每 lane 一个线性自加指针表 `{tile_id, row}`）：
- 指针被点亮那拍取数、**自加**；`addr // K = tile_id`、`addr % K = row`（K=每 tile 深度）。
- **封顶 = `tile_num*K`**（`tile_num` 由 `update` 传入 = 当前 page 内 tile 数）；**到顶归 0**，自动重读同一份激活喂下个 N-tile（复用，§7）。
- `page` = ping-pong 双缓冲页，由 `switch_page` 切（指令驱动，§7）。

> 区别只在"取数"那一步：fifo 是 `pop` 队头（弹走），buf 是按**读指针**读（不弹走 → 自加到顶归 0 可重读）。

## 7. 复用 / 重读 + ping-pong 切页

| 事件 | buf 行为 | 触发来源 |
|---|---|---|
| 同 page 复用（下个 N-tile 重用同一份激活）| 读指针自加**到顶 `tile_num*K` 归 0**、重读 | **自动**（无需外部信号；§6）|
| 换页（双缓冲：另一页正被 DMA 载新数据）| `switch_page`：翻活动页、读指针归 0 | **指令驱动**（后续指令解析，本阶段占位 hook）|

- **复用**：指针自加到顶归 0 是**自动**的——一个 page 的 `tile_num` 个 tile 读完即循环，天然喂下个 N-tile，不需 controller 给 reuse 标量。
- **page（ping-pong）**：两页隔离存储，一页被阵列读、另一页 DMA 载下一批，`switch_page` 翻页隐藏访存。**切页时机由指令解析给**（本阶段只留 `switch_page()` 占位，不自动触发）。
- WS-1（单 tile、单 page、`tile_num=1`）：指针扫一遍 K 行即停，不触发归 0、不切页。

---

# 第四部分 · 内部细节

## 8. 存储（2 页 ping-pong）+ 每 lane 自加读指针

- buf 存 **2 页**（ping-pong），每页每 lane 一条序列；对外按 **AR 个 lane**（每个 = 阵列一行 k）+ **读指针**寻址。
- **lane 传播寄存器**（长 `AR`）：标量 feed 最左注入、逐拍右推，生成 skew（同 [weight_fifo_design.md](weight_fifo_design.md) §9）。
- **每 lane 一个读指针**：被点亮那拍，活动页读指针处取数、指针 **自加**（`% (tile_num*K)` → 到顶归 0）。读指针**不随读消失**，自动归 0 即重读（§6/§7）。

> 与 CommonFIFO 的唯一实现差别：把"队头 pop + commit 推进"换成"读指针取数 + 自加（封顶归 0）"。
> 存储现用每 lane 一条 list（与 fifo 同时序）；如需读延迟模型可换 [sram.py](../sim/eval/analyzer/sim_model/sram.py)（可寻址、带读延迟）。

## 9. 复用 / 切页控制

- **复用（同 page）**：读指针自加**到顶 `tile_num*K` 归 0**，自动重读 → 喂下个 N-tile。**无需外部触发**。
- **切页（ping-pong）**：`switch_page()` 翻活动页、读指针归 0 → 切到 DMA 刚载好的另一页。**触发来源 = 指令解析（后续）**，本阶段占位。
- 读/写两段式：`update` 算 `*_next`、`commit` 落定（与全项目一致）。

---

# 附录

## 10. 现状与待做

| 项 | 状态 |
|---|---|
| CommonBuf 模块 | ✅ **已实现**（[commonbuf.py](../sim/eval/analyzer/sim_model/commonbuf.py)：2 页 + 每 lane 一条 list + 自加读指针，未用 sram.py；两段式 update/commit）。单元测试 [commonbuf_test.py](../sim/eval/analyzer/sim_model/commonbuf_test.py)（6 例）|
| 标量 feed + 内部 skew | ✅ 同 CommonFIFO（`_prop` 逐拍右推，§6 同款；读尽后点亮出 0，同 fifo 空→0）|
| 读地址 {page,tile_id,row} | ✅ 线性自加指针表 `{tile_id,row}`，封顶 `tile_num*K` 归 0（§6）|
| 复用（同 page）| ✅ **自动**：指针到顶归 0 重读，无需外部触发（§7、§9）|
| ping-pong 切页 | 🔧 `switch_page()` 占位（翻页 + 读指针归 0）；**切页时机由指令解析驱动，后续接**（§7、§9）|
| OS / WS-1 影响 | OS 激活用 CommonFIFO（不涉本 buf）；**WS-1 e2e 激活已切到 CommonBuf**（默认 `tile_num=1`，全套测试通过）|

## 11. 关联文档与术语表

**关联文档**
- [weight_fifo_design.md](weight_fifo_design.md) — 喂料的标量 feed + 内部 skew 机制（buf 复用同款）；fifo vs buf 对照。
- [controller_design.md](controller_design.md) §7.3 — 数据来源：反压（驻留）vs 标量 feed + 源内部传播（流动），buf 属后者。
- [WS_weight_design.md](WS_weight_design.md) — WS 调度、激活复用（§0.2）、WS-3 N 切块（§9）。

**术语表**

| 术语 | 含义 |
|---|---|
| common buf / CommonBuf | 可寻址、可重读的缓冲；存流动且复用的操作数（WS 激活 / IS 权重）|
| 一整层 | 一个**网络层的全部激活**（当前 buf 容量 = 一层）|
| 读指针 | 每 lane 的读地址；被 feed 点亮时取数 + 推进，可 rewind 重读 |
| rewind | 读指针归 0，复用同一层喂下一个 N-tile（免重 DMA）|
| 复用 | 同一份激活跨 N-tile 多次读（buf 的核心价值）|
| skew | 阵列要的对角错位进料；由 buf 内部 lane 传播生成（同 fifo）|
