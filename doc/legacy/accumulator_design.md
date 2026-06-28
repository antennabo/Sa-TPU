> ⚠ **DEPRECATED** — accumulator 早期设计稿。当前 RTL `rtl/accumulator.sv` 实现概要
> 收进 [../controller_ws.md](../controller_ws.md) 的接口表。本文中 OS drain mux / restore 等
> 内容是 OS 路径产物，OS 不再落地。WS 现实现：per-column 标量地址 SR（长度 AC）+
> per-column sdpram、v1 只 overwrite（无 add）。保留为历史参考；新工作请读 doc/ 根下的新版。

---

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

---

# 第五部分 · RTL 实现 plan（v1）

## 13. 范围

v1 只对齐 sim 现状，对应 WS-1 单 K-chunk：

- **只覆写**，不带 `o_wr_add`。controller 当前没发 `add`（[controller_ws.sv:79-81](../rtl/controller_ws.sv#L79-L81) 只有 `o_wr_vld/o_wr_row/o_wr_tile`），accumulator 端口对齐这一现状。K 切段累加（§9）等 controller 引入 K-chunk 语义后再回头一起补。
- **同步读出口**：`(i_rd_slot, i_rd_row) → o_rd_data[AC]`，1 拍 latency，给 tb / 上层一个最简单的取数路径。streaming dump 不做。
- `cap_delay` 对齐由 controller 完成（[tinytpu_top.sv:60-63](../rtl/tinytpu_top.sv#L60-L63) 注释："已 CAP_DELAY 对齐到 col 0 psum 到达时刻"），accumulator RTL 内的 SR 长度 = `AC`，**只做 0..AC-1 拍的 column skew**（Python `Accumulator(cap_delay=0)` 那条路径）。

## 14. 模块接口

```sv
module accumulator #(
    parameter int AC          = 8,    // 列数（= SA COL_N）
    parameter int OUT_W       = 32,   // psum 位宽
    parameter int NUM_SLOTS   = 16,   // = 1 << TAG_W
    parameter int ROW_MAX     = 32,   // = TILE_NUM_MAX * W；上层算出来传进来
    localparam int SLOT_W     = $clog2(NUM_SLOTS),
    localparam int ROW_W      = $clog2(ROW_MAX)
)(
    input  logic                  clk,
    input  logic                  rst_n,

    // ── 数据面（来自 SA 顶层 o_out / o_out_vld）──
    input  logic [OUT_W-1:0]      i_psum     [AC],
    input  logic                  i_psum_vld [AC],   // 列 c 上 psum 有效

    // ── 地址面（来自 controller，已对齐 col 0）──
    input  logic                  i_wr_vld,          // 标量
    input  logic [ROW_W-1:0]      i_wr_row,          // 标量
    input  logic [SLOT_W-1:0]     i_wr_slot,         // 标量

    // ── 读出口（同步读，1 拍 latency）──
    input  logic                  i_rd_en,
    input  logic [SLOT_W-1:0]     i_rd_slot,
    input  logic [ROW_W-1:0]      i_rd_row,
    output logic [OUT_W-1:0]      o_rd_data  [AC]
);
```

- 数据面用 SA 的 `i_psum_vld[c]` 作为该列 psum 真到达的 gate；写真值的条件 = `i_psum_vld[c] && sr[c].vld`。两套 valid 在硬件上是同源 + 同延迟（都是 col 0 对齐 + 列 c 延 c 拍），冗余的 AND 只是防御性。
- 不暴露 `o_wr_add` 入口（见 §13）。

## 15. 内部数据通路

```
i_wr_vld / i_wr_row / i_wr_slot ──┐
                                  ▼   长度 AC 的 SR（每拍右移）
                            sr[0]──sr[1]──sr[2]── ... ──sr[AC-1]
                              │      │      │              │
                              ▼      ▼      ▼              ▼   列 c 取 sr[c]
                       _mem[slot][row][0..AC-1]   ← 写：i_psum[c] when sr[c].vld
```

- `sr` 元素 = `{vld, row, slot}`，类型用 `struct packed` 或三个并行数组。
- 每拍一律右移；rst_n 时全清 0。
- `sr[c].vld && i_psum_vld[c]` 为真，下一拍把 `i_psum[c]` 写到 `_mem[sr[c].slot][sr[c].row][c]`。

存储用 `logic [OUT_W-1:0] mem [NUM_SLOTS][ROW_MAX][AC]`，让综合工具自行选 distributed RAM 还是 BRAM。第一版不强约束实现。

## 16. 时序

- **写**：第 `t` 拍 controller 注入标量，第 `t+c` 拍写入第 `c` 列（与 sim `cap_delay=0` 行为一致）。无 `_pending`/`commit` 两段；RTL 上写直接落 mem。
- **读**：`i_rd_en` 拉高同拍寄存 `(i_rd_slot, i_rd_row)`，下一拍出 `o_rd_data[AC]`（同步读 BRAM 风格）。
- **读写同 slot/row 同拍**：v1 不保证 read-before-write 还是 write-before-read，调用方避免 hazard；tb 在 `commit done` 之后才发读。

## 17. 集成到 tinytpu_top

- 把 [tinytpu_top.sv](../rtl/tinytpu_top.sv) 里目前 expose 出去的 `o_wr_row/o_wr_tile/o_wr_vld + o_out/o_out_vld` 改成内部连进 `u_accum`。
- 顶层新增读口 `i_rd_en/i_rd_slot/i_rd_row + o_rd_data[AC]`。
- 顶层参数透传：`AC=AC, OUT_W=OUT_W, NUM_SLOTS=(1<<TAG_W), ROW_MAX=TILE_NUM_MAX*W`。

## 18. tb 计划

新建 `tb/accum_tb/`：

1. **单元向量**：直接驱动 `i_psum/i_wr_*`，验证 column skew、覆写、`vld=0` 时不写。
2. **与 controller 串联**：复用现有 `ctrl_ws_tb` 的 scenario，把 controller 的 `o_wr_*` 接进 accumulator，再用读口对照 Python `Accumulator` 的 `_mem`。这一步把 sim 端 ws1 e2e 等价于硬件 ws1 e2e。

集成后 (`tinytpu_top` 含 accum) 的 e2e 对拍放到 `tb/tpu_top_tb/`，对照 [tpu_top_test.py](../simulator/cycle/tests/tpu_top_test.py)。

## 19. 待做（v2+）

| 项 | 触发条件 |
|---|---|
| `o_wr_add` 端口 + `+=` 写路径 | controller 引入 K-chunk 语义，能发 `add=1` |
| OS drain 入口（同 update） | OS 控制器接入 |
| BRAM 实现强约束 / dual-port 控制 | 综合阶段发现 LUT 紧 / 时序紧 |
