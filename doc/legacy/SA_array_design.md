> ⚠ **DEPRECATED** — 早期 OS/WS/IS 三模式统一设计稿。当前主线已切到 WS-only 落地，
> 本文 OS / IS 部分不再维护，WS 部分被 [../systolic_array.md](../systolic_array.md) 吸收并对齐 RTL。
> 字段命名 / 接口几何与现 RTL 不一致（如 `output_sel`、`acc_clr`、`Gk ≥ M+N-1`、`is_b_buf`）。
> 保留为历史参考；新工作请读 doc/ 根下的新版。

---

# spatial_array 阵列设计（OS/WS/IS 三模式统一路由）

本文档是 Sa-TPU golden model 里 **脉动阵列 `spatial_array`（简称 sa）** 的总设计说明。
面向**完全没接触过本项目**的读者：从"要解决什么问题"讲起，到整体结构，再到对外接口，最后才是内部实现细节。

> 配套文档（细节下钻入口）：
> - [OS_restore_design.md](OS_restore_design.md) — OS 数据流的累加 / drain / restore 细节
> - [WS_weight_design.md](WS_weight_design.md) — WS 数据流的权重双缓冲 / 权重切换细节
>
> 阅读顺序：先读完本文建立全局，再按需跳到上面两份看某种数据流的细节。

---

# 第一部分 · 需求与目标

## 1. 这个阵列要解决什么问题

TPU 这类加速器的核心计算是**矩阵乘** `C = A · B`：

```
A 是 M×K，B 是 K×N，C 是 M×N
C[m][n] = Σ_k  A[m][k] · B[k][n]      （对收缩维 k 求和）
```

脉动阵列（systolic array）是承载该计算的高效硬件结构之一：一张 PE（处理单元）网格，
操作数**逐拍从阵列边缘注入、流经各 PE**，每个 PE 每拍完成一次**乘加（multiply-accumulate, MAC）**。
操作数进入阵列后由相邻 PE 逐级复用，无需反复访存，这是其高效的来源。

```
              b_data（B，从上边流入）
               │   │   │
               ▼   ▼   ▼
  a_data ────▶ PE  PE  PE
（A，从左边）  │   │   │
               ▼   ▼   ▼
               PE  PE  PE     每个 PE：prod = a·b，累加进 psum
               │   │   │      操作数每拍向相邻 PE 移动一格（脉动）
               ▼   ▼   ▼
             结果从某条边流出
```

`spatial_array` 即该 PE 网格的 golden model（行为级、cycle 可对照的参考实现）。

## 2. 为什么一个阵列要支持多种数据流（OS / WS / IS）

矩阵乘有三维 `M × N × K`，但物理阵列只有两维（行 × 列）。**收缩维 `k` 的那个求和 `Σ_k` 必须有个去处**：
要么在**空间**里做（占掉阵列一维），要么在**时间**里做（同一组 PE 跨多拍累加）。谁来占空间、谁走时间，
就产生了三种数据流：

| 数据流  | 全称              | 驻留（被复用）的操作数          | 适合场景            |
|--------|-------------------|--------------------------------|---------------------|
| **OS** | output-stationary | 输出 psum 原地累加于 PE         | K 较深、减少 psum 搬运 |
| **WS** | weight-stationary | 权重 B 驻留于 PE               | 权重复用率高（如多 batch 共享权重），经典 TPU |
| **IS** | input-stationary  | 激活 A 驻留于 PE               | 激活复用率高           |

同一块物理阵列，改变驻留对象即切换数据流。本项目要求**一份 `spatial_array` 实现同时覆盖三种数据流**，
而非为每种各实现一套阵列——这是本设计的核心诉求，也是后文"模式无感路由"的由来。

> 三种数据流各自的维度映射在 [§6](#6-三种数据流的全景对照) 有一张全景表，建议看完整体再回头细读。

## 3. 设计目标与约束

**目标**
- **golden model 定位**：行为 / cycle 时序可对照，作为将来 RTL 实现的黄金参考。
- **一套阵列、多种数据流**：OS / WS / IS 共用同一份 `spatial_array`，靠配置切换。
- **模式无感**：阵列本身不认识"OS/WS/IS"这些名字，只认通用信号；模式语义全部下放给上层 controller。

**硬约束（贯穿全文，多处设计由其导出）**
- **出口带宽 = 阵列一条边**：阵列内部有 M×N 个 PE，但物理输出口只有一条边（底边 N 根线 / 右边 M 根线）。
  **每拍最多输出 N（或 M）个结果**，无法在一拍内读出全部 PE。
- **tile 原子化、缩放靠块数**：一个计算 tile 固定为小尺寸（如 8×8），更大的矩阵切分为多个 tile 依次喂入，
  而非扩大阵列。tile 一旦开始计算即连续喂完，中途不打断（见 [OS_restore_design.md](OS_restore_design.md)）。

**明确不做的（边界）**
- 不支持"计算中途被驱逐、部分和先存累加器、返回后再读回 PE 续算"（gap + 驱逐续算）——
  细节与理由见 [OS_restore_design.md](OS_restore_design.md) §2。

---

# 第二部分 · 整体设计

## 4. sa 在系统里的位置

`spatial_array` 与三个模块协同工作：

左侧 controller 是**控制面**，统一驱动其余四个模块；右侧是**数据面**（实线 = 数据，虚线 = 控制）：

```
  ┌────────────┐                          ┌────────┐
  │            │ ┄ rd ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄▶ │ FIFO_B │
  │            │                          └───┬────┘
  │            │                    b_data (B)│
  │ controller │ ┄ rd ┄┐                      ▼
  │            │       ▼               ┌──────────────┐
  │            │  ┌────────┐           │ spatial_array│
  │            │  │ FIFO_A │──data───▶│     (sa)     │
  │            │  └────────┘   (A)     │              │
  │            │ ┄ ctrl ┄┄┄┄┄┄┄┄┄┄┄┄┄▶│              │
  │            │                       └──────┬───────┘
  │            │ ┄ addr ┄┐            result  │
  │            │         ▼                    ▼
  └────────────┘     ┌──────────────────────────┐
                     │        accumulator       │
                     └──────────────────────────┘
```

> legend: `rd` = read timing · `ctrl` = control signals · `addr` = row + slot address ·
> `data` = a_data (A, from left) / b_data (B, from top) · `result` = result data

数据面 / 地址面**分离**是关键：sa 是**纯数据面**，只输出结果数据；**全部地址（行地址 + 槽地址 `wr_tile`）由
controller 出**，直送 accumulator。两者在 accumulator 汇合后写入 `mem[wr_tile][行][列]`。

- **controller**：唯一持有模式语义的模块，决定运行 OS 还是 WS。它统一驱动其余模块——给两路 FIFO 发**读时序**、
  给 sa 发**控制信号**的注入时序、给 accumulator 直送**行地址 + 槽地址**（全部地址面）。
- **FIFO**：两路独立缓冲——FIFO_A 从**左边**逐拍喂 `a_data`（A），FIFO_B 从**上边**逐拍喂 `b_data`（B）。
- **sa**：本文对象。接收两边的数据与 controller 的控制信号，在内部完成路由、驱动各 PE，输出**结果数据**（地址由 controller 提供）。
- **accumulator**：汇合 sa 的结果数据与 controller 的行/槽地址，写入结果存储（跨 tile 累加亦在此层）。

## 5. 核心设计原则

整份设计建立在四条原则上：

**① 模式无感：sa 不持有 OS/WS/IS 语义**
sa 仅识别一组**通用信号**（如"数据是否右移""psum 是否下流"），不感知具体数据流类型。
运行哪种数据流，完全由 controller 对这些信号的配置决定。由此三种模式共用同一份阵列实现，无模式分支。

**② 两条正交的轴：静态拓扑与逐拍动态**
sa 的输入分为两类：

| | 静态拓扑 flag | 逐拍动态信号 |
|---|---|---|
| 来源 | 构造函数（`__init__`）| 每拍的 `update(...)` |
| 变化频率 | **整个 run 不变** | **每拍可变** |
| 表达 | **选定数据流**（=模式）| 本拍的数据与控制信号注入 |
| 示例 | `is_shift_row/col/acc_d/acc_l` | `a_data / b_data / w_switch / ...` |

**静态 flag 选定模式，动态信号驱动每一拍。**

**③ 两段式时序**
sa 作为硬件行为模型，严格采用两段式：
- `update(...)`：仅**计算**下一拍状态（写各 `_next`），不改当前状态；
- `commit()`：将全部 `_next` 一次性**落定**为当前状态。
如此一拍内所有 PE 读到的均为上一拍的快照，与真实寄存器的并行更新一致。

这套两段式与 Verilog 一一对应，读模型时按此映射回 RTL：

| Python（golden model）| Verilog 对应 |
|---|---|
| `__init__` 的配置（`M / N / latency / is_shift_*`）| `parameter`（elaboration 静态参数）|
| `update(...)` 的形参（`a_data / b_data / w_switch / ...`）| **module 接口**（每拍驱动的 IO 端口）|
| `*_next`（`update` 里算出）| **组合逻辑**（next-state 函数）|
| 已 `commit` 的 `state` / 寄存器（`a / b / b_buf / state`）| **时序逻辑**（触发器，时钟沿 = `commit()` 更新）|

**④ 每拍的路由流程**
每拍 `update` 执行如下流程：

```
取上一拍快照（各 PE 的 a / b / psum）  +  本拍边缘注入（a_data / b_data / 控制信号）
        │
        ▼  依静态 flag 决定路由方式
为每个 PE 计算其本拍的 4 路输入：a_in、b_in、shadow、acc_in
        │
        ▼
逐个调用 pe.update(...)，由各 PE 计算自身 _next
```

sa 本身**不执行乘加**（由 PE 完成），仅负责**将数据路由至对应 PE**。路由规则由静态 flag 选定，
被路由的数据与控制由动态信号提供。

## 6. 三种数据流的全景对照

下表是全文的坐标系，后续接口与细节均回挂至此。

矩阵乘是**三维** `M×N×K`，物理阵列只有**两根轴**（行、列）。三维里只有两维能摊到阵列的行 / 列上
（即**空间**展开），剩下的第三维塞不进阵列，只能**一拍处理一片、跨多拍迭代走完**——这剩下的一维就是
**时间维**。选哪两维进空间，就决定了是哪种数据流，也就决定了剩哪一维走时间（§2 提到的"`Σ_k` 在空间或
时间"只针对 K；而时间维**不一定是 K**：OS 是 K、WS 是 M、IS 是 N）。三种数据流都落在同一块物理阵列上：

|        | 阵列两维（空间）        | 时间维 | 谁驻留 | psum 走向 | 结果从哪条边出 |
|--------|------------------------|---|---|---|---|
| **OS** | 输出 `M×N`             | `K`（收缩）| psum（输出原地）| 不动，**原地累加** | 每列就地选读（mux）|
| **WS** | `K×N`（权重 `B[k][n]`）| `M`（输出行流入）| 权重 `B` | 沿列**下流**，逐行加一项 | **底边**（最后一行）|
| **IS** | `M×K`（激活 `A[m][k]`）| `N` | 激活 `A` | 沿行**左流** | **右边** |

下面用同一个 `2×2 · 2×2 = 2×2` 的例子（`A·B=C`），把三种数据流的时间维**逐拍喂什么**摆出来
（略去脉动错位，只看每拍喂哪一片）：

```
A = [a00 a01]   B = [b00 b01]   C[m][n] = a[m][0]·b[0][n] + a[m][1]·b[1][n]
    [a10 a11]       [b10 b11]
```

**OS（阵列 = 输出 2×2；时间维 `K=2`）**：PE(m,n) 原地累加 `C[m][n]`。
```
拍0 (k=0): 左喂 A[:,0]=(a00,a10)，上喂 B[0,:]=(b00,b01) → 每个 PE 累加 a[m][0]·b[0][n]
拍1 (k=1): 左喂 A[:,1]=(a01,a11)，上喂 B[1,:]=(b10,b11) → 每个 PE 累加 a[m][1]·b[1][n]
2 拍后 PE(m,n)=C[m][n]，就地读出。      ← 时间维 K：逐拍喂一个收缩切片，同一 PE 累加
```

**WS（阵列 = `K×N` 2×2，权重驻留；时间维 `M=2`）**：PE(k,n) 驻留 `B[k][n]`（先载入、不动）。
```
拍0 (m=0): 喂激活行 A[0,:]=(a00,a01)，psum 沿列下流累加 → 出 C[0,:]=(C00,C01)
拍1 (m=1): 喂激活行 A[1,:]=(a10,a11)，psum 沿列下流累加 → 出 C[1,:]=(C10,C11)
                                       ← 时间维 M：逐拍流入一个输出行
```

**IS（阵列 = `M×K` 2×2，激活驻留；时间维 `N=2`）**：PE(m,k) 驻留 `A[m][k]`。
```
拍0 (n=0): 喂权重列 B[:,0]=(b00,b10)，psum 沿行左流累加 → 出 C[:,0]=(C00,C10)
拍1 (n=1): 喂权重列 B[:,1]=(b01,b11)，psum 沿行左流累加 → 出 C[:,1]=(C01,C11)
                                       ← 时间维 N：逐拍喂一个输出列
```

三者区别只在"哪两维进空间、谁驻留"，剩下那一维一律走时间、逐拍迭代。

> OS / WS 两种的完整时序推导（含反对角线 skew 等错位时序）分别在 [OS_restore_design.md](OS_restore_design.md)
> 和 [WS_weight_design.md](WS_weight_design.md)。IS 目前仅作为模式占位，尚未实现（见 [§14](#14-现状与待梳理清单)）。

---

# 第三部分 · 接口

## 7. PE：阵列的基本单元

阵列网格里的每个格子就是一个 **PE**。sa 把数据路由到各 PE 后，**实际的乘加由 PE 完成**（sa 自己不算，见 §5④）。
和 sa 一样 **PE 也模式无感**——只做乘加、存权重、累加 psum，不知道自己在跑 OS 还是 WS。实现见
[pe.py](../sim/eval/analyzer/sim_model/pe.py)。

本节只讲 **PE 的接口**：sa 每拍**驱动**给 PE 的输入、以及 sa 从 PE **读取**的输出。端口是逻辑层；
PE 内部的寄存器 / 数据通路 / 流水机制见第四部分。

**sa 每拍驱动给 PE 的输入：**

| 输入端口 | 语义 | 服务模式 |
|---|---|---|
| 激活 `a_in` | 本拍激活，进 PE 参与乘法，并寄存待下拍右传 | 全部 |
| 有效位 `a_vld` | **标量**；`a_in` 本拍有效才更新 `a` 寄存器，否则保持 | 全部 |
| 权重 `b_in` | 权重通路：载入 **active**（OS / IS）或 **shadow** `b_buf`（WS）——由构造 `is_b_buf` 定 | 全部 |
| 上游有效 `b_vld` | **标量**；`b_in`（上游权重）本拍是否有效 | 全部 |
| 本格 ready `b_rdy` | **标量**；本格能否接收。OS/IS 恒真（不反压）；**WS = shadow 反压 FIFO 的 ready 链**（sa 算，见 §10）。载入 = `b_vld & b_rdy` | 全部 |
| 累加 `acc_in` | 累加输入：上一拍 psum / 上邻 psum / 0（由数据流定，见 §10）| 全部 |
| 翻转 `b_sw` | 一拍把 active ↔ shadow 权重互换，并清 shadow valid（让出可再填）| WS |

**sa 从 PE 读取的输出**（用于路由到邻居 / 落到 accumulator / 算 ready 链）：

| 输出 | 用途 |
|---|---|
| `state`（psum）| 累加结果 / 部分和——OS 选读、WS 底行取，或作邻居的 `acc_in` |
| `a` / `a_vld` | 传给右邻（行内右流，valid 同行右传）|
| `b`（active）| 传给下邻（列内下流，OS / IS）|
| `b_buf` / `b_buf_vld` | WS：shadow 内容 + 占位标志，供 sa 算每列 **ready 链**（反压）|

**契约**（PE 对 sa 的保证，与模式无关）：
- 乘法**恒用 active 权重**：`prod = a · b`（shadow 只待命、不参与乘，靠 `b_sw` 换上来才生效）。
- 两段式：`update` 算 `_next`、`commit` 落定（§5③）。
- 流水深度由构造参数 `latency`（1 / 2）定，影响"输入到 psum"的拍数；机制见第四部分。

> `pe.update(a_in, a_vld, b_in, b_vld, b_rdy, acc_in, b_sw)`，载入 = `b_vld & b_rdy`。**反压只在带 buf（shadow，WS）
> 这条路上**：active 路（OS/IS）`b_rdy` 恒真、普通 valid 流式载入；shadow 路（WS）`b_rdy` 由 sa 每列 ready 链给——填满即停、保持到 `b_sw`。
> 影子**复用 `b_in` 通道**（不设独立 `shadow_in`），进 active 还是 shadow 由构造 `is_b_buf` 定（§9 / §13）。

## 8. sa 的对外接口

按 §5② 的两条轴，sa 的输入分两类。本节只讲**动态接口**——每拍 `update(...)` 传进来的东西
（对应 RTL 里 module 的逐拍 IO 端口，见 §5③ 映射）。静态构造参数（选模式的 `is_shift_*`）留到 §9 按模式列。

`update` 签名：

```
update(a_data, a_vld, b_data, b_vld, acc_clr=None, output_sel=None, b_sw=None)
```

| 参数 | 形状 | 注入位置 | 服务模式 | 语义 |
|---|---|---|---|---|
| `a_data` | `[M]` | 左边缘（每行 1 个）| 全部 | 本拍从**左**喂入的数据 A，按行进 |
| `a_vld` | `[M]` bool | 左边缘（每行 1 个，随 `a_data`）| 全部 | 各行 `a_data` 的有效位；随 `a_data` 一起右传，到 PE 时门控其载入 |
| `b_data` | `[N]` | 上边缘（每列 1 个）| 全部 | 本拍从**上**喂入的数据 B；经 `b_vld` 选通后载入 |
| `b_vld` | `[N]` bool | 上边缘（每列 1 个，随 `b_data`）| 全部 | 各列 `b_data` 的有效位——OS/IS 逐拍载入 active；WS 喂进 shadow **反压 FIFO**（填满即停，§10）|
| `acc_clr` | `[M]` bool / None | 左边缘 | OS | 选中行的 PE 累加器本拍清零（drain 后复位）|
| `output_sel` | `[M]` bool / None | 左边缘 | OS | 选中行本拍 drain 读出使能 |
| `b_sw` | `[M]` bool / None | 左边缘（每行 1 个）| WS | 翻转使能，**随 `a_data` 一起向右逐拍推进**；到达某 PE 时该 PE 翻转 active ↔ shadow |

要点（呼应 §5②）：

- **数据 / valid / 控制三类**：`a_data` / `b_data` 是数；`a_vld` / `b_vld` 是各自的有效位；
  `acc_clr` / `output_sel` / `b_sw` 是控制使能。由 controller 按数据流时序逐拍点亮（哪种模式点哪些，见 §9）。
- **接口纯 valid、无 ready；反压只在带 buf 的 shadow 路内部**：sa 的逐拍接口上不暴露 ready（要 stall 由
  controller 全局冻结 feed）。**唯一的反压在 WS 影子 FIFO 内部**——每列一条 ready 链做反压（填满即停、§10），
  这是 sa 内部、不上接口。active 路（OS/IS）是普通流式 valid、不反压。`a_vld`/`b_vld` 把 golden model 的
  "`None`=本拍无数据"显式成 RTL valid 位。
- **只在边缘注入，sa 内部传播**：所有参数都是 `[M]`（左边缘，每行 1 个）或 `[N]`（上边缘，每列 1 个）——
  controller 只在阵列**一条边**注入，信号在 sa 内逐拍推进、形成内部图案（数据连同其 `vld` 一起传播、
  `b_sw` 随 `a_data` 右推、`acc_clr` / `output_sel` 沿行推进；§11）。**没有**直接喂给每个 PE 的 `[M][N]` 输入。
  到达 PE 时，每个 PE 拿到的是**标量** valid（边缘数组经传播后落到单个 PE）。
- **WS 影子权重 = 反压式下移 FIFO**：WS 下 active 驻留（不从 `b_data` 取），`b_data` 在 WS 专喂**影子**。影子是一条
  valid/ready 反压 FIFO——倒序喂权重行、逐拍沉底堆叠，**填满即反压停、保持到 `b_sw` swap**（无需"load 窗口拉低保持"，
  反压自动停得干净、不会移过头）。复用 `b_data` 通道、无独立 shadow-load 信号。细节见 §10。
- **不用的给 `None`**，sa 当它不存在（如 OS 不传 `b_sw`，WS 不传 `acc_clr` / `output_sel`）。

> 边界划清：`update` 只收**数据 + 控制使能**。结果的**行地址 / 槽地址不在这里**——由 controller 直送
> accumulator（§4）。sa 内部如何据这些参数路由到每个 PE（含影子权重怎样落到 `b_buf`）属实现细节，见 §10。

> ⚠ 待梳理：`output_sel`（OS drain 读出使能）当前在签名里已声明但 `update` 体内出口逻辑未接通（需配 controller/accumulator，
> 属集成，见 §14 A'）；且 `output_sel` 与 [OS_restore_design.md](OS_restore_design.md) §5.3 的 `out_en` 同物异名。

## 9. 三模式各怎么配置与调用

把 §5② 两条轴落到具体：**构造时**填静态 `is_shift_*` flag 选模式，**每拍**按模式给 `update` 该给的参数。
下表每列一种模式（IS 仅占位，尚未实现）：

| | **OS** | **WS** | **IS** |
|---|:---:|:---:|:---:|
| **谁驻留** | psum（输出原地）| 权重 `b` | 激活 `a` |
| **构造静态 flag** | | | |
| `is_shift_row`（`a` 右流）| `1` | `1` | `0` |
| `is_shift_col`（`b` 下流入 active）| `1` | `0` | `1` |
| `is_shift_acc_d`（psum 下流）| `0` | `1` | `0` |
| `is_shift_acc_l`（psum 左流）| `0` | `0` | `1` |
| **每拍 `update` 给** | `a_data`+`a_vld`, `b_data`+`b_vld`,<br>`acc_clr`, `output_sel` | `a_data`+`a_vld`, `b_data`+`b_vld`*, `b_sw` | （占位）|
| **留空（`None`）** | `b_sw` | `acc_clr`, `output_sel` | — |

> \* WS 的 `b_data`/`b_vld` 倒序喂权重行进 shadow 反压 FIFO；填满后反压自动停（无需 controller 卡窗口），保持到 `b_sw`。

读法（OS 列）：构造 `spatial_array(M, N, ..., is_shift_col=1, is_shift_row=1, is_shift_acc_d=0, is_shift_acc_l=0)`，
之后每拍 `update(a_data, b_data, acc_clr, output_sel)`（不给 `b_sw`）。WS / IS 照各自列填。

关键点：

- **`b_data` 喂给谁由 `is_shift_col` 定**：`=1`（OS / IS）→ `b_data` 进 **active 权重**（逐拍下流）；
  `=0`（WS）→ active 权重驻留不动，`b_data` 改进 **shadow 权重**（这就是 WS 用 `b_data` 载影子的由来，§8）。
  无需额外的"shadow 使能"flag——`is_shift_col=0` 本身就把 `b_data` 指向 shadow。
- **`latency`（1 / 2）是构造参数、与模式无关**：只决定流水深度（§7），三种模式都可配。
- **psum 出口随 flag 走**：OS（`acc_d=acc_l=0`，原地）靠 `output_sel` 选读；WS（`acc_d=1`）下流出底边；
  IS（`acc_l=1`）左流出右边。对应 §6 末列。

# 第四部分 · 内部细节

## 10. 路由实现：从快照算每个 PE 的输入

承 §5④：每拍 `update` 用**上一拍快照**（各 PE 的 `a / b / b_buf / state`）加**本拍边缘注入**，
为每个 PE 算出 `a_in / b_in / acc_in`（及随行的 valid），再逐个调用
`pe.update(a_in, a_vld, b_in, b_vld, acc_in, b_sw)`。所有路由都遵循同一套**移位**规则。

**统一移位规则**（边缘注入 + 内部取邻居快照）：

```
右流（a 沿行）：PE(r,c) 输入 = c==0 ? 左边缘注入 : 左邻 PE(r,c-1) 上一拍值
下流（b 沿列）：PE(r,c) 输入 = r==0 ? 上边缘注入 : 上邻 PE(r-1,c) 上一拍值
不移位        ：PE(r,c) 输入 = 自身上一拍值（原地保持）
```

是否移位由静态 flag（`is_shift_*`）定 = 模式。

**`_route_a`（激活 `a`，右流；纯 valid，无反压）**
- `is_shift_row=1`（OS / WS）：`a_data[r]` 从左注入第 0 列、逐拍右移；**`a_vld[r]` 与 `a` 同行右传**（valid 随数据走）。
- `is_shift_row=0`（IS）：激活驻留，`a` 原地不动。

**`_route_b`（权重 `b`，下流）**
- `is_shift_col=1`（OS / IS）：`b_data[c]` 从上注入第 0 行、下移进 **active** 权重，`b_vld=b_vld[c]`、`b_rdy` 恒真（流式载入，不反压）。
- `is_shift_col=0`（WS）：active 驻留；`b_data` 进 **shadow 反压 FIFO**。**每列算一条 ready 链**（组合往上）：
  `ready_k = (空) or ready_{k+1}`；给 PE 的 `b_rdy[k]=ready_k`、`b_vld[k]`=上游有效（顶=`b_vld[c]`、其余=上邻 `b_buf_vld`），
  `b_in[k]`=顶为 `b_data[c]`、其余取上邻 `b_buf`；载入 = `b_vld & b_rdy`。
  倒序喂、沉底堆叠，**填满 → ready 全 0 → 停**，保持到 `b_sw`。决策 A：同一条 `b_in` 通道，进 active 还是 shadow 由 `is_b_buf` 定。

**`_route_acc`（psum 累加输入，源优先级）**——每个 PE 的 `acc_in` 按优先级单选一个来源：

| 优先级 | 条件 | `acc_in` 取 | 用在 |
|---|---|---|---|
| 1 | `acc_clr` 命中（drain 复位）| `0` | OS drain 后从头算 |
| 2 | `is_shift_acc_d=1` | 上邻 psum（顶行取 `0`）| WS：psum 下流 |
| 3 | `is_shift_acc_l=1` | 右邻 psum（右列取 `0`）| IS：psum 左流 |
| 4 | 否则（原地）| 自身 psum | OS：原地累加 |

> 第 2–4 条由静态 acc flag 三选一、整 run 不变（= 模式的 psum 走向，对照 §6 末列）；只有第 1 条
> `acc_clr` 是逐拍动态、沿行传播形成 drain / restore 图案（§11）。

> ✅ 已实现并单测：[spatial_array.py](../sim/eval/analyzer/sim_model/spatial_array.py) 的 `_route_a`（a/a_vld 右传）、
> `_route_b`（OS active 下流 / WS 每列 ready 链反压）、`_route_acc`，6 参 `pe.update`。`output_sel`（OS drain mux 出口）仍预留未接。

## 11. 控制信号在 sa 内的传播

§8 说控制信号（`acc_clr` / `output_sel` / `b_sw`）都是 `[M]` 边缘信号——controller 只在**左边缘每行注入一个 bool**。
但实际要点亮的是阵列内部一**片** PE；这片图案不是 controller 直接给的，而是**注入后在 sa 内逐拍向右推**形成的。

**什么叫"斜向逐拍推进"**：一个 bool 在某行左边缘注入后，每拍向右挪一个 PE。所以行 `r` 在第 `t` 拍注入的信号，
第 `t+c` 拍才到达 PE(r,c)。各行注入时刻错开、每行又同速右移，于是任一时刻"被点亮"的 PE 连成一条**斜线**：

```
同一个信号在 4 拍里逐拍右移（× = 本拍点亮的 PE，一行 4 个 PE）：
 拍 t      拍 t+1    拍 t+2    拍 t+3
 ×···      ·×··      ··×·      ···×
```

多行错开注入时，斜线扫过整个阵列——这就是 OS drain 的复位 / 读出图案、WS 翻转图案的来源。

**实现：`_shift_in`（右移一格 + 左边缘注入）**
sa 内部为每种控制波各存一个 `[M][N]` bool 网格（`_drain_grid` / `_restore_grid` …），两段式推进：

```
grid_next[r][0]   = 本拍左边缘注入（acc_clr[r] / output_sel[r] / b_sw[r] …）
grid_next[r][c>0] = grid[r][c-1]        # 取上一拍的左邻 → 整体右移一格
```

`commit` 落定 `grid = grid_next`；本拍生效用组合的 `_shift_in` 结果（抵消 ctrl→sa 的一拍寄存，时序对齐）。

**三种控制波各走各的：**
- `acc_clr`（OS）：drain 后把 PE 累加器复位为 0（§10 `_route_acc` 第 1 优先级）。
- `output_sel`（OS）：drain 读出使能——点亮的 PE 本拍把 psum 送出口（§12）。
- `b_sw`（WS）：翻转 active ↔ shadow，**贴着 `a_data` 的右移走**（同在右流方向、逐列错 `+c`），于是每个 PE
  在"新 tile 激活刚到"那拍翻权重（off-by-one 细节见 [WS_weight_design.md](WS_weight_design.md)）。

**时序对齐**：各控制波相对数据波的精确偏移（OS 的 `drain_delay = latency+1`、WS 翻转早 1 拍等）不靠绝对拍号算，
而是用测试钉死（与 §5③ counter-based 风格一致）。

> ⚠ 现状：`_drain_grid` / `_restore_grid` 在 [spatial_array.py](../sim/eval/analyzer/sim_model/spatial_array.py)
> 已有骨架但 `update` 体内未接通；`b_sw` 的 `[M]` 注入 + 传播也待实现（当前 `w_switch` 是 `[M][N]` 直给）。见 §14。

## 12. 输出口与带宽不变式

§3 的硬约束：阵列内有 `M×N` 个 PE，但物理出口只有**一条边**——一拍最多吐 `N`（顶 / 底边）或 `M`（左 / 右边）个结果。
三种数据流各用不同的出口机制（对照 §6 末列）：

**OS：每列一个 M:1 mux，原地选读**
- OS 的 psum 原地累加、不移动。drain 时靠 `output_sel` 波（§11）点亮"该读哪个 PE"：每列被点亮的那个 PE 的
  psum 经一个 **M:1 mux** 选出 → `sa.output[N]`（**只出数据**；行地址 / 槽地址由 controller 给，§4）。
- **为什么这样能"零气泡"**（气泡 = 阵列空转、不算有用乘加的拍）：换一种 drain 方式——把 psum 一格格**移出**
  阵列（像 WS 那样从边上流出）——搬运期间这些 PE 被占着、算不了下一个 tile，于是必须等旧 tile 全 drain 完才能开新
  tile = 气泡。OS 的 mux 是**原地选读**：只在旁边接根线把 PE 的 psum 读走，**不挪动数据、不占 PE 的计算通路**，
  所以读出旧 tile 的同时 PE 已能开始算下一个 tile → 背靠背 tile 之间**零气泡**。

**WS：psum 下流出底边**
- WS 的 psum 沿列下流（§10 `acc_d`）。算完的结果从**底行**逐拍流出 → 读 `sa.data[M-1]`（`[N]` 宽）。

**IS（占位）：psum 沿列方向流、从一侧列边出**（未实现，方向随 `is_shift_acc_l` 定）。

**唯一的带宽约束：`Gk ≥ M`（每列 mux 不打架）**

OS 一个接一个算输出块；每个块算完由一条 `output_sel` 斜线（§11）drain 出去。记 **`Gk` = 相邻两块开始 drain 的
间隔拍数**。约束只来自**每列的 mux**：每列只有一个 M:1 mux，一拍只能读该列 1 个 PE；一个块把某列 `M` 行依次读出
要 `M` 拍，所以下个块至少隔 `M` 拍才能再用这列。若 `Gk < M`，两个块的 drain 斜线会在**同一列同一拍**点亮两个 PE
→ 一个 mux 读不了俩，物理上就错。这是 sa 侧的硬限（`assert ≤1/列`）。

> **为什么没有"`Gk ≥ M+N-1`"那条**：写进 accumulator 哪个槽的**槽地址不是全边共用的一个标量**——它和结果一样
> 在 accumulator 里**逐拍推移**，每列各自带着对的槽地址（per-column tag，参 WS 的 capture tag，
> [WS_weight_design.md](WS_weight_design.md) §7）。所以即便两个块的 drain 斜线同拍共存、不同列属不同块，每列也各写
> 各的槽，**无需**让块隔到 `M+N-1` 拍。

`Gk` 在原子 8×8、真实层里远大于 `M`（一个块累加很多 K-chunk 才 drain 一次），恒满足；只有紧密打包到 `Gk < M`
才会撞 mux，属**禁止工况**。

> ⚠ 与 [OS_restore_design.md](OS_restore_design.md) §6 冲突：那份论证的是"**标量** `wr_tile` + `Gk ≥ M+N-1`"；
> 本文改为槽地址逐拍推移（per-column），去掉 `Gk ≥ M+N-1`。需统一，见 §14。

> ⚠ 现状：OS 的 mux 选读出口、`sa.output` / `sa.data[M-1]` 读法在 [spatial_array.py](../sim/eval/analyzer/sim_model/spatial_array.py)
> 尚未按本节接通（半迁移）。见 §14。

## 13. PE 内部细节

§7 给了 PE 的接口；这里是它的内部实现（[pe.py](../sim/eval/analyzer/sim_model/pe.py)）。

**寄存器**（两段式，对应 RTL 时序逻辑，§5③）：

| 寄存器 | 含义 |
|---|---|
| `a` / `a_vld` | 本拍激活 + 其 valid（同行右传）|
| `b` | **active** 权重（参与乘）|
| `b_buf` / `b_buf_vld` | **shadow** 权重 + 占位标志（WS 反压 FIFO 一格；供 sa 算 ready 链）|
| `state` | psum（累加结果 / 部分和）|
| `mult` | `latency=2` 的流水寄存器（见下）|

**`pe.update(a_in, a_vld, b_in, b_vld, b_rdy, acc_in, b_sw)` 算 `_next` 的逻辑：**

```
prod = a · b                        # 恒用 active 权重
latency=1:  state_next = acc_in + prod
latency=2:  state_next = acc_in + mult ;  mult_next = prod      # 乘/加拆两拍

a_next = a_vld ? a_in : a ;  a_vld_next = a_vld    # a 与其 valid 右传

b_load = b_vld & b_rdy              # 握手：上游有效 & 本格 ready
权重（is_b_buf=True 即 WS）：
  b_sw          → 交换 b ↔ b_buf，b_buf_vld_next=False   # swap：抓进 active，shadow 让出可再填
  否则 b_load   → b_buf_next=b_in, b_buf_vld_next=True    # shadow 载入
  否则          → 全保持（反压停）
is_b_buf=False（OS/IS）：  b_next = b_load ? b_in : b   # active 流式载入（b_rdy 恒真）
```

`b_in` 进 active 还是 shadow，由**构造参数 `is_b_buf`**（按模式设）定，不是 PE 运行时判断模式（§5③：构造参数 = Verilog `parameter`）。
`b_rdy`：OS/IS 恒真（不反压）；WS = sa 每列 **ready 链**给的反压门控（填满即停）。

**`latency` 1 vs 2（流水深度）**：`1` = 单拍组合乘加直接进 psum；`2` = 多一级寄存器 `mult` 把"乘"和"加"拆到两拍
（本拍存乘积、下拍才加），对应 RTL 给乘法器加一级流水以缩短关键路径。`a` / `b` 的移位仍是 1 拍/PE，只有 MAC→psum
这条路多 1 拍。

**数据通路**（active 权重参与乘法，shadow 在旁待命，由 `b_sw` 换上）：

```
            ┌──────────────┐   swap (b_sw)   ┌──────────────┐
            │  b  (active) │ ◀─────────────▶ │ b_buf (shadow)│
            └──────┬───────┘                 └──────────────┘
                   │ active weight
   a ──▶ │ a │ ──▶ × ──prod──▶ + ──▶ │ state │ ──▶ psum
                              ▲
                           acc_in
```

> ✅ 已实现并单测（[pe_test.py](../sim/eval/analyzer/sim_model/pe_test.py) / [sa_test.py](../sim/eval/analyzer/sim_model/sa_test.py)）：
> 6 参 `pe.update`、`a_vld` 右传、`b_buf` valid/ready 反压、`b_sw` 清 `b_buf_vld`；latency 1/2、OS `sa.data==A@B`、WS 反压载入→swap→stream 全绿。

# 附录

## 14. 现状与待梳理清单

> 本节是**代码与设计的缺口清单**，全文各处 ⚠ 注汇总于此；随梳理推进逐条消除、最终可整节删除。

**A. 已实现并单测**（[pe.py](../sim/eval/analyzer/sim_model/pe.py) / [spatial_array.py](../sim/eval/analyzer/sim_model/spatial_array.py)，[pe_test.py](../sim/eval/analyzer/sim_model/pe_test.py) / [sa_test.py](../sim/eval/analyzer/sim_model/sa_test.py)）
- ✅ 接口改名 + 7 参 `pe.update(a_in,a_vld,b_in,b_vld,b_rdy,acc_in,b_sw)`（载入=`b_vld&b_rdy`）；`a_vld` 右传。
- ✅ `_route_a`（a 右流）/`_route_b`（OS active 下流 / **WS shadow 每列 ready 链反压**）/`_route_acc`（含 `acc_clr`）。
- ✅ `b_sw` `[M]` 左边缘注入 + 右推；swap 抓 shadow→active 并清 `b_buf_vld`。
- ✅ 验证：OS `sa.data==A@B`、WS 反压载入→swap→stream 底行==a·B，latency 1&2、方阵+矩形。

**A'. 尚未接通**
- **OS drain 出口**：`output_sel`→`sa.output`（每列 M:1 mux 选读）、`acc_clr` 的 drain 波传播、WS 底行 `sa.data[M-1]` 的取数封装——`output_sel` 形参已预留但 `update` 体内未接（需配 controller/accumulator，属集成）。

**B. 与既有文档冲突，待统一**（改旧文档或改代码，二选一）
- **影子载入机制**：本文 = **valid/ready 反压 FIFO**（填满即停、保持到 swap，仅"带 buf"才反压）；[WS_weight_design.md](WS_weight_design.md) §4 = `shadow_load` 窗口信号（灌满拉低保持）。机制不同，需统一。
- **`b_sw` 几何**：本文 = `[M]` 边缘注入 + 内部传播；[WS_weight_design.md](WS_weight_design.md) §5 = `[AR][AC]` per-PE mask（controller 算好）。
- **行地址归属**：本文 = controller 出（纯地址面）；[OS_restore_design.md](OS_restore_design.md) §5.3 = `out_row` 来自 sa。
- **槽地址 `wr_tile`**：本文 = accumulator 内逐拍推移（per-column）、去掉 `Gk≥M+N-1`；[OS_restore_design.md](OS_restore_design.md) §6 = 标量 + `Gk≥M+N-1`。
- **命名**：本文 `output_sel` ↔ OS_restore §5.3 `out_en`，同物异名。

**C. 未实现 / 占位**
- **IS 模式**：目前仅出现在 §6 维度表，路由 / 输出 / 验证均未做（`is_shift_row=0, is_shift_col=1, acc_l=1` 占位）。

## 15. 关联文档与术语表

**关联文档 / 源码**
- [OS_restore_design.md](OS_restore_design.md) — OS 累加 / drain / restore / mux 出口细节。
- [WS_weight_design.md](WS_weight_design.md) — WS 权重双缓冲 / 切换 / capture tag 细节。
- [spatial_array.py](../sim/eval/analyzer/sim_model/spatial_array.py)、[pe.py](../sim/eval/analyzer/sim_model/pe.py) — 实现。

**术语表**

| 术语 | 含义 |
|---|---|
| `M / N / K` | 矩阵乘三维：A=M×K、B=K×N、C=M×N |
| `a_data` / `b_data` | sa 左 / 上边缘逐拍喂入的数据（A / B），`[M]` / `[N]` |
| `a_vld` / `b_vld` | 数据有效位。接口纯 valid；反压只在 WS 影子 FIFO 内部（每列 ready 链）|
| active / shadow 权重 | PE 内参与乘的 `b` / 备用的 `b_buf`，`b_sw` 一拍互换 |
| psum | 部分和（PE 的 `state`）|
| drain | 把算好的 psum 读出阵列 |
| `Gk` | 相邻两输出块开始 drain 的间隔拍数（OS 带宽约束 `Gk≥M`）|
| `latency` | PE 流水深度（1 / 2）|
| `is_shift_*` | sa 构造静态 flag = 选数据流（OS / WS / IS）|
| 斜向逐拍推进 | 边缘注入的控制信号每拍右移一格、形成对角图案（§11）|
