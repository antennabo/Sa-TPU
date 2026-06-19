# accumulator 设计（输出累加器：drain / capture 写回 C，per-column 地址传播）

本文档写给**第一次接触本项目**的读者，自顶向下：先讲 accumulator 要解决什么，再讲整体、接口、细节。
读完应能看懂 [accumulator.py](../sim/eval/analyzer/sim_model/accumulator.py)。

它是数据通路的**末端**——阵列算出的结果从这里落盘成完整的输出矩阵 `C`。与 [controller_design.md](controller_design.md)、
[SA_array_design.md](SA_array_design.md) 配套：controller 出地址、sa 出值、**accumulator 把两者合起来写进输出存储**。

---

# 第一部分 · 需求与目标

## 1. accumulator 要解决什么

阵列一次只算一个 tile 的部分结果，且结果是**斜着、逐列/逐行**吐出来的；完整的 `C` 要跨多个 tile、多个 K-段拼/累加而成。
accumulator 就是那块**输出存储**，负责把每拍吐出的结果写到 `C` 的正确位置：

```
   sa（值）        controller（地址）         accumulator
  结果 per-column ─┐   槽 / 行（标量）─┐      ┌──────────────┐
                  └──────────────────┴────▶ │ _mem[slot]   │ → 完整 C tile
                                            │   [row][col] │
                                            └──────────────┘
```

- **存**：`_mem[slot][row][col]`，按"输出槽（哪个 tile）"分开存。
- **写**：每拍把 sa 吐的结果写到 `_mem` 对应位置。
- **累加**：K 切段时，同一输出位置的多段部分和要 **`+=`** 累加（而非覆写）。

## 2. 数据面 vs 地址面（本模块的核心分工）

写进 `C` 需要两样东西：**写什么值** + **写到哪**。本项目把这两者分给不同模块（参 [OS_restore_design.md](OS_restore_design.md) §5-6）：

| | 来源 | 内容 |
|---|---|---|
| **数据面** | **sa** | 结果值（per-column：OS drain 总线 / WS 底行 psum）|
| **地址面** | **controller** | 槽 `wr_tile` + 行 `m`（**标量**注入）|

> **统一原则（§14-D）**：地址走**标量注入 + accumulator 内 per-column 传播**——controller 每拍只发一个 `(行 m, 槽 wr_tile)`，
> accumulator 把它随 sa 的 per-column 结果**逐列对齐**后落盘。OS drain 与 WS capture 共用这一套（见 §4、§8）。

---

# 第二部分 · 整体设计

## 3. accumulator 在系统里的位置

```
   ┌────────────┐  标量 vld,row,slot,add    ┌──────────────────┐
   │ controller │ ───────────────────────▶ │   accumulator     │
   └────────────┘                          │  _mem[slot][r][c] │
   ┌────────────┐  结果值 per-column [N]     │  + per-column 地址 │ → C
   │     sa     │ ───────────────────────▶ │     传播 + 累加     │
   └────────────┘  (OS drain 总线/WS 底行)   └──────────────────┘
```

- **调用顺序**：`sa.update` 在 `accumulator` 之前（值先就绪）；两段式，`commit` 落盘。
- accumulator **不参与计算**，只做"地址对齐 + 写/累加"。

## 4. 核心：标量地址注入 + accumulator 内 per-column 传播

阵列结果是**斜着**吐的——同一拍底行不同列，属于**不同输出行 / 不同槽**（背靠背时尤甚，[WS_weight_design.md](WS_weight_design.md) §A）。
所以地址必须 **per-column**。但 controller 只发**标量**（§2）——per-column 是 accumulator **自己传播出来的**：

```
controller 每拍注入标量 (vld, row, slot, add)
        │
        ▼  accumulator 内一条移位寄存器，按 cap_delay 逐列对齐
   列 0 配它该有的 (vld,row,slot,add)；列 c 延 c 拍配它的那条
        │
        ▼  vld 真则写 _mem[slot][row][c] = 值（add 则 += 值）
```

这与 controller 的"标量 feed → fifo 内部传播"、"控制波标量 → sa 右推"是**同一条原则**：
**上游发标量，per-column 由下游（这里是 accumulator）铺开**。

---

# 第三部分 · 接口

## 5. 存储结构

```
_mem[slot][row][col]        # slot=输出槽(哪个 tile)，row/col=tile 内行列
```
- `NUM_TILES` 个槽，每槽一个 `M×N` 输出 tile。
- `get_tile(slot)` 取整槽结果（校验 / 落最终 C）。

## 6. 统一接口 `update`（一个方法，两段式）

按 §4 的统一原则，**OS drain 和 WS capture 是同一种写**——值从 sa（per-column）、地址从 controller（标量注入），
所以**只用一个 `update`**（不再分 OS/WS 两个方法）：

```
update(values, vld, row, slot, add):
  values[N] -- sa 出的 per-column 结果（OS = drain 总线 / WS = 底行 psum sa.data[AR-1]）
  vld       -- 标量：这拍注入的地址有效否（硬件 valid；为假时 row/slot/add 是 don't-care）
  row/slot/add -- 标量地址：写第几行 / 哪个槽 / 覆写还是累加（controller 给）
  → 内部 per-column 传播（§8）把 (vld,row,slot,add) 逐列对齐
  → 列 c 的 vld 为真才写 _mem[slot][row][c]（add 则 += 旧值）
```

- **显式 `vld`**（不是用 None 表示"不写"）：硬件里地址总线一直有值、靠一根 valid 决定写不写。`vld` 与地址一起 per-column 传播。
- per-column 地址不是 `[N]` 入参给的，而是 accumulator **内部传播出来的**（§8）——上游只发标量。
- **`vld`/`add` 跟着地址一起延迟**：写发生在注入后 `cap_delay + c` 拍，所以 `vld`/`row`/`slot`/`add` 必须随同一条目延迟，
  不能用"当拍"的值（否则覆写/累加、写不写都张冠李戴）。
- `commit` 落盘（§10）。

## 7. OS / WS 是同一 `update` 的两种用法

| | 值 `values`（数据面，sa）| 地址 `(vld,row,slot,add)`（标量，controller）| `add` |
|---|---|---|---|
| **OS drain** | drain 总线（每列 mux 选读的 psum）| drain 起拍注入 `vld=1, (行, 槽)` | `False`（PE 已累加完，drain 是覆写到槽）|
| **WS capture** | 底行 psum `sa.data[AR-1]` | feed 时注入 `vld=1, (m, wr_tile)` | 换 K-chunk=`True`、换 N-tile=`False`（§9）|

> 两者唯一的差别是**值从哪条边出**（OS mux 出口 / WS 底行）和 **`add` 模式**；地址机制、写法完全相同 → 一个 `update` 够。
> 不写的拍/列 = `vld=0`（地址 don't-care）。`add` 区分 K-chunk（同槽累加）vs N-tile（新槽覆写）。

---

# 第四部分 · 内部细节

## 8. per-column 地址传播（待接）

把 controller 的标量 `(vld, row, slot, add)` 对齐到 sa 的 per-column 结果，靠一条移位寄存器（即从 controller 挪过来的 capture SR）：

```
每拍：  sr_next = [(vld, row, slot, add)] + sr[:-1]            # 标量注入、逐拍移
列 c：  (vld_c, row_c, slot_c, add_c) = sr_next[c + cap_delay]  # 列 c 延 (c+cap_delay) 取该列地址
写：    if vld_c:  _mem[slot_c][row_c][c] = values[c]  (add_c 则 += )
```

- `cap_delay = (AR-1) + L + 1`（psum 下流 `AR` 行 + PE 流水 + 寄存；详见 [controller_design.md](controller_design.md) §13）。
- 这一段是 §controller §14-B 标的"capture 下游传播"——controller 已只发标量，**这条 SR 待在 accumulator 里补上**。

## 9. 累加 vs 覆写

| 边界 | 写法 | 为什么 |
|---|---|---|
| 换 **K-chunk**（同输出、下一收缩段）| `+=`（读旧值相加）| 部分和是 `Σ_k` 的不同段，要累加到同槽同位置 |
| 换 **N-tile**（另一组输出列）| 覆写（新槽）| 不同输出列、互相独立 |

> "切不切 / 累不累加"由 controller 决定（`switch_weight`、K-chunk vs N-tile，见 [controller_design.md](controller_design.md) §9.3），
> accumulator 只按传进来的 `add` 执行。

## 10. 两段式

- `update` / `accumulate_row` 只把要写的 `(slot, row, col, val)` 暂存进 `_pending`（不立即改 `_mem`）。
- `commit` 把 `_pending` 落盘（`+=` 在此读旧值）；与全项目两段式一致。

---

# 附录

## 11. 现状与待做

| 项 | 状态 |
|---|---|
| `_mem[slot][row][col]` + 两段式 `_pending` | ✅ 已实现（[accumulator.py](../sim/eval/analyzer/sim_model/accumulator.py)）|
| **统一 `update(values, vld, row, slot, add)` + 内部 per-column 传播 SR**（§6、§8）| ✅ **已实现 + 单测**（[accumulator_test.py](../sim/eval/analyzer/sim_model/accumulator_test.py)）：显式 vld、标量地址注入、列 skew、覆写/累加、`cap_delay`。两旧方法（`update(OS)`/`accumulate_row(WS)`）已合并 |
| `cap_delay` 精确值（WS）| ✅ **已钉死** `(AR-1) + L + 2`：psum 下流 `AR` 行 + PE 流水 `L` + 寄存 + 驱动循环一拍（accum 读上拍底行）。见 [ws1_e2e_test.py](../sim/eval/analyzer/sim_model/ws1_e2e_test.py) |
| **WS-1 端到端 `accum == A·B`** | ✅ **已验证**：controller + ab fifo + sa + accum 串通，8 形状 × latency 1&2（[ws1_e2e_test.py](../sim/eval/analyzer/sim_model/ws1_e2e_test.py)）|
| OS 接入（drain 也走标量注入）| ⏳ 待做：OS 现走 sa 的 `out_row`（per-column），改成 controller 标量注入 + 本 `update`（§14-D）|
| WS 多 tile / 复用（WS-2/3/4）| ⏳ 待做：K 切段累加（`add`）、N 切块新槽、activation buf 复用 |

## 12. 关联文档与术语表

**关联文档**
- [controller_design.md](controller_design.md) — 谁发标量 `(m, wr_tile)`（§7.2 / §12 capture、§8.4 drain）、`cap_delay`（§13）、统一方向（§14-D）。
- [SA_array_design.md](SA_array_design.md) §12 — OS drain mux 出口 / WS 底行 psum 出口（数据面来源）。
- [OS_restore_design.md](OS_restore_design.md) §5-6 — OS drain 写口、数据面/地址面分工、`Gk` 不变式。

**术语表**

| 术语 | 含义 |
|---|---|
| slot / `wr_tile` | 输出槽：结果写进哪个 tile（`_mem` 第一维）。OS drain 与 WS capture 同一语义 |
| 数据面 | 结果值，来自 sa（per-column）|
| 地址面 | 槽 + 行，来自 controller（标量注入，accumulator 内传播成 per-column）|
| `cap_delay` | `(AR-1)+L+1`：把标量地址对齐到底行 psum 逐列吐出的延迟 |
| 累加（`+=`）| K 切段时同槽同位置叠加部分和；N 切块则覆写新槽 |
| `_pending` | 本拍待落盘的写 `(slot,row,col,val)`；`commit` 落盘 |
