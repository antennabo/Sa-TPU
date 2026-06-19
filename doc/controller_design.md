# controller 设计（OS / WS 双状态机 · 把一次 tile 矩阵乘调度成逐拍信号）

本文档写给**第一次接触本项目**的读者，自顶向下：先讲 controller 要解决什么问题，再讲整体结构，
再讲对外接口，最后讲内部细节。读完应能看懂 [controller.py](../sim/eval/analyzer/sim_model/controller.py)。

阵列本身（PE 怎么算、怎么路由）见 [SA_array_design.md](SA_array_design.md)；本文只讲**谁来指挥那个阵列**。
两份文档配套：阵列是"手"，controller 是"大脑"。

---

# 第一部分 · 需求与目标

## 1. controller 要解决什么问题

阵列 [spatial_array](../sim/eval/analyzer/sim_model/spatial_array.py) 只会一件事：每拍读边缘注入的数据 + 控制信号，
按固定规则把每个 PE 的输入算好、做一次乘加。它**不知道**现在算的是哪个矩阵、算到第几步、什么时候该把结果读走。

把"一次 tile 矩阵乘 `C = A · B`"翻译成"阵列每一拍该收到的那一组信号"，就是 controller 的活：

```
        指令 / 数据就绪信号                每拍的控制信号
   ┌──────────────────────┐         ┌────────────────────────────┐
   │ 这是个 M×N×K 的 tile  │         │ 本拍 fifo 弹哪几行/列数据？ │
   │ weight/activ 备好了吗 │  ──▶    │ 本拍阵列该不该收激活？     │  ──▶  fifo / sa / accumulator
   │ 要不要换权重 / 新块   │ controller│ 本拍哪些 PE 该 drain/复位？ │
   └──────────────────────┘         │ 算完写进 accumulator 哪个槽 │
                                     └────────────────────────────┘
```

一句话：**controller 是阵列的指挥**——把一道矩阵乘指令展开成逐拍的 read / drain / switch / 写回信号，
让阵列、fifo、accumulator 三者步调一致。

## 2. 为什么需要"状态机 + 控制波"

阵列是 systolic 的：数据**斜着错开**喂进去、结果**斜着错开**流出来（细节见 SA 文档 §6、§11）。
这带来两个 controller 必须处理的事实：

1. **一次 tile 矩阵乘要分阶段**——先把数据喂满（feed），再把结果排空（drain）；多个 tile 还会首尾重叠。
   这种"现在处于哪个阶段、下一拍进哪个阶段"天然是一个**状态机**。

2. **同一个控制动作要点亮的不是一个 PE，而是阵列里斜着排开的一片**——例如"把算完的 psum 读出来"，
   阵列左上角的 PE 比右下角的早算完，读出动作要沿对角线一拍扫一格。controller 不可能每拍直接画出整片
   `[M][N]` 图案；它只在**左边缘每行注入一个 bool**，让这个 bool 在阵列内**逐拍向右推**，自己长成那片斜线
   （这套"注入 + 右推 = 斜线"的机制在 sa 里，见 SA 文档 §11）。controller 这一侧负责的，是**按正确的时刻、
   往正确的行注入**——也就是维护若干条**移位寄存器**，决定每拍左边缘那一列 bool 长什么样。本文把这种
   "左边缘逐行注入、在阵列内推成一片斜线"的控制信号统称为**控制波**。

所以 controller = **一个状态机**（管阶段）+ **几条移位寄存器**（管每拍往边缘注入什么）。

## 3. 设计目标与约束

| 目标 | 含义 |
|---|---|
| **模式无感的 pe/sa** | OS / WS / IS 的差异**全部**收在 controller 里（各自独立状态机 + 独立信号生成）。pe/sa 只认通用信号，不知道当前是哪种数据流。|
| **两段式时序** | 完全对应 RTL：组合逻辑只算次态与输出（写 `*_next`），`commit()` 才在"时钟沿"把 `reg = reg_next` 锁存。见 §5。|
| **counter 驱动、阈值固定** | 状态转移只看计数器到没到固定阈值（如 `cnt == K-1`），**不**看数据/读出图案反推。延迟全从一个根参数 `latency` 推导。|
| **tile 原子** | 一个 tile 是固定 `8×8`、一口气连续喂完，中途不打断、不驱逐（见 [tile-atomic-scale-by-tk]、[OS_restore_design.md](OS_restore_design.md)）。缩放靠**块数**而非把单 tile 做大。|

---

# 第二部分 · 整体设计

## 4. controller 在系统里的位置

controller 在数据通路的**最左边**，和四个模块交互（参 SA 文档 §4 的系统图）：

```
                ┌───────────────────────────────────────────────┐
                │                  controller                    │
                │   (OS FSM / WS FSM + 控制波移位寄存器)          │
                └───┬───────────┬───────────┬──────────────┬─────┘
       feed (1bit)  │           │           │              │  wr_tile / out_row
       (wb & ab)    │  b_data   │  a_data   │ output_sel   │  (写哪个槽 / 写哪行)
                    ▼  b_vld     ▼  a_vld    ▼ acc_clr/b_sw ▼
              ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌──────────────┐
              │ wb fifo │  │ ab fifo │  │    sa    │  │ accumulator  │
              │ (weight)│  │ (activ) │  │  array   │  │ (output mem) │
              └────┬────┘  └────┬────┘  └────┬─────┘  └──────────────┘
                   │ b_data     │ a_data     │ sa.output / sa.data[M-1]
                   └────────────┴───────────▶│────────────────────▶ accumulator
```

- **对 wb / ab（输入数据源）**：给**标量 `feed`**（这拍喂不喂）；**skew 由 fifo/buf 内部 lane 传播生成**（见 §7.3、
  [weight_fifo_design.md](weight_fifo_design.md) §9），controller 不再给 `[N]`/`[M]` 读 mask。`feed` 喂的是 OS 的 wb+ab、
  WS 的 ab buf；WS 权重弹出走 wb↔sa 的 valid/ready 自握手（controller 不门控，见 §7.2、§9.4）。取出的 `b_data` / `a_data` 喂进 sa 的边缘。
- **对 sa（阵列）**：给两类东西——①**激活的数据伴随信号** `a_vld[M]`（与 `feed` 同步，标这拍边缘进来的激活有没有效；
  权重侧的 `b_vld` 是 wb→sa、**非 controller 产**，见 §7.2）；②**控制波的左边缘注入** `acc_clr[M]` / `output_sel[M]` /
  `b_sw[M]`（OS 复位 / OS 读出 / WS 换权重）。
  **WS 下 sa 还回馈一组信号给 controller**：sa 对 wb 的**每列反压** `[N]`——某列 shadow 满（该列 ready 链顶
  = 0，`backpressure[c] = not b_rdy[0][c]`）就拉起该列反压、让 wb 这条 lane 停。这 `[N]` 条**全部拉起** = 整套
  shadow 灌满 = WS 的 `weight_available`（见 §7.1）。因为权重驻留在 sa 内的 shadow FIFO，"权重备好没"是 sa 持有的状态。
- **对 accumulator**：给**地址面**——`wr_tile`（写哪个槽，标量）；数据与行地址 `out_row` 由 sa 给（数据面）。

> 关键分工：**数据面**（结果值 + 行地址）走 sa，**地址面**（写哪个槽）走 controller。两者在 accumulator 汇合。
> 详见 [OS_restore_design.md](OS_restore_design.md) §5–6。

## 5. 核心设计原则

### ① 两段式时序（= RTL 的组合 + 时序）

controller 严格分两段，对应硬件的组合逻辑与时序逻辑：

| 段 | 方法 | 干什么 | 对应 RTL |
|---|---|---|---|
| **组合** | `update(...)` | 读当前状态 + 本拍输入，算出**次态** `*_state_next` / `*_cnt_next` 和**所有输出的 next 值**。只写 `*_next`，绝不碰已锁存的 `reg`。| 组合逻辑（`always @(*)`）|
| **时序** | `commit()` | 纯锁存：`reg = reg_next`，**无任何逻辑**。| 时钟沿（`always @(posedge clk)`）|

`update` 内部又分两小步（都属组合）：
1. **次态组合** `_next_*_state(...)`：只写 `_*_state_next` / `_*_cnt_next`。默认"保持现态、`cnt+1`"，再按 case 覆盖（避免 latch）。
2. **输出组合** `_*_feed/drain/...`：按**次态**（`*_state_next`，不是现态）算各输出的 `_*_next`。这样锁存后输出与新状态**同拍对齐**。

> 命名映射（与 [pe.py](../sim/eval/analyzer/sim_model/pe.py) / sa 一致）：`__init__` 入参 ↔ Verilog `parameter`；
> `update(...)` 入参 ↔ module 的输入端口；`reg`（已锁存）↔ 时序逻辑；`reg_next` ↔ 组合逻辑；`commit()` ↔ 时钟沿。

### ② 维度约定（最容易混的地方）

`self.M` / `self.N` **恒为物理阵列**的行数 / 列数；`self._K` **恒为时间维 feed 拍数**。
但"物理阵列的行列"映射到完整矩阵乘 `C = A·B`（A 是 `M×K`、B 是 `K×N`、C 是 `M×N`）的哪一维，**随模式不同**：

| 模式 | 阵列行 = | 阵列列 = | 时间维（`self._K`）= | 构造 |
|---|---|---|---|---|
| **OS** | 输出行 M | 输出列 N | 收缩维 K | `Controller(M, N, K, "OS")` |
| **WS** | 收缩段 K_tile | 输出列 N_tile | 输出行 M | `Controller(M=K_tile, N=N_tile, K=M, "WS")` |

> 收缩维 `Σ_k` 谁在空间做谁就占一维阵列：OS 把 K 放时间、阵列摆成输出 `M×N`；WS 把 K 放空间（权重驻留）、
> M 改走时间。这套映射的来龙去脉见 [WS_weight_design.md](WS_weight_design.md) §0。**本文为避免歧义，统一用
> `self.M / self.N / self._K` 这三个"物理 + 时间"符号叙述，需要时再点出它在该模式下对应完整矩阵乘的哪一维。**

### ③ 模式在 controller 显形、对 pe/sa 隐形

`update` / `commit` / `reset` 里用 `if self._mode == ...` 分支，OS / WS 各跑各自的状态机和信号生成，
**互不共享逻辑**（只共享标量 `feed` 输出字段）。pe/sa 收到的永远是通用信号。

### ④ 控制波 = 移位寄存器

§2 说的每条"控制波"在 controller 里就是**一条移位寄存器**：每拍往一端注入（注或不注），整体移一格，
延若干拍后从固定位置读出，喂给 sa 的左边缘。所有阶段性图案（feed 的头尾、drain 斜线、WS 换权重斜线）
都由这套移位 + 延迟读出天然产生。统一细节见 §10。

## 6. 一拍里 controller 做什么

以 OS 为例，`update(cycle, weight_available, activ_available, new_tile, tile_id, ...)` 一拍内顺序执行：

```
update():                                            # —— 组合段 ——
  1. 锁存本拍输入到内部（_new_tile / _new_tile_id …）
  2. _next_os_state(wA, aA)   → 算 _os_state_next / _cnt_next + 块边界事件
  3. _os_feed()               ┐
  4. _os_drain()              ├ 输出组合：按"次态"算 read/output_sel/acc_rst/wr_tile 的 *_next
  5. _os_restore()            ┘
commit():                                            # —— 时序段 ——
  os_state = _os_state_next; feed = _feed_next; …  (纯锁存)
```

WS 同构：`_next_ws_state` 后跟 `_ws_feed / _ws_switch / _ws_capture`（权重载入走 wb↔sa 自握手，无 controller 信号），`commit` 锁存 WS 那组寄存器。
驱动循环里 controller 的 `update` 在 sa / accum 之前、`commit` 和其它模块一起在拍尾。

---

# 第三部分 · 接口

## 7. controller 的对外接口

### 7.1 `update` 入参（= module 的输入端口）

```python
update(cycle, weight_available, activ_available,
       new_tile=False, tile_id=0,          # OS 专用
       switch_weight=False, tag=0)         # WS 专用
```

| 入参 | 类型 | 含义 | 用在 |
|---|---|---|---|
| `cycle` | int | 当前拍号（仅调试打印）| 全部 |
| `weight_available` | bool | 下一 tile 权重就绪。**OS：wb fifo avail；WS：sa 对 wb 的每列反压 `[N]` 全部拉起**（每列 shadow 满，权重驻留在 sa）| 全部 |
| `activ_available` | bool | 下一 tile 激活就绪（OS: ab fifo；WS: activation buf）| 全部 |
| `new_tile` | bool | 本 tile 是**新输出块**首段（→ 写新槽 + psum 清零）| OS |
| `tile_id` | int | 新输出块写进 accumulator 的槽号（指令 `accum_addr`）| OS |
| `switch_weight` | bool | tile 边界要不要换权重（注不注换权重控制波）。**`False`（同权重续喂）为预留**，标准 GEMM 每边界恒 `True` | WS |
| `tag` | int | 当前 tile 的目标**槽**号。**与 OS `tile_id` 同语义**（当前/新 tile 写哪槽），最终成为输出 `wr_tile`（§7.2）。消费的输出存储层待定 | WS |

> `weight_available` / `activ_available` 是"下一 tile 数据就绪没"的握手。来源分两路：**激活** OS 由 ab fifo avail、
> WS 由 activation buf 就绪给（§7.3）；**权重** OS 由 wb fifo avail 给，**WS 由 sa 回馈**——具体是 sa 对 wb 的**每列反压 `[N]` 全部拉起**
> （每列 shadow 满）。状态机据此决定 feed 完之后接着喂下一 tile（无缝）还是进 drain。WS 的 `WLOAD→STREAM`（§9.2）
> 即靠这组反压全拉起判定 shadow 灌满，而非 controller 自己数 `AR` 拍。

### 7.2 输出（= 已锁存的寄存器，`commit` 后稳定）

| 输出 | 形状 | 给谁 | 含义 |
|---|---|---|---|
| `feed` | bool **标量** | OS: wb+ab fifo / WS: ab buf | 这拍喂不喂。**skew 由下游 fifo/buf 内部 lane 传播生成**（§7.3）。WS 权重不用 feed（走反压，§7.2 注）|
| `output_sel` | bool [M] | sa（左边缘）| **OS**：drain 读出使能，点亮的 PE 把 psum 送出口 |
| `acc_rst` | bool [M] | sa（左边缘）| **OS**：drain 后把 PE 累加器复位为 0 |
| `wr_tile` | int（标量）| accumulator | 本拍写哪个 accumulator **槽**。|
| `m` | int（标量）| 输出存储层 | **WS**：本拍喂的输出**行**号；per-column 对齐由下游传播（OS 的行地址走 sa `out_row`，不用此信号）|
| `b_sw` | bool [M] | sa（左边缘）| **WS**：换权重使能（active ↔ shadow 翻转），见 §9、§12 |

> **WS 的权重载入不由 controller 门控**：权重走 wb↔sa 的 valid/ready **自握手**——`b_vld`（wb 有数据，
> 数据伴随信号、wb→sa，**非 controller 产**）与 sa 每列 ready 链 `b_rdy` 配对，`b_vld & b_rdy` 即弹出并载入
> shadow。所以 WS 的**权重侧既不靠 `feed`、controller 也不产 `b_vld`**；controller 对权重侧只**观测** sa 的
> `[N]` 反压全拉起 = `weight_available`（§7.1）。`feed` 在 OS 喂 wb+ab、在 WS 只喂 ab buf（激活），权重恒走反压。

> ⚠ 目标接口：WS 的 `b_sw` 是与 [SA_array_design.md](SA_array_design.md) 对齐的**目标设计**；
> 当前 [controller.py](../sim/eval/analyzer/sim_model/controller.py) 的 WS 仍输出旧的 `shadow_load` + `w_switch[M][N]`，
> 待改。见 §14。

### 7.3 数据来源：反压（驻留）vs 标量 feed + 源内部传播（流动）

权重 / 激活各自从哪取、怎么取，**随模式不同**。统一规律：

> **驻留**的操作数（要填进 PE shadow）走 **fifo + 反压**——一次性灌入、满则停（valid/ready 自握手），无 feed、无 skew。
> **流动**的操作数走 **标量 feed + 源内部 lane 传播**——controller 只给一个 `feed` 标量，fifo/buf 自己把它逐 lane 推出 skew
> （见 [weight_fifo_design.md](weight_fifo_design.md) §9）。跨 tile **反复读**的流动操作数还需 **buf**（可寻址重读，fifo 消费即弃）。

| 模式 | 权重 weight | 激活 activation |
|---|---|---|
| **OS** | wb **fifo** + 标量 `feed`（源内部传播 skew）| ab **fifo** + 标量 `feed`（源内部传播 skew）|
| **WS** | wb **fifo** + **反压**自握手（驻留，载 shadow；无 feed，§7.2）| **buf** + 标量 `feed`（源内部传播）+ 地址重读（跨 N-tile 复用）|
| **IS**（WS对称，未实现）| **buf** + 标量 `feed` + 地址重读 | **fifo** + **反压**自握手（驻留，载 shadow）|

> 一个 `feed` 标量喂所有"流动侧"的源，**skew 全在源内部生成**；驻留侧恒走反压（无 feed）。
> WS 与 IS **对称**：把"驻留 / 流动"角色在权重↔激活间对调（WS 驻留权重、IS 驻留激活）。

## 8. OS 模式：状态机 + 信号

### 8.1 五个状态

```
IDLE              无活跃 tile
COMPUTE           最老 tile 在 feed、drain 尚未开始
OVERLAP_SEAMLESS  最老 tile drain 与后继 tile feed 无缝重叠（背靠背）
OVERLAP_GAP       最老 tile drain 中途、后继 tile 迟到才接上（有间隔）
DRAIN             最老 tile 在排空、后面没有 tile 了
```

> `COMPUTE / OVERLAP_SEAMLESS / OVERLAP_GAP` 三态**行为完全相同**（都喂 `K` 拍激活），区别只在"从哪个入口进来"
> （首个 / 背靠背接上 / 迟到接上），代码里合成 `_OS_FEED_STATES` 统一处理。

### 8.2 转移（阈值固定，与读出图案无关）

记 `avail` = 下一 tile 的 weight & activ 都就绪（按模式只查需要的那一侧）：

```
IDLE   : avail                         → COMPUTE,          cnt=0
FEED*  : cnt == K-1 (feed 末拍):
           avail                       → OVERLAP_SEAMLESS, cnt=0   # 接着喂下一 tile
           ¬avail                      → DRAIN,            cnt=0   # 没有下一 tile，排空
         否则 cnt+1                                                # feed 中：cnt 0..K-1
DRAIN  : avail (迟到 tile 到达)         → OVERLAP_GAP,      cnt=0   # drain 中途有 tile 接上
         cnt == M+N-2                  → IDLE,             cnt=0   # 排空 M+N-1 拍完
         否则 cnt+1                                                # drain 中：cnt 0..M+N-2
(FEED* = COMPUTE / OVERLAP_SEAMLESS / OVERLAP_GAP)
```

- **feed 长 `K` 拍**：一个 tile 的全部 `K` 个收缩步逐拍喂完（`cnt` 数 `0..K-1`）。
- **drain 长 `M+N-1` 拍**：psum 原地、读出斜线扫完整阵列要 `M+N-1` 拍（`cnt` 数 `0..M+N-2`）。

### 8.3 块边界事件（驱动 drain / 写槽 / 清零）

次态算完后，由几个组合 bool 标出"本拍发生了什么块级事件"：

| 事件 | 条件 | 触发 |
|---|---|---|
| `_block_end` | feed 末拍且（无下一 tile 或下一是新块）| 旧块算完 → 启动 drain（往 drain 移位寄存器注入 `tile_id`）|
| `_block_start` | 新块的首段 feed 起拍 | 新输出块 → `tile_id` 切到新槽、psum 清零 |
| `_psum_init` | = `_block_start` | 往 restore 移位寄存器注入"新块 overwrite(=0)" |

> OS **不**支持"算一半 spill 到累加器、回来读回 PE 续算"——restore 恒给 `0`（全新累加）。
> 这是明确的设计决策，理由见 [OS_restore_design.md](OS_restore_design.md) §1–2。

### 8.4 三道控制波（各产出哪个输出信号）

OS 有三道控制波，各是一条移位寄存器（机制统一见 §10，本节只讲「目的 + 产出什么」）：

| 控制波 | 目的 | 何时起 | 产出信号 |
|---|---|---|---|
| **feed** | 告诉 fifo "这拍喂不喂"，让数据斜着进阵列（skew 由 fifo 内部传播，§7.3）| feed 态（FEED*）期间持续 | 标量 `feed`（喂 wb+ab fifo）|
| **drain** | 一个 tile 算完，把各 PE 的 psum 沿对角线逐行读出 | 旧块算完（`_block_end`）| `output_sel[M]`（每行这拍要不要读出，bool）、`wr_tile`（写哪个槽，标量）|
| **restore** | 读出后把那些 PE 的累加器清 0，好让下个 tile 从头算 | 新块首段起（`_psum_init`）| `acc_rst[M]`（每行这拍要不要清 0，bool）|

两个要点：
- **feed 自带 ramp**：头部逐行进入工作、尾部逐行退出、两 tile 重叠时的叠加，全由 **fifo 内部的 lane 传播**天然给出（§7.3、[weight_fifo_design.md](weight_fifo_design.md) §9），controller 只发标量 feed。
- **controller 只管「行向」**：三道波给 sa 的都是「左边缘每行一个 bool」（`drain` 另给标量槽号 `wr_tile`）。
  最终点亮的是阵列里斜着的一片，那个「列向 `+c`」的铺开是 **sa 右推**出来的（§10、SA §11），不是 controller 算的。

> 移位寄存器的长度、注入内容、`+D` 延迟读出等实现细节见 §11。

## 9. WS 模式：状态机 + 信号（目标设计）

> 本节按与 SA 文档对齐的**目标接口**（`b_sw` / `b_vld` / 反压 FIFO）写。controller.py 当前 WS 实现仍是旧接口
> （`shadow_load` / `w_switch[M][N]` / 沿列下移），差异集中列在 §14。WS 的数据流原理（ping-pong、换权重几何）
> 见 [WS_weight_design.md](WS_weight_design.md)。

### 9.1 五个状态

```
IDLE     无活跃 tile
WLOAD    冷启动：首 tile 权重逐拍灌进阵列（AR 拍，暴露、不可隐藏）
STREAM   连续喂 F 行激活；后台预载下一 tile 权重；换权重控制波在边界翻
STALL    预留：激活饥饿 / 权重没备好 → feed 与 switch 冻结、capture 继续（v1 不触发）
DRAIN    末 tile 排空剩余 psum（仅 capture），到最后一个 capture → IDLE
```

记 `AR = self.M`（阵列行 = K 段长）、`AC = self.N`（阵列列 = N 块宽）、`F = self._K`（feed 拍数 = M）。

### 9.2 转移

```
IDLE   : activ_available               → WLOAD,  cnt=0      # 有激活就启动；权重在 WLOAD 里载
WLOAD  : weight_available (sa 每列反压 [N] 全拉起) → STREAM, cnt=0   # shadow 灌满，由 sa 回馈判定
STREAM : cnt == F-1 (feed 末拍):
           avail                       → STREAM, cnt=0      # 背靠背喂下一 tile
           ¬avail                      → DRAIN,  cnt=0      # 末 tile
         (STALL 三条入边预留，v1 不触发)
DRAIN  : cnt == AR+AC+L-3              → IDLE,   cnt=0      # 排空到最后一个 capture
```

> **`IDLE→WLOAD` 只看 `activ_available`**（不看 weight）：进 WLOAD 才开始把权重载进 shadow，若用"shadow 满"
> 的 `weight_available` 当 IDLE 门控会死锁（空 shadow 永远进不去）。权重数据缺失则 WLOAD 停等 `weight_available`。
> **`WLOAD→STREAM` 用 sa 回馈的 shadow 就绪**（§4、§7.1）：sa 对 wb 的每列反压 `[N]` 全拉起即灌满。

### 9.3 换权重是【受控事件】

**先定义 K-chunk / N-tile**：物理阵列固定 `AR`(行) × `AC`(列)。完整矩阵乘的 K、N 可能比阵列大，就得切开分批算（M 走时间不用切）：
- **K-chunk** = 收缩维 K 切出的**一段**（每段 `AR` 深）。同一块输出的不同 K 段是 `Σ_k` 的不同部分 → 结果要**累加**到同一槽。
- **N-tile** = 输出列 N 切出的**一块**（每块 `AC` 宽）。不同块是**不同的输出列**、互相独立 → 各写**新槽**。

> 例（2×2 阵列算 4×4，详见 [WS_weight_design.md §0.2](WS_weight_design.md)）：`C[:,0:2] = A[:,0:2]·W00 + A[:,2:4]·W10`，
> 两个 K-chunk（W00→W10）累加进 slot0；换到列 2:4 是 N-tile（W10→W01），写 slot1。

WS 下每个 tile 用一套驻留权重，喂完 F 行切到下一个 tile 时通常要换一整套权重——即 `b_sw` 那道换权重控制波。
`switch_weight` 这个**输入**管「这个边界要不要换」（类比 OS 的 `new_tile`）：

| 边界 | switch_weight | controller 行为 | 输出存储（**不归 controller**）|
|---|---|---|---|
| 换 K-chunk | True | 注入 `b_sw` 换权重波 | 部分和**累加**到同槽（+=）|
| 换 N-tile | True | 注入 `b_sw` 换权重波 | 写**新槽**（覆盖）|
| 同权重续喂（**预留**，标准 GEMM 不触发）| False | 不注入、feed/capture 继续 | — |

> **换 K-chunk 和换 N-tile 对 controller 完全一样**（都注入 `b_sw`）；区别只在结果累加还是写新槽，那属输出存储层。
> controller 只管"切不切"。**首 tile（WLOAD→STREAM）恒注入一次**（首套权重 shadow→active 总要切，与 `switch_weight` 无关）。

### 9.4 三道控制波（controller 输出组合）+ 自握手的权重载入

controller 产出三道控制波；权重载入**不是** controller 的控制波，而是 wb↔sa 的 valid/ready 自握手（见下）。

| 控制波 | 几何 | 注入 / 内容 | 输出 |
|---|---|---|---|
| **feed**（标量）| — | `feed = (state_next == STREAM)` | 标量 `feed`；激活的行 skew 由 ab buf 内部传播（§7.3）|
| **switch**（`b_sw`）| 左边缘按行 skew → sa 右推成对角线 `k+n` | 首 tile 恒注入；之后边界 `switch_weight=True` 时注入；早 feed 1 拍 | PE(k,n) 在 `feed_start+k+n` 翻 active↔shadow |
| **capture**（标量注入）| — | STREAM 喂行 m：输出行 `m` + 槽 `wr_tile`（= 旧 `tag`），否则无效 | 两个标量 `m` / `wr_tile`；per-column 对齐（psum 下流 + 流水）由下游随 psum 传播，controller 不算 `[N]`（`wr_tile` 与 OS drain 槽同一语义，§14-D）|

**权重载入（自握手，非 controller 控制波）**：wb 出权重 + `b_vld`（有数据），sa 每列 ready 链给 `b_rdy`，
`b_vld & b_rdy` 即弹出、沉底堆进 shadow，满则该列反压拉起、自动停，保持到 `b_sw`。controller 权重侧无信号（`feed` 不喂权重、不产 `b_vld`），
只**观测** `[N]` 反压全拉起 = `weight_available`（§7.1、§9.2）。

> 目标设计为只在**左边缘按行注入 `b_sw[k]`**（行 k 延 k 拍），到 PE(k,n) 是 `t+k+n`——`+k` 来自注入 skew、
> `+c=+n` 来自 sa 右推，自然长成反对角线 `k+n`。于是 OS（`output_sel`/`acc_rst`）和 WS（`b_sw`）**用同一套
> "[M] 左边缘 + sa 右推"机制**，pe/sa 不再需要 per-PE mask 通道。

> **shadow 载入靠反压、不靠精确数拍、也不靠 controller 门控**：原设计 controller 出 `shadow_load`(bool) 精确开
> `AR` 拍窗口、且权重要倒序喂；目标设计里 shadow 是 sa 内的 valid/ready 反压 FIFO（sa 自算 ready 链、沉底堆叠、
> 满则停、保持到 `b_sw`）。**载入完全由 wb↔sa 握手自定时，controller 不参与**（`feed` 不喂权重、不产 `b_vld`）。细节见 SA 文档 §10 `_route_b`。

## 9.5 IS 模式（占位，未实现）

为三模式对称留位。IS（input-stationary）= 激活驻留、psum **左流**、收缩维 N 走时间（对照 SA §6）。
状态机思路与 WS 同构、**角色对调**：驻留的是激活（走 **fifo + 反压**载入 shadow），权重改走 **buf + 地址**（流动、反复读）——
正好是 WS 的镜像（§7.3）。但 controller 尚无 IS 分支，`update` 遇到 `mode="IS"` 直接 `raise NotImplementedError`。待 OS / WS 稳定后再补。

---

# 第四部分 · 内部细节

## 10. 控制波 = 移位寄存器（通用思路）

所有控制波（OS 的 feed/drain/restore、WS 的 feed/switch/shadow/capture）共用一套机制：

```
每拍：  sr_next = [本拍注入] + sr[:-1]          # 一端注入、整体移一格
读出：  输出 = sr_next[固定位置 + 延迟 D]        # 延 D 拍后从某格读出
锁存：  commit 时 sr = sr_next
```

- **注入**决定"这一拍要不要发起这个动作"（如 feed 中 / 块结束 / 换权重）。
- **移位**让动作沿时间自动铺开——配合**注入时刻按行错开**（skew），读出处就长成阵列里那条**斜线**。
- **延迟 `D`**补偿数据通路的流水：信号从 controller 出发，经 fifo、sa 输入寄存器、PE 流水（MAC latency）、
  到结果可读，差若干拍。控制波读出点要相应延后 `D` 拍才与数据对齐。

> 行向（`+r`）的错位由 controller 在移位寄存器里管；列向（`+c`）的传播**不**在 controller，而在 sa——
> controller 只给"左边缘每行一个 bool"，sa 每拍右推一格、自己长出 `+c`（SA 文档 §11）。这就是为什么
> drain / restore / switch 的移位寄存器都只需"行向 + 延迟"长度（`M + D`），不需要 `[M][N]`。

## 11. OS 三道控制波细节

### feed — `_os_feed`
```
feed = (state_next ∈ FEED*)        # 标量：这拍喂不喂（仅此一个输出）
```
controller 只发这个标量；**lane 传播（skew）移到 wb/ab fifo 内部**（最左 lane 注入、逐拍右推，见
[weight_fifo_design.md](weight_fifo_design.md) §9）。头部 ramp（阵列逐行进入工作）、drain 尾巴、两 tile 重叠时的叠加，
全由 fifo 那侧的 lane 传播自然给出。

### drain — `_os_drain`
```
inject               = tile_id if _block_end else None
_acc_sr_next         = [inject] + _acc_sr[:-1]
D = drain_delay
output_sel[r]        = (_acc_sr_next[r+D] is not None)  # 每行 drain 读出使能（bool）
head                 = _acc_sr_next[D]                  # row0 本拍起 drain 的块
wr_tile              = head if head is not None else 保持 # 标量目的槽
```
移位寄存器里搬的是 `tile_id`（用来算标量 `wr_tile`），但**只把"有没有"(bool) 给 sa**——`tile_id` 本身不进
sa 数据面。"一拍至多一条 drain 斜线"由带宽不变式保证（SA §12），故标量 `wr_tile` 够用。

### restore — `_os_restore`
```
_acc_read_sr_next    = [_psum_init] + _acc_read_sr[:-1]
D = restore_delay
acc_rst[r]           = _acc_read_sr_next[r+D]           # 每行清零使能（bool）
```
新块首段起，沿对角线把被 drain 读走的 PE 就地复位为 0，好让下一个块从头累加。

## 12. WS 三道控制波细节（目标设计）

controller 产 feed / switch / capture 三道；权重载入是 wb↔sa 自握手，controller 不产信号（§9.4）。

```
feed     : feed = (state_next == STREAM)        # 标量；激活 skew 由 ab buf 内部传播（§7.3）

switch   : inject = (首 tile WLOAD→STREAM) or (STREAM 边界 & switch_weight)
           _ws_switch_sr_next = [inject] + _ws_switch_sr[:-1]
           b_sw[k]            = _ws_switch_sr_next[k + D_sw]      # 左边缘按行 skew，sa 右推成 k+n

capture  : if state_next==STREAM:  m = 当前喂的输出行 ;  wr_tile = tag   # 两个标量
           # wr_tile（槽）与 OS drain 同一语义；per-column 对齐（psum 下流 AR 行 + 流水 cap_delay）
           # 由下游随 psum 传播，controller 不算 [N]

# 权重载入：无 controller 信号——wb 出 b_data+b_vld，sa 每列 ready 链给 b_rdy，
#           b_vld & b_rdy 即弹出沉底进 shadow，满则该列反压拉起、停，保持到 b_sw。
#           controller 只观测 [N] 反压全拉起 = weight_available。
```

- **switch** 不再是 `[AR][AC]` 直给的 per-PE mask，而是 `[M]` 左边缘注入 + sa 右推（§9.4）。
- **权重载入** 不再由 controller 开 `shadow_load` 窗口，改为 wb↔sa 的 valid/ready 自握手（sa 反压 FIFO 自己沉底、满则停）。
- **capture** 拆成 `m`（输出行）+ `wr_tile`（槽，= 旧 `tag`）两个标量：`wr_tile` 与 OS drain 槽**同一语义**，
  二者都标量注入、由下游随 psum 传播成 per-column。背靠背时一个 tile 的尾与下个 tile 的头会在同拍、不同列出现，
  列与列可能属不同槽——正因下游 per-column 传播，每列各带各的 `(m, wr_tile)`，不会写错（[WS_weight_design.md](WS_weight_design.md) §7、§A）。

## 13. 延迟参数怎么从 latency 推导

PE 流水延迟 `L`（`latency`，1 或 2）是**唯一的根参数**，三个延迟都从它推：

| 延迟 | 值 | 补偿什么 |
|---|---|---|
| `drain_delay` | `L + 1` | OS drain 读出：PE MAC 流水 + 输出寄存，drain 斜线延后读 |
| `restore_delay` | `L + 1` | OS 清零：同 drain，复位斜线与 drain 同步延后 |
| `cap_delay` | `(AR-1) + L + 1` | WS capture 的 per-column 对齐：psum 下流 `AR` 行 + PE 流水 + 寄存。**拆分后此延迟在下游传播层（随 psum），不在 controller**（controller 只标量注入 `m` / `wr_tile`）|

> 阈值（`K-1` / `M+N-2` / `AR+AC+L-3`）是**精确拍数**，靠状态机数 counter 给定；但控制波相对数据波的
> **精确偏移**（上面这几个 delay、switch 早 1 拍）不靠绝对拍号硬算，而是**用单元测试钉死**——和 §3 的
> counter-based 风格一致。`drain_delay = L+1` 已在 OS 端到端测试中验证，独立于 `M/N/K`。

---

# 附录

## 14. 现状与待梳理清单

### A. 已实现并验证
- **OS 状态机 + 三道控制波**：[controller.py](../sim/eval/analyzer/sim_model/controller.py) 的 `_next_os_state` /
  `_os_feed` / `_os_drain` / `_os_restore`。端到端 `accum == A·B` 已验证（单 tile / 单 K-段，latency 1&2，多形状含 8×8）。
- **WS 状态机 + 三道控制波**：`_next_ws_state` / `_ws_feed` / `_ws_switch` / `_ws_capture`，信号已对齐本文目标接口
  （`b_sw[M]` / 无 `shadow_load` / `WLOAD→STREAM` 由 `weight_available` / capture 拆标量 `m`+`wr_tile`）。
  **信号结构**经 [controller_ws_test.py](../sim/eval/analyzer/sim_model/controller_ws_test.py) 单测。
- **WS-1 单 tile 端到端 `accum == A·B`**：✅ **已验证**——controller + ab fifo + sa + accum 串通跑通，8 形状 × latency 1&2
  （[ws1_e2e_test.py](../sim/eval/analyzer/sim_model/ws1_e2e_test.py)）。`cap_delay = (AR-1)+L+2` 已钉死、capture 下游传播在 accumulator 落实。

### B. WS 端到端剩余（多 tile）
WS-1 单 tile **真实闭环已通**（`weight_available` 由 `sa.shadow_full` 回馈、WLOAD 期间反压填 shadow、cold `b_sw` 翻进 active）。剩下的是扩到多 tile：
| 待做 | 说明 |
|---|---|
| ✅ `weight_available` 真实闭环 | 已实现：sa 暴露 `shadow_full[N]`（每列反压），驱动 `all → weight_available`（[ws1_e2e_test.py](../sim/eval/analyzer/sim_model/ws1_e2e_test.py)）|
| WS-2/3/4 多 tile | K 切段累加（accum `add`）、N 切块新槽、activation buf 复用（[common_buf_design.md](common_buf_design.md)）|
| `b_sw` 早 1 拍校准 | WS-1 cold swap 通过；多 tile 背靠背换权重的精确偏移待跑通时钉死 |

### C. OS 出口尚未接通
- `output_sel` / `acc_rst` 控制波 controller 侧已产出，但 sa 侧的 mux 选读出口（`sa.output` / `out_row`）
  尚未按 [OS_restore_design.md](OS_restore_design.md) §5 接通（半迁移，SA 文档 §14）。

### D. 文档间待统一的冲突 + OS/WS 输出地址的统一
- **`Gk` 不变式**：[OS_restore_design.md](OS_restore_design.md) §6 论证"标量 `wr_tile` + `Gk ≥ M+N-1`"；
  SA 文档 §12 改为"槽地址逐拍推移（per-column）、只需 `Gk ≥ M`"。需统一，三处（本文 §11、SA §12、OS_restore §6）。
- **OS/WS 输出地址同构（采纳方向）**：槽地址在两模式**同一语义**——输入 OS `tile_id` ≡ WS `tag`，输出统一为 `wr_tile`。
  机制统一为**「标量注入 + 下游随 psum per-column 传播」**：OS drain 与 WS capture 都只注入标量 `wr_tile`（WS 另加 `m`），
  per-column 由下游 accumulator 传播。如此 `Gk ≥ M+N-1` 的"标量广播"假设自然消除（与上一条同向）——这是建议的终态。

### E. 未实现
- **IS 模式**（§9.5）：无 controller 分支，`raise NotImplementedError`。
- **STALL**（WS）：状态保留，v1 三条入边不触发（`assert avail 恒真 & F≥AR & shadow 恒就绪`）。
- **多 K-段累加带 gap**（OS）、**WS-5b gap / WS-6 `F<AR`**：见 [WS_weight_design.md](WS_weight_design.md) §9。

## 15. 关联文档与术语表

**关联文档**
- [SA_array_design.md](SA_array_design.md) — 阵列（pe/sa）设计；controller 给的信号在那里怎么被路由、控制波怎么右推成斜线。
- [OS_restore_design.md](OS_restore_design.md) — OS 的 restore/drain 决策、出口 mux、`Gk` 不变式。
- [WS_weight_design.md](WS_weight_design.md) — WS 的 ping-pong 权重、换权重几何、维度映射、案例清单。
- [tile-atomic-scale-by-tk] — tile 原子 8×8、缩放靠块数（§3 约束的来源）。

**术语表**

| 术语 | 含义 |
|---|---|
| `M, N, K` | 完整矩阵乘尺寸：A 是 `M×K`、B 是 `K×N`、C 是 `M×N` |
| `self.M / self.N` | **物理阵列**行 / 列（恒定，模式无关）|
| `self._K` | **时间维 feed 拍数**（OS = 收缩 K；WS = 输出行 M）|
| `AR / AC / F` | WS 别名：`AR=self.M`（K 段长）、`AC=self.N`（N 块宽）、`F=self._K`（=M）|
| `L`（latency）| PE 流水延迟（1 或 2），三个 delay 的根参数 |
| **控制波** | controller 在左边缘逐行注入、在 sa 内逐拍右推成斜线的控制信号（feed/drain/restore/switch …）|
| `cnt` | 当前阶段内的计数器，状态转移只看它到没到固定阈值 |
| `avail` | 下一 tile 的 weight & activ 是否都就绪 |
| `Gk` | OS 相邻两输出块开始 drain 的间隔拍数（带宽不变式，SA §12）|
| `tag` | WS capture 携带的 tile 标识，输出存储层据此寻址（背靠背区分列归属）|
