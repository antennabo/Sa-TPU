# weight fifo 设计（CommonFIFO：按 lane 缓存权重 tile，主动推给阵列边缘）

本文档写给**第一次接触本项目**的读者，自顶向下：先讲 weight fifo 要解决什么，再讲整体结构、对外接口、内部细节。
读完应能看懂 [commonfifo.py](../sim/eval/analyzer/sim_model/commonfifo.py)。

它和 [controller_design.md](controller_design.md) / [SA_array_design.md](SA_array_design.md) 配套：controller 是大脑、阵列是手，
**weight fifo 是喂到阵列嘴边的料斗**——把 DMA 搬来的权重 tile 缓存住，按节奏推进阵列。

> 同一个 `CommonFIFO` 类在 OS 下既当 weight fifo（wb）又当 activation fifo（ab）；本文以**权重**为主线讲，
> activation 的差异（WS 下改用可寻址 buf）见 [activation_buf 设计]（待写）。

---

# 第一部分 · 需求与目标

## 1. weight fifo 要解决什么

阵列每拍要从上边缘吃进一行权重，但权重来自 DMA、到得不齐、且一个 tile 有好几拍深。weight fifo 夹在中间：

```
   DMA 搬运            weight fifo           阵列上边缘
  (一拍一条) ──写─▶  [缓存整个 tile]  ──推─▶  (每拍一行权重)
```

- **缓存**：把一个完整权重 tile（`K` 条深度向量）攒住，等阵列要的时候再吐。
- **按节奏推**：阵列怎么吃随模式不同——**OS 斜着错开吃**（systolic，相邻列差 1 拍），**WS 一行一行吃**（整行 `N` 列一起灌进 shadow，不错位）。weight fifo 负责按正确节奏把数据推出去（skew / 反压细节见 §6）。

## 2. 核心思想：controller 只给标量，skew / 反压在 fifo 这一侧

本项目一条贯穿的设计原则（对照 controller 的 capture→accumulator、控制波→sa）：
**controller 只发标量，"铺开成多路"的活交给下游模块。** weight fifo 这一侧就承接两件这样的活：

| 模式 | controller 给什么 | fifo 这侧做什么 |
|---|---|---|
| **OS** | 一个标量 feed（"这拍喂不喂"）| 把它从**最左 lane 逐拍推到最右 lane**，每个 lane 延一拍 → **skew 在 fifo 内部长出来** |
| **WS** | （权重侧不给信号）| 和 sa 做 **valid/ready 反压**：fifo 有数据 & sa 那列 ready → 才推一拍 |

> 一句话：**OS 用"推"（带内部 skew、无反压），WS 用"推 + 反压"（valid/ready，无 skew）**。两者都是 fifo 主动把料推向阵列，
> 区别只在"要不要等下游 ready"和"skew 谁生成"。

---

# 第二部分 · 整体设计

## 3. weight fifo 在系统里的位置

```
        ┌─────────────┐
        │     DMA      │  一拍写一条深度向量 wdata[N]
        └──────┬──────┘
               │ write
               ▼
   ┌───────────────────────┐     controller: 标量 feed (OS)
   │      weight fifo       │ ◀── 或 sa: 每列 ready (WS)
   │  lane0 lane1 ... laneN │
   │   每 lane 一个 FIFO     │ ──push──▶  阵列上边缘 b_data[N] (+ b_vld)
   └───────────────────────┘
               │ avail
               ▼  (哪个 tile 备好了 / 开喂没)  →  controller
```

- **写口（DMA → fifo）**：DMA 把权重写进来，攒满 `K` 条**深度向量**（每条 = tile 的一"层" `[N]`）= 一个完整 tile。
  **写入节奏（一拍写几条）待定**——取决于 DMA 设计（低优先级，PIO v1），不影响下面的缓存 / tile 边界语义。
- **读口（fifo → 阵列）**：把缓存的权重推到阵列上边缘 `b_data[N]`。OS 由 controller 标量触发内部 skew 推进；WS 由 sa 的每列 ready 反压驱动。
- **握手（fifo → controller）**：**仅 OS** —— `avail` 告诉 controller"有没有一个攒满且还没开喂的 tile"，让状态机决定开不开新 tile。
  WS 不走这条：weight 就绪由 sa shadow 反压回报（§6）。

## 4. 两段式 + tile 边界握手

和全项目一致，fifo 也是**两段式**：`update(...)` 只算 `*_next`（组合），`commit()` 才落定（时钟沿）。

tile 边界靠三个计数联动（§8 细讲）：
- `_loaded`：已写满的完整 tile 数（DMA 写够 `K` 条 +1）。
- `_started`：已开始喂的 tile 数（某 tile 的第一条被读出时 +1）。
- `avail = _loaded > _started`：存在"攒满但还没开喂"的 tile —— controller 据此判断下一 tile 数据就绪。

> `avail` **仅 OS 用**。WS 下 weight 就绪由 sa 的 shadow 反压回报（每列反压全拉起 = `weight_available`，见 §6、
> [controller_design.md](controller_design.md) §7.1），controller 不查 wb 的 `avail`；这套 tile 边界跟踪也只为 OS 的 `avail` 服务。

---

# 第三部分 · 接口

## 5. 写口（DMA 写入）

```
update(wdata, ...):
  wdata[N]  -- 本拍 DMA 写入的一条深度向量（None/False = 这拍不写）
```
- 连写 `K` 条深度向量攒成一个 tile，`_loaded += 1`。
- > ⚠ **写入节奏待定**：当前模型按"一拍最多一条"写（取决于 DMA 设计，低优先级、PIO v1）；
  > 真实 DMA 可能一拍搬多条或带间隔。这只改"多快写满一个 tile"，不改缓存 / 读出 / `avail` 语义。
- 每个 tile 的第一条会被标记为 tile-head（§8），用来界定 tile 边界。

## 6. 读口：OS 推（内部 skew）vs WS 推 + 反压

**OS —— 标量 feed → 内部 lane 传播出 skew**
- controller 给**一个标量** `feed`（这拍在不在喂）。
- fifo 内部一条 **lane 方向的传播寄存器**：`feed` 注入最左 lane，每拍向右挪一个 lane。lane `c` 在 `feed` 之后第 `c` 拍才被点亮 → 弹出队头。
- 于是各 lane 弹出时刻错 1 拍，**skew 由 fifo 内部生成**，推到上边缘的 `b_data[N]` 天然带阵列要的对角错位。
- 无反压（OS 阵列恒收）。

**WS —— sa 每列 ready → valid/ready 反压**
- 权重要载进 sa 的 shadow，sa 那侧是个反压 FIFO（每列一条 ready 链，见 SA 文档 §10）。
- weight fifo 出**有数据**（valid），sa 出**每列 ready**；`valid & ready[c]` 那列才推一拍。某列 shadow 满 → ready 拉低 → 那列停。
- **无 skew**：WS 权重是整行灌进 shadow、由 sa 的 ready 链沉底，不需要 fifo 做 lane skew。
- controller **不参与**权重推（§controller_design §7.2/§9.4）；只观测 sa 的每列反压全拉起 = `weight_available`。

## 7. 输出

| 输出 | 形状 | 含义 |
|---|---|---|
| `data` | [N] | 本拍各 lane 推出的权重（被点亮=队头，未点亮=0），`commit` 后有效 |
| `avail` | bool | **仅 OS**：是否还有"攒满且未开喂"的 tile（给 controller 判就绪）。WS 的 weight 就绪走 sa shadow 反压（§6），不用 avail |

---

# 第四部分 · 内部细节

## 8. 按 lane FIFO + tile 边界跟踪

- **N 个独立 FIFO**（每 lane 一个），各存该 lane 在整个 tile 里的那一列深度数据。为什么按 lane：阵列上边缘 `N` 列各取各的，
  按 lane 切开最直接；深度（`K`）方向在每个 lane 的 FIFO 里排队。
- **DMA 写**：`wdata[N]` 一拍 push 进 `N` 个 lane 各一个；`_wcount` 数到 `K` → 一个 tile 满，`_loaded += 1`、`_wcount` 归零。
- **tile-head 跟踪（仅 OS）**：`_heads` 是一条与 lane0 队列平行的标记队列，每个 tile 的首条标 `True`。当 lane0 弹出的恰是一个 tile-head →
  这个 tile **开始喂**，`_started += 1`。`avail = _loaded > _started` 即"有攒满但没开喂的 tile"。WS 不用这套（就绪走 sa 反压，§6）。

## 9. OS 的 lane 传播（skew 的来源）

OS 读口的核心：一条 **lane 方向的 1-bit 传播寄存器**（长 `N`），与 §controller 的控制波同构，只是这条在 fifo 里、沿 **lane** 推：

```
每拍：  prop_next = [controller 给的标量 feed] + prop[:-1]   # 最左 lane 注入、整体右移
lane c 弹出 = prop_next[c]                                    # lane c 延 c 拍被点亮
data[c]    = prop_next[c] ? lane_c.pop() : 0
```

- 最左 lane 当拍点亮、最右 lane 延 `N-1` 拍——这就是阵列上边缘要的对角进料。
- 头部 ramp（逐列进入）、尾部收束，全由这条传播天然给出，不用特判。

## 10. 读出两段式

- `update` 里算 `data_next`（被点亮的 lane 取队头、其余 0）并暂存要 pop 的 lane。
- `commit` 真正 pop 那些 lane、推进 tile-head、落定 `data = data_next` / `_started`。

---

# 附录

## 11. 现状与待做

| 项 | 状态 |
|---|---|
| OS 标量 feed + 内部 lane 传播 | ✅ **已实现**：[commonfifo.py](../sim/eval/analyzer/sim_model/commonfifo.py) `update(wdata, feed)` + `_prop`（§9）；controller 出标量 `feed`（[controller.py](../sim/eval/analyzer/sim_model/controller.py) `_os_feed`/`_ws_feed`）。单测 [commonfifo_test.py](../sim/eval/analyzer/sim_model/commonfifo_test.py) 校验 skew |
| WS 反压口 | ⏳ **待做**：`valid & sa 每列 ready` 推（§6 WS），需 sa 暴露 ready + 驱动层接线 |
| OS 端到端 | ⏳ **待做**：[cycle_analyzer.py](../sim/eval/analyzer/cycle_analyzer.py) 驱动循环 stale（旧 sa 接口），需整体迁到新 sa/fifo 接口后跑 `accum == A·B` |

> 已落地的是统一原则的前半：①"controller 给 mask"→"controller 给标量 + fifo 内部传播"。剩 ②WS valid/ready 反压口（接 sa）。

## 12. 关联文档与术语表

**关联文档**
- [controller_design.md](controller_design.md) — 谁给 fifo 标量 feed / 怎么观测反压成 `weight_available`。
- [SA_array_design.md](SA_array_design.md) — 阵列上边缘怎么收 `b_data`、WS shadow 反压 FIFO（§10 `_route_b`）。
- activation buf 设计（待写）— WS 下激活改用可寻址 buf（复用），与本 fifo 对照。

**术语表**

| 术语 | 含义 |
|---|---|
| lane | fifo 的一路，对应阵列上边缘的一列（weight）/ 一行（activation）|
| 深度向量 | DMA 一拍写入的一"层"`[N]`；攒 `K` 条 = 一个 tile |
| tile-head | 每个 tile 的首条深度向量标记，用来界定 tile 边界（→ `_started` / `avail`）|
| skew | 阵列要的对角错位进料；OS 下由 fifo 内部 lane 传播生成 |
| `avail` | `_loaded > _started`：有攒满但未开喂的 tile（**仅 OS** 给 controller；WS 就绪走 sa 反压）|
| push（推）| fifo 主动把料推向阵列；OS=带内部 skew、无反压，WS=valid/ready 反压 |
