# OS 累加与 restore 设计（决策：不支持 gap+驱逐续算）

本文档记录 Sa-TPU golden model 里 **OS（output-stationary）数据流的累加复位（restore）设计决策**，
是 `spatial_array._route_acc` / `Accumulator` / cycle_analyzer 实现的依据。

---

## 0. 决策（TL;DR）

> **OS 累加恒为"原地连续累加 + drain 时复位为 0"。不支持"算到一半被驱逐、部分和先存累加器、回来再读回 PE 续算"。**
> 因此 **`sa.update` / `_route_acc` 的 `restore_data` 接口删除**，restore 时 PE 累加输入恒给 `0`。

---

## 1. 背景：restore 是什么

OS 下 PE(m,n) 原地累加 `C[m][n] = Σ_k a_mk·b_kn`。一个输出块的所有 K-chunk 在**同一个 PE 里连续累加**，
算完整块后由 drain 波前（`ctrl.acc_read`）把 psum 读出到 accumulator，**同拍把该 PE 的累加器复位**，
好让下一个 tile 从头算。

"复位成什么值"就是 `_route_acc` 给被 `restore_mask` 选中的 PE 算出的 `acc_in`：

```
sa.update(..., restore_data, restore_mask)
  → _route_acc:  restore_mask[r][c] 命中 → acc_in[r][c] = restore_data[r][c]
  → pe.update:   state_next = acc_in + prod        # acc_in=0 ⟹ 从头开始
```

- `restore_data = 0` → **全新 tile**，从零累加。
- `restore_data = 累加器回读值`（非 0）→ **续算**：把之前 spill 到累加器的部分和读回，接着往上加。

## 2. 为什么删掉非 0 路径（续算）

非 0 回读只服务一个场景：**gap 重启 + 驱逐**——某个 tile 的 K-chunk 算到一半，遇到足够长的 gap 使
controller 进 IDLE，且 gap 期间 PE 被别的活占用、state 被清零（spill 到累加器）；重启时必须把部分和
从累加器**读回 PE** 才能正确续算。

**本设计明确不支持该场景**，基于以下不变式：

- **tile 原子化、缩放靠块数**（见 [tile-atomic-scale-by-tk]）：一个输出块的全部 K-chunk 一口气连续喂完，
  中途不打断、不驱逐。
- OS 走**原地连续累加**：PE state 全程保留到整块 drain，从不中途 spill。
- 跨段/跨块的累加由 accumulator 侧机制承担（OS 原地累加；WS 用 `accumulate_row`），**都不依赖把部分和
  读回 PE**。

既然没有任何真实数据流触发非 0 回读，保留这条反馈线只是徒增接口复杂度。

## 3. 接口影响（已实施）

消费端、生产端、调用点全部清理完毕：

| 位置 | 改动 |
|---|---|
| `spatial_array.update` / `._route_acc` | 删除形参 `restore_data`；restore 分支恒给本地 `Z = dtype_state(0)` |
| `restore_mask`（`ctrl.acc_read`）| **保留**——仍需用它标记"哪些 PE 本拍 drain 并复位为 0" |
| `Accumulator.update` | 删除形参 `read_mask` + `self.restore_data` 生产逻辑；连带删 `self.tile_id`（变孤儿）|
| `Controller` | 删除 `update()` 的 `restore_en` 形参 + `self._restore_en`；`_psum_init = _block_start`（续算项恒 False，等价化简）|
| 调用点 cycle_analyzer / test_matmul_driver / test_sim_model / test_ws_model | 去掉 `restore_data` / `read_mask` / `restore_en` 实参 |

> 保留但**仅服务 overwrite**：`Controller._os_restore` / `_restore_delay` / `_psum_init` / `acc_read` 波前——
> 它本就同时负责新块 overwrite(data=0)，名字含 "restore" 但语义已只剩 overwrite（未改名，保持 surgical）。

## 4. ⚠ 限制（标记点）

> **本设计不支持 OS 的 gap+驱逐续算。** 一旦某 tile 的累加被打断且 PE state 丢失，无法从累加器读回部分和
> 继续——结果会错。
>
> 已随本次清理删除测试 `test_gap_restart_restore`（测的正是被废的 gap+驱逐续算）及 `test_sim_model._step`
> 的 `restore_en` 分支。
>
> **未来若需支持**（例如阵列被抢占需 spill/restore、或非原子的中断式累加）：必须**重新加回**
> "accumulator 读口 → PE 累加输入" 的反馈线（即恢复 `restore_data` 参数 + accumulator 的 `read_mask` 回读
> + controller 的 `restore_en`），本文档第 1–2 节即为彼时的设计参照。

## 5. OS drain 输出：每列 8:1 mux + 组合输出端口（已实施）

### 5.1 动机：sa 出口带宽 = 一条边
阵列内部有 M×N 个 PE，但物理输出口只有一条边：底边 N 根线 / 右边 M 根线。**一拍最多吐 N（或 M）个结果**，
不可能一拍把 M×N 个 PE 全读出。原来 `accum.update(sa.data[M][N], ...)` 传整阵列快照，不忠实于这个带宽。

### 5.2 OS vs WS 两种 drain 硬件
| | drain 机制 | 出口 | tile 重叠 |
|---|---|---|---|
| **OS** | 每列一个 **M:1(8:1) mux 原地选读** | 长 N 组合输出 `sa.output` | 零气泡 ✅ |
| **WS** | psum **下移**到底边出口 | 长 N 底行（`accumulate_row` 读 `sa.data[AR-1]`）| — |

OS 是**原地选读**（mux），不移位 → 不占 PE、不引入 drain 气泡，保住背靠背 tile 重叠。

### 5.3 接口（控制 bool 使能随数据从左进入、在 sa 内右传成波前；数据面/地址面分开）
波前生成**放在 sa 里**（控制和数据一起流），controller 只给**左边缘每行 bool 使能** + 标量槽地址：
- **controller**（地址面）：
  - `out_en[r]` (bool [M]) = `_acc_sr_next[r+drain_delay] is not None`——每行 drain **读出使能**（左边缘注入）。
  - `acc_rst[r]` (bool [M]) = `_acc_read_sr_next[r+restore_delay]`——每行 **清零使能**（左边缘注入）。
  - `wr_tile`（**标量**）= `_acc_sr_next[D:]` 整条波前里唯一的非 None tile_id = 当前在写的块（槽地址）。
  - `_acc_sr` 仍是反对角 SR（搬 tile_id），但**只把"有无波前"(bool) 给 sa**；tile_id 只用于算标量 wr_tile，**不进 sa**。
- **sa**（数据面）：内部 `_drain_grid`/`_restore_grid`（[M][N] bool 寄存）**向右推一格 + 左边缘注入**
  ⟹ 反对角波前由本地传播形成（行 skew 来自 controller 注入时序，`+c` 来自 sa 右推）。本拍用组合的 `_shift_in` 结果：
  - `self.output` [N]：每列被 drain 波前点亮的行做 M:1 mux 选读（组合端口）。
  - `self.out_row` [N]：每列被点亮的**行号**（给 accum 行寻址）。
  - restore：被点亮的 PE 就地复位为 0（`_route_acc(restore_now[M][N])`）。
  - 时序与旧"controller 直接出 [M][N]"**严格一致**（sa 用本拍 `_shift_in` 抵消掉 ctrl→sa 的那一拍寄存）。
  WS 不传 `out_en`/`acc_rst`（默认 None）→ `output=None`、grid 全 False、不复位，走 `accumulate_row`。
- **accum**：`update(sa.output, sa.out_row, ctrl.wr_tile)` → 写 `mem[wr_tile][out_row[c]][c]`。
  数据 + 行地址来自 **sa**（数据面），槽地址 `wr_tile` 来自 **ctrl**（地址面）。
- **调用顺序**：`sa.update` 在 `accum.update` 之前。

### 5.4 ⚠ 两个不变式（强度不同）
1. **`Gk = cpb·K ≥ M`**（mux 每列 ≤1）：一个 M:1 mux 一拍每列只能选 1 个。**sa 解析每列 mux 时**内置 `assert ≤1/列`。
2. **`Gk ≥ M+N-1`**（一拍 ≤1 个 tile_id，更严）：drain 波前不重叠 ⟹ 标量 `wr_tile` 够用。**ctrl 扫 `_acc_sr[D:]` 时**内置 `assert ≤1`。

原子 8×8（`K=M=N=8`，块按 Gk 顺序流，真实层 cpb=196/8）两条都恒满足。`test_pipelined_multiblock` 已按 `Gk≥M+N-1` 间距设形状；`Gk<M+N-1` 的紧密打包属**禁止工况**（assert 挡）。

## 6. ✅ 已实施：`wr_tile`（写哪个槽）归控制面、标量

`wr_tile`（drain 写进哪个 accumulator 槽）是**地址**，已归 **controller（地址面）**生成、且为**标量**，
**不再随波前进 sa 数据面**（PE 只持 psum、out_en 只传 bool 使能）。这依赖"一拍只有一个块在写"——下面是为什么标量够用：

> **若 `Gk < M+N-1`（drain-drain 重叠）会出问题**：相邻两块的 drain 波前**同拍共存于阵列**（一条扫完整阵列要
> `M+N-1` 拍），**不同列属于不同块**，同一拍要写 2 个不同 tile_id：
> ```
> 4×4, 块A波前 r+c=5、块B波前 r+c=1 同拍共存：
>          col0 col1 col2 col3
>   r0      ·  [B]   ·    ·
>   r1     [B]  ·    ·    ·
>   r2      ·   ·    ·   [A]
>   r3      ·   ·   [A]   ·
>  写哪槽:   B   B    A    A   ◀ 一拍 2 个不同值 ⟹ 标量不够
> ```
> 每列仍 ≤1 个 PE drain（mux 没问题），但槽地址需 per-column。

**故强制 `Gk ≥ M+N-1`（§5.4 不变式 2，assert 钉死）→ 任一拍最多一条 drain 波前 → 一拍一个 tile_id → 标量 wr_tile 够用。**
真实层 `Gk=cpb·K`（cpb=196/8）远超门槛；2-tile_id 只在被禁止的 `cpb=1` 紧密打包里出现，已不支持。

## 7. 关联
- [tile-atomic-scale-by-tk] — tile 原子 8×8、缩放靠块数，既是"无缝、不驱逐"的前提，也是 §5.4 `Gk≥M`（mux 够用）的前提。
- [WS_weight_design.md](WS_weight_design.md) — WS 的 K-段累加走 `accumulate_row`，psum 下移出底边，与 OS 的 mux 选读对照。
