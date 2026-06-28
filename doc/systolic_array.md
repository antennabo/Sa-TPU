# Systolic Array 设计（WS）

`rtl/systolic_array.sv` + `rtl/pe.sv` + `rtl/common/weight_buf.sv` 的设计说明。
配套 sim：`simulator/cycle/sim_model/spatial_array.py` + `pe.py`。
前置阅读：[architecture.md](architecture.md) §3 / §4 / §5。

---

## 1. 角色

systolic_array（下文简称 sa）是 AR×AC 个 PE 拼成的矩形网格，**纯数据面**：
- 接收三组边缘信号：`a / a_vld`（左 AR 路）、`b / b_vld / b_rdy`（顶 AC 路）、`b_sw[AR]`（左控制）
- 每拍把上一拍快照 + 本拍边缘注入路由到各 PE，调用 PE 完成乘加
- 从底行输出 `out / out_vld`（每列一个）

sa 不持有任何 slot / row 地址；所有寻址语义在 controller / accumulator 一侧（见
[architecture.md](architecture.md) §4）。

## 2. PE：基本单元

每个 PE 是一个 INT8×INT8 → INT32 的饱和 MAC，参考 [rtl/pe.sv](../rtl/pe.sv)：

```
result = sat(a × b + c)            # SIGNED=1 时走两路补码、有符号饱和
```

关键参数：
- `A_W = B_W = 8`，`OUT_W = 32`
- `PIPE_MUL = 1`（默认）：乘法 1 拍 + 加法/输出寄存 1 拍 = **`LATENCY = 2`**
  `PIPE_MUL = 0`：乘加单拍组合 = `LATENCY = 1`
- `SIGNED = 1`（顶层例化）：饱和到 `[-2^31, 2^31-1]`，`overflow` 标记

PE **无反压、自由运行**：每拍输出 `result_vld`（跟随 `a_vld` 经 LATENCY 拍流水延迟）。
权重 `b` 由所在 PE 的 weight_buf 提供（见 §5），不直接从 sa 边缘读。

## 3. 内部连接

```
   a[r]──▶[a_buf 0]──▶[a_buf 1]──▶ … ──▶[a_buf AC-1]──▶ (drop)
                │            │                  │
                ▼            ▼                  ▼
              PE(r,0)      PE(r,1)        PE(r,AC-1)
                │            │                  │   acc 沿列下流
                ▼            ▼                  ▼
              PE(r+1,0)    PE(r+1,1)     PE(r+1,AC-1)
              ...

   b[c]──▶[wb 0,c]──▶[wb 1,c]──▶ … ──▶[wb AR-1,c]──▶ (沉底，无下游)
   每个 wb = active 寄存器 + shadow data_buf；shadow 沿列下沉填充
```

三条独立的"通路"：
1. **激活 a**：左边缘进入，沿行右传，每跳过一个 PE 经 1 个 `data_buf`（1 深 valid/ready）。
   到 PE(r,c) 时延 c+1 拍。`a_vld` 随数据右传（用 valid 门控 PE 计算）。
2. **权重 b**：顶边缘进入，沿列下沉到对应 PE 的 shadow（详见 §5）。
3. **累加 c**：每 PE 把自己的 `result` 喂给**下一行同列**的 `c`（顶行 `c = 0`）。
   形如 OS 但在 WS 下作用不同：psum 沿列**下流积累**，从底行 `out[c]` 输出 = 整列 K 求和结果。

## 4. b_sw 传播：左边缘注入 + 右推

controller 出 `b_sw[AR]`（每行一个 bool）。sa 内 `b_sw_grid[r][c]` 是 AR×AC 个 FF：
- 第 0 列：`b_sw_grid[r][0] <= b_sw[r]`（左边缘打 1 拍）
- 第 c≥1 列：`b_sw_grid[r][c] <= b_sw_grid[r][c-1]`（每拍右移一格）

于是 controller 在第 `t` 拍注入的 `b_sw[r]`，第 `t + c + 1` 拍到达 PE(r,c)。各行注入时刻
按 controller 的行 stagger SR 错开（行 r 比行 0 延 r 拍）→ 最终到 PE(r,c) 的拍号
= `inject + r + c + 1`，沿**反对角线 `r+c`** 扫过整个阵列。

为什么对角线 = 唯一不出错的 swap 几何，见 §6。

## 5. weight_buf：active + shadow + 沉底反压链

每个 PE 一个 weight_buf（[rtl/common/weight_buf.sv](../rtl/common/weight_buf.sv)）：

```
              i_in_rsv_vld/data       (上邻 shadow → 本级)
              ────────────▶ ┌────────────┐
              ◀──────────── │  shadow    │   1 深 data_buf
              o_in_rsv_rdy  │  (data_buf │
                            │    + clr)  │
                            └─────┬──────┘
                                  │ shadow value
                  i_sw ──────────▶│  swap：
                  (一拍脉冲)       │   active <= shadow
                                  ▼   shadow.i_clr 拉高 → shadow 让出
                            ┌────────────┐
                            │  active    │   寄存器
                            └─────┬──────┘
                                  │ o_work_data → PE.b
              i_out_rsv_rdy ◀───── │
              o_out_rsv_vld/data ─▶│  (本级 shadow → 下邻)
```

**列内沉底反压链**（关键不变式）：
- 顶端 (row 0)：`b_buf_vld/data` 来自 sa 顶边缘 `b_vld[c] / b[c]`（直接接 weight_fifo）
- 底端 (row AR-1)：`b_buf_rdy = 0`（无下游 → 永远不流出 → 沉底）
- 每级 `o_in_rsv_rdy = i_out_rsv_rdy | !shadow.full`：组合 ready 链从底端向顶端传播

效果：
1. 顶边缘新权重 → 第一个空位的 shadow 接收（其他 shadow 满 → 反压给上一级 → 直到找到空位）
2. AR 行依次喂权重 → AR 个 shadow 自底向上填满 → `o_in_rsv_rdy = 0` 反压拉起
3. 顶层 `weight_loaded = !|wfifo_rdy`（所有列反压拉起）= 整套 shadow 已满 → 可触发 `i_sw`
4. `i_sw` 一拍：所有 PE 同时把 shadow 值搬进 active，shadow.i_clr 拉高让出 → 可立刻开始填下一套

**关键好处**：controller 不数 AR 拍、不知道 shadow 是否满，**只观测反压**。权重路完全靠 valid/ready 自握手。
详见 [decisions.md](decisions.md) D4。

## 6. 为什么 swap 必须沿反对角线（而不能整阵列一拍翻）

WS 下激活 `a[r]` 沿行右流（每跳 1 拍），psum 沿列下流。同一个权重 `B[r][c]` 被 `a[m, r]` 用到的拍号
= `m_inject + r + c`（行 skew + 列 skew）。所以每个 PE "用完旧权重 / 新权重首达" 的时刻**取决于 r+c**：

- 旧 tile 最后一次用 PE(r,c)：`(F-1) + r + c`（F 为 feed 行数）
- 新 tile 首达 PE(r,c)：`T + r + c`（T 为新 tile feed 起点）

PE(r,c) 必须在两者之间翻。取 `T = F`（背靠背），翻转时刻 = `T + r + c`，沿**反对角线 r+c** 推进 1 拍 1 格。

若整阵列一拍翻，必须等旧 psum 沿列**全部排空**（约 AR+AC+L 拍）才翻 → 阵列空转 = 气泡。
反对角线波前让每个 PE 各自掐表翻 → 旧 psum 排空波 + 新 tile 填充波在阵列里并存、斜着追 → **零气泡**。

> 推导细节及 2×2 算例见 [legacy/WS_weight_design.md](legacy/WS_weight_design.md) §2（DEPRECATED，但
> 推理过程未变；当前 RTL 的实现细节在 §4-§5）。

## 7. 输出口：底行直接给出

WS 下 psum 沿列下流，到达底行（row AR-1）即整列 K 求和结果。sa 输出 `out[c] = pe_out[AR-1][c]`，
`out_vld[c] = pe_out_vld[AR-1][c]`（PE 的 `result_vld` 跟随 `a_vld` 流水延迟 `LATENCY` 拍）。

顶层 `tinytpu_top` 把 `out / out_vld` 直接接 `accumulator.i_psum / i_psum_vld`，
配上 controller 已对齐过的标量 `o_wr_slot / o_wr_row / o_wr_vld`，写入 `accumulator._mem[slot][row][c]`。
controller 已经把 `wr_*` 信号对齐到 col 0 psum 的拍号；accumulator 内部 SR 把 col c 再延 c 拍即可。

详见 [controller_ws.md](controller_ws.md) §accum 段。

## 8. 维度约束（重申）

- `M = tile_num × W`（`W = AR + LATENCY`，每 tile 喂 W 行激活）
- `K ≤ AR`：单 K-chunk 完整放入阵列；`K > AR` 需要多 K-chunk 切分（v1 暂未支持累加上层 add）
- `N ≤ AC`：单 N-tile；`N > AC` 需要多 session（每 session 不同权重 + 不同 accum slot）

> WS-6 `M < W` 边界、WS-5b `gap` 节奏属未来工作，见 [legacy/WS_weight_design.md](legacy/WS_weight_design.md) §9。

## 9. sim 一一映射

| Python ([spatial_array.py](../simulator/cycle/sim_model/spatial_array.py)) | RTL |
|---|---|
| `_route_a`（a 右传 + a_vld 同行）| `a_buf` 链 + `data_buf` |
| `_route_b`（shadow 列内沉底 + ready 链）| `weight_buf` 串成的反压链 |
| `_route_acc`（acc 下流，顶行注 0）| `pe_c[r][c]`：顶行 = 0，否则 = `pe_out[r-1][c]` |
| `_shift_in(b_sw_grid)`（边缘注入 + 右推）| `b_sw_grid` always_ff 链 |
| `pe.update(a, a_vld, b, ..., acc_in)` | `pe` 例化，输入 `(a, a_vld, b, c)`，输出 `(result, result_vld)` |
