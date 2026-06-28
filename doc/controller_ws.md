# Controller (WS) 设计

`rtl/controller_ws.sv` 的 FSM、控制波、邻接接口设计说明。Python 等价：
`simulator/cycle/sim_model/controller_ws_ref.py`。前置阅读：[architecture.md](architecture.md)。

---

## 1. 角色

controller_ws 把一条 matmul 指令翻译成逐拍信号，驱动 3 个邻居：

| 邻居 | 信号 | 内容 |
|---|---|---|
| systolic_array | `o_weight_sw[AR]` | weight active↔shadow 翻转命令（左边缘注入，sa 内右推）|
| activation_buf | `o_act_ren[AR]` / `o_act_raddr[AR]` | per-lane 读使能 + 读地址（行 stagger）|
| accumulator | `o_acc_wen[AC]` / `o_acc_waddr[AC]` / `o_acc_accen[AC]` / `o_acc_outen[AC]` | per-column 写控制（**controller 内部已 deskew 到列**）|
| accumulator (读) | `o_acc_ren[AC]` / `o_acc_raddr[AC]` | 占位，本指令恒 0，待 STORE 类指令再驱动 |
| weight_fifo | 无 | **不参与**（wfifo↔sa 反压自握手，详见 [decisions.md](decisions.md) D4）|

上层握手输入（指令字段 + 流控）：

| 信号 | 类型 | 含义 |
|---|---|---|
| `i_start` | bool | 上升沿启动一次指令（IDLE→WLOAD）|
| `i_weight_loaded` | bool | sa 反压回馈 = `!|wfifo_rdy`，所有列 shadow 满 |
| `i_activ_available` | bool | 上层确认本指令激活已在 abuf 就位 |
| `i_wtile_num` | `[WTILE_NUM_W]` | 本指令跑几个 weight tile（指令内常量，≥1）|
| `i_act_staddr` | `[ACT_ADDR_W]` | abuf 读起点（**所有 wtile 共用同一份 activation**）|
| `i_acc_staddr` | `[ACC_ADDR_W]` | accumulator 写起点（首个 wtile）|
| `i_feed_num` | `[ACT_ADDR_W]` | 每个 wtile 读多少行 activation = 写多少行 psum（指令内常量，`≥W`）|

旧 `i_switch_weight / i_accum_slot / i_tile_num / i_start_addr` 已全部并入上面 4 项：
- `i_switch_weight` → 内部由 `wtile_idx < i_wtile_num-1` 决定
- `i_accum_slot + tile_num × W` → 平坦化为 `i_acc_staddr + wtile_idx × i_feed_num`
- `i_tile_num × W` 的"session 总 feed 行数" → `i_feed_num` 直接表达
- `i_start_addr` → `i_act_staddr`（且 wtile 之间不变）

## 2. 维度约定（与 [architecture.md](architecture.md) §5 一致）

| 符号 | 公式 | 备注 |
|---|---|---|
| `AR`, `AC` | 编译期参数 | 阵列行 / 列 |
| `LATENCY` | 编译期参数（1 或 2）| PE 流水深度 |
| `W` | `AR + LATENCY` | warmup / drain 拍数；硬件流水深度决定 |
| `wtile_num` | 运行时（指令内常量）| 本指令跑几个 weight tile |
| `feed_num` | 运行时（指令内常量，`≥W`）| 每 wtile feed 行数 |
| `M = wtile_num × feed_num` | — | 单次指令产生的输出总行数 |

> 旧 `F` / `tile_num` 字段已删（[decisions.md](decisions.md) D7 / D8）。
> 约束：`feed_num ≥ W`（否则 CAPTURE 长度变负），`wtile_num ≥ 1`，二者指令内必须稳定。

## 3. 7 态 FSM

每个 wtile 走 FEED(或 OVERLAP) → CAPTURE → OVERLAP/DRAIN 三段；多 wtile 之间通过
OVERLAP 衔接（旧 wtile 的最后 W 行 psum drain 与新 wtile 的 W 行 warmup feed 共拍）。
当 OVERLAP 触发拍 `i_weight_loaded == 0` 时，走 `REWAIT` 临时停泊，等下一份 weight 到位。

### 3.1 状态集

| 状态 | 角色 |
|---|---|
| **IDLE** | 等 `i_start` 上升沿 |
| **WLOAD** | 等 sa shadow 满 + 激活就绪 |
| **FEED** | 纯 warmup 喂活、无 psum 出。两个来源：首 wtile（从 WLOAD cold）、X≥W 的后续 wtile（从 REWAIT cold） |
| **CAPTURE** | 同 wtile 内：继续喂活 + 同时写 psum |
| **OVERLAP** | 旧 wtile drain + 新 wtile warmup-feed **部分重叠**。从 CAPTURE/FEED happy-path 或 REWAIT 早退（X<W）进入 |
| **REWAIT** | 旧 drain 顺势走（前 W 拍写老 psum）+ 等下一份 weight 到位 |
| **DRAIN** | 末 wtile 收尾：停 feed，排完最后 W 行 psum，回 IDLE |

> 关键："OVERLAP 的拍数恒为 W"是硬约束，但**「能写多少」由 wr_row 状态闸**。从 REWAIT cnt=X 早退进的 OVERLAP 只在前 W-X 拍写（剩余 drain），后 X 拍空。

### 3.2 计数器/寄存器

| 名 | 行为 |
|---|---|
| `ws_cnt` | 进新状态置 0；非 IDLE 每拍 +1；REWAIT 也累加（用作 X<W vs X≥W 判定）|
| `wtile_idx` | rst=0；`WLOAD→FEED` 置 0；`OVERLAP→exit` 或 `REWAIT→FEED` 时 **+1** |
| `wr_row` | wtile_idx 变化那拍同步归 0；处于 {CAPTURE, OVERLAP, REWAIT, DRAIN} 且 `wr_row<feed_num` 时 +1；达 `feed_num` 后冻结 (cap) |
| `offset[c]` | rst=0；boundary 或 cold 那拍归 0；`lane_fire[c]` 时 +1，`cap=feed_num` 自然回卷 |

派生：
- `last_wtile = (wtile_idx == wtile_num - 1)`
- `last_wtile_next = (wtile_idx == wtile_num - 2)` （OVERLAP 末拍判定，递增**前**值）
- `cap_last = feed_num - W - 1`（CAPTURE 末拍）
- `wr_vld_internal = state ∈ {CAPTURE, OVERLAP, REWAIT, DRAIN} && wr_row < feed_num`

### 3.3 完整转移表

| 当前 | 触发条件 | 下一 | wtile_idx | wr_row | inject | offset_rst |
|---|---|---|---|---|---|---|
| IDLE | `start_edge` | WLOAD | hold | hold | — | — |
| WLOAD | `weight_loaded & activ_available` | FEED | =0 | =0 | cold | — |
| FEED | `cnt==W-1 & feed_num>W` | CAPTURE | hold | hold | — | — |
| FEED | `cnt==W-1 & feed_num==W & last_wtile` | DRAIN | hold | hold | — | — |
| FEED | `cnt==W-1 & feed_num==W & !last_wtile & weight_loaded` | OVERLAP | hold | hold | boundary | — |
| FEED | `cnt==W-1 & feed_num==W & !last_wtile & !weight_loaded` | REWAIT | hold | hold | — | — |
| CAPTURE | `cnt==cap_last & last_wtile` | DRAIN | hold | hold | — | — |
| CAPTURE | `cnt==cap_last & !last_wtile & weight_loaded` | OVERLAP | hold | hold | boundary | — |
| CAPTURE | `cnt==cap_last & !last_wtile & !weight_loaded` | REWAIT | hold | hold | — | — |
| OVERLAP | `cnt==W-1 & feed_num>W` | CAPTURE | **+1** | **=0** | — | — |
| OVERLAP | `cnt==W-1 & feed_num==W & last_wtile_next` | DRAIN | **+1** | **=0** | — | — |
| OVERLAP | `cnt==W-1 & feed_num==W & !last_wtile_next & weight_loaded` | OVERLAP | **+1** | **=0** | boundary | — |
| OVERLAP | `cnt==W-1 & feed_num==W & !last_wtile_next & !weight_loaded` | REWAIT | **+1** | **=0** | — | — |
| REWAIT | `weight_loaded & cnt<W` （旧 drain 未完，早退）| OVERLAP | hold | hold | boundary | ✓ |
| REWAIT | `weight_loaded & cnt≥W` （旧 drain 已完）| FEED | **+1** | **=0** | cold | ✓ |
| DRAIN | `cnt==W-1` | IDLE | hold | hold | — | — |

> **offset_rst 只在 REWAIT 退出时拉起**。其他过渡（cold WLOAD→FEED, 各种 boundary→OVERLAP）
> 各 lane 是连续 firing 的, 自然 wrap 已经处理对行 stagger, 强 reset 反而会破坏 stagger。

### 3.4 inject 定义

```
cold     = (state==WLOAD || state==REWAIT) && state_next==FEED
boundary = (state_next==OVERLAP) && (
              (state==FEED    && feed_last)        # FEED end (feed_num==W path)
           || (state==CAPTURE && cap_last)         # CAP end → OVL
           || (state==REWAIT)                       # REWAIT exit (early)
           || (state==OVERLAP && ovl_last)         # OVL→OVL Case E inter-wtile
           )                                       # 1-cycle pulse, NOT held throughout OVERLAP
inject   = cold | boundary             # 喂给 o_weight_sw[0]
```

> **关键**：boundary 必须是 1 拍脉冲，对应 wtile 切换那一拍。如果按 `state_next==OVERLAP` 单条件
> 写，OVERLAP 中段每拍 state==state_next==OVERLAP 都会 fire，导致 weight_sw[0] 变成持续高
> 电平 → SA 误以为多次 swap → b_sw 波形错乱。

REWAIT→FEED 走 **cold**（从空闲流水重启）；REWAIT→OVERLAP 走 **boundary**（部分重叠切换）。

> 单次指令端到端时长（不含 WLOAD、无 REWAIT 拉长）= `wtile_num × feed_num + W` 拍。
> 每命中一次 REWAIT 额外加 X 拍（X = REWAIT 内等到 weight_loaded ↑ 的拍数, X≥1）。

## 4. 四道控制波

### 4.1 `o_weight_sw[AR]` — weight swap 命令

inject 定义同 §3.4：

```
cold     = (state==WLOAD || state==REWAIT) && state_next==FEED
boundary = (state ∈ {FEED, CAPTURE, OVERLAP, REWAIT}) && state_next==OVERLAP
inject   = cold | boundary
o_weight_sw[0]   <= inject                # 左边缘 FF
o_weight_sw[r≥1] <= o_weight_sw[r-1]      # 行 stagger SR
```

sa 内部 `b_sw_grid` 再每拍右推 1 格 + 第 0 列额外打 1 拍 FF（详见
[systolic_array.md](systolic_array.md) §4），PE(r,c) 在 `inject + r + c + 1` 拍翻转。

### 4.2 `o_act_ren[AR]` / `o_act_raddr[AR]` — abuf 读

行 stagger："lane c+1 比 lane c 晚 1 拍开始"，与 a 通路在 sa 内的右流速率一致：

```
lane_fire[0]    = out_feed_next                     # 内部 feed 节拍 (组合)
lane_fire[c≥1]  = o_act_ren[c-1]                    # 把 o_act_ren 本身当 SR 用

o_act_ren[c]    <= lane_fire[c]                     # 下拍输出
o_act_raddr[c]  <= i_act_staddr + offset[c]
```

`offset[c]` 自加 + 自然回卷，**只在 REWAIT 退出时强 reset**：
```
cap         = i_feed_num
offset_inc  = (offset[c]==cap-1) ? 0 : offset[c]+1
offset[c]   ← lane_fire[c] ? offset_inc : offset[c]
offset_rst  = (state == REWAIT) && (state_next ∈ {OVERLAP, FEED})
              那拍 mux:
                {rst=1, fire=1}: offset[c] ← 1   (lane 这拍读 row 0, 下拍接 row 1)
                {rst=1, fire=0}: offset[c] ← 0   (lane c≥1 还没轮到)
```

效果：
- 单 wtile：`offset[c]` 从 0 自加到 `feed_num-1`，自然完成一段 feed
- 多 wtile happy path（CAPTURE→OVERLAP / OVERLAP→OVERLAP / WLOAD→FEED 等）：
  各 lane 连续 firing, 自然 wrap (offset==feed_num-1 → 0) 在每 lane 各自 timing 上分别
  发生 → 行 stagger 保住。**不**额外 reset
- REWAIT 退出 (cnt<W→OVERLAP 早退 / cnt≥W→FEED)：REWAIT 期间 lanes 停 fire,
  offset 冻结在非 0 值；退出那拍强 reset 让新 wtile 从 row 0 干净开始

### 4.3 `o_acc_wen[AC] / o_acc_waddr[AC] / o_acc_accen[AC] / o_acc_outen[AC]` — accumulator 写

controller 端内部做 per-column deskew SR：标量信号在 col-0 节奏算出，
通过长 AC-1 的 SR 链产生 col c 的版本，已经对齐 sa col c 的 psum 出口节拍。

```
# 标量 (col-0 节奏)
wr_vld_scalar  = state ∈ {CAPTURE, OVERLAP, REWAIT, DRAIN} && wr_row < feed_num
                                          # 由 wr_row 闸: REWAIT 中达 feed_num 后自动停, 无需状态特化
wr_row_scalar  = wr_row (定义见 §3.2)
tile_acc_base  = i_acc_staddr + wtile_idx × i_feed_num   # wtile_idx 变化时同拍重算
acc_addr_scalar= tile_acc_base + wr_row_scalar

# per-column SR (col 0 直用, col c≥1 用 q[c-1])
o_acc_wen[0]    <= wr_vld_scalar;       o_acc_wen[c]    <= o_acc_wen[c-1]
o_acc_waddr[0]  <= acc_addr_scalar;     o_acc_waddr[c]  <= o_acc_waddr[c-1]
o_acc_accen[*]  <= 1'b0                 # 本指令各 wtile 写独立区间, 不累加
o_acc_outen[c]  <= o_acc_wen[c]         # outen = 最终结果 publish 信号
                                        # 本指令 acc_en=0 各拍写入即最终, 故 outen = wen
                                        # 未来 K-tiling 指令需要区分 outen ≠ wen
```

> per-column deskew 的责任从 accumulator 上移到 controller（[decisions.md](decisions.md) D8
> 失效 D6）。好处：accumulator 退化为纯 per-AC 收信，slot 概念取消，地址平坦化为单一
> `ACC_ADDR_W`。

### 4.4 `o_acc_ren[AC] / o_acc_raddr[AC]` — accumulator 读

本指令（MATRIX_MULTIPLY）不驱动读口，恒置 0。STORE / READOUT 类指令再补 FSM 扩展。

## 5. 邻居模块接口

### 5.1 activation_buf

[rtl/activation_buf.sv](../rtl/activation_buf.sv) — **纯被动 per-lane RAM**（无控制状态、无 page、无 skew）。

```
写口：per-lane (i_wr_en[c], i_wr_addr[c], i_wr_data[c])     ← 外部驱动（DMA / 测试 TB）
读口：per-lane (i_rd_en[c], i_rd_addr[c])                   ← controller `o_act_ren/o_act_raddr`
     → o_rd_data[c]（组合从 sdpram 读，1 拍内可见 i_rd_en 决定的 vld）
     → o_rd_vld[c] = i_rd_en[c] 打 1 拍 FF
```

"双 page" 通过外部地址高位实现（DEPTH 设两倍，把 page bit 拼在 wr_addr/rd_addr 高位）。
controller 不持 page 状态，只管 `i_act_staddr + offset[c]`。

### 5.2 weight_fifo

[rtl/weight_fifo.sv](../rtl/weight_fifo.sv) — AC 个独立 sync_fifo lane + 同步广播写。

```
写口：i_wvalid + i_wdata[AC] → 各 lane 同时入队
     o_wfull = OR(per-lane full)；wr_eff = i_wvalid & !any_full
读口：每 lane 独立 valid/ready，与 sa 顶边 b/b_vld/b_rdy 对接
     o_vld[c] = !empty[c] & i_ready[c]
```

写口对外暴露在 tinytpu_top（外部 DMA 同步广播一行 AC 条权重）。读口直接对接 sa，
**controller 不门控**。多 wtile 时，上层必须保证 OVERLAP 触发前下一份权重已陆续到位
（详见 [decisions.md](decisions.md) D4）。

### 5.3 accumulator

[rtl/accumulator.sv](../rtl/accumulator.sv) — 纯 per-column 写入 + per-column sdpram。

```
数据面：i_psum[AC] / i_psum_vld[AC]               ← sa.out / sa.out_vld
地址面：i_wr_vld[AC] / i_wr_addr[AC]              ← controller 已 deskew 的 per-column
        / i_acc_en[AC] / i_out_en[AC]
存储：per-column sdpram，地址 = ACC_ADDR_W 位平坦地址（无 slot/row 二级结构）
每列写：mem[c][i_wr_addr[c]] = i_psum[c]  当 i_wr_vld[c] & i_psum_vld[c]
```

读口：per-column `i_rd_en[c]` / `i_rd_addr[c]`，由上层直驱（暂不经过 controller）。
**v1 只覆写**（`i_acc_en` 当前恒 0），无 K 切段累加，详见 [decisions.md](decisions.md) D8。

## 6. 时序 trace

### 6.1 Happy path: `AR=2, LATENCY=2, W=4, wtile_num=2, feed_num=8` (i_weight_loaded 始终高)

```
                wtile_idx=0                          wtile_idx 在 ovl_last 递增
                ┌── FEED ───┐┌── CAPTURE ────────┐┌── OVERLAP ──┐
拍号:        0    1    2    3    4    5    6    7    8    9   10   11
o_feed       1    1    1    1    1    1    1    1    1    1    1    1
lane_fire[0] 1    1    1    1    1    1    1    1    1    1    1    1
o_act_ren[0] 0    1    1    1    1    1    1    1    1    1    1    1
o_act_raddr  0    0    1    2    3    4    5    6    7    0    1    2
                                                          ↑ wtile_jump (ovl_last)
                                                            offset 归 0, 重新读

                wtile_idx=1
                ┌── CAPTURE ──────────┐┌── DRAIN ──────────┐
拍号:       12   13   14   15   16   17   18   19
o_feed       1    1    1    1    0    0    0    0      # DRAIN 停 feed
lane_fire[0] 1    1    1    1    0    0    0    0
o_act_ren[0] 1    1    1    1    1    0    0    0
o_act_raddr  3    4    5    6    7    0    0    0
```

每 lane 各自 `offset[c]`，独立自加，`cap = feed_num` 回卷。
跨 wtile 时 controller 内部 `wtile_jump` 触发 `offset` 归 0。

accum 写（col 0 视角，已 deskew）：

```
拍号:        0    1    2    3    4    5    6    7    8    9   10   11   12 ...   19
wr_vld[0]    0    0    0    0    1    1    1    1    1    1    1    1    1 ...   1
tile_acc_base                       0    0    0    0    0    0    0    0    8 ...   8
                                                                       ↑ ovl_last 递增 wtile_idx → base 跳到 8
wr_row[0]    -    -    -    -    0    1    2    3    4    5    6    7    0 ...   7
o_acc_waddr  -    -    -    -    0    1    2    3    4    5    6    7    8 ...  15
```

### 6.2 慢路径 trace（同 Case D, weight_loaded 在 CAPTURE 末拍掉低）

**X=2 < W**: REWAIT 走 2 拍即早退至 OVERLAP

| 拍 | state | cnt | wtile_idx | wr_row | wr_vld | lane_fire[0] | inject |
|---|---|---|---|---|---|---|---|
| 0..3 | FEED | 0..3 | 0 | 0 | 0 | 1 | cold@0 |
| 4..7 | CAPTURE | 0..3 (cap_last=3) | 0 | 0..3 | 1 | 1 | — |
| 8 | REWAIT | 0 | 0 | 4 | 1 | 0 | — |
| 9 | REWAIT | 1 | 0 | 5 | 1 | 0 | — |
| 10 | REWAIT | 2 (w↑) | 0 | 6 | 1 | 0 | — |
| 11 | OVERLAP | 0 | 0 | 7 | 1 | 1 | bnd@11 |
| 12..14 | OVERLAP | 1..3 | 0 | 8 (cap) | 0 | 1 | — |
| 15 | CAPTURE | 0 | **1** | **0** | 1 | 1 | — |
| ... continues wtile 1 ... |

wtile 0 共写 8 行：CAPTURE(4) + REWAIT(3) + OVERLAP(1) = 8。boundary@11 翻 wtile 1 weight。

**X=6 ≥ W**: REWAIT 跑完 drain 后继续等，最终走 cold→FEED

| 拍 | state | cnt | wtile_idx | wr_row | wr_vld | lane_fire[0] | inject |
|---|---|---|---|---|---|---|---|
| 0..3 | FEED | 0..3 | 0 | 0 | 0 | 1 | cold@0 |
| 4..7 | CAPTURE | 0..3 | 0 | 0..3 | 1 | 1 | — |
| 8..11 | REWAIT | 0..3 | 0 | 4..7 | 1 | 0 | — |
| 12..13 | REWAIT | 4..5 | 0 | 8 (cap) | 0 | 0 | — |
| 14 | REWAIT | 6 (w↑) | 0 | 8 | 0 | 0 | — |
| 15 | FEED | 0 | **1** | **0** | 0 | 1 | cold@15 |
| 16..18 | FEED | 1..3 | 1 | 0 | 0 | 1 | — |
| 19 | CAPTURE | 0 | 1 | 0 | 1 | 1 | — |
| ... continues wtile 1 ... |

wtile 0 共写 8 行：CAPTURE(4) + REWAIT(4) = 8。cold@15 重启流水切 wtile 1。

> 命中 REWAIT 的额外开销 = X 拍（X≥1）。变体 II 早退（X<W）相比"必跑满 W 再 FEED"的方案最多省 `W-X` 拍。

## 7. 延迟参数

PE `LATENCY`（1 或 2）是根参数，其余从它推：

| 名称 | 公式 | 用途 |
|---|---|---|
| `W` | `AR + LATENCY` | warmup / drain 拍数（FSM 阈值）|
| CAP_DELAY | `LATENCY + 1` | sa 顶边喂 b → 底行 out_vld 总拍数；controller 内部 `wr_vld_scalar` 注入与 sa col 0 psum 同拍到 accum |

> CAP_DELAY 在 RTL 里隐含于 controller 对 `out_active_next` 的判定时序（FF 采 state_next）。
> Python ref 用同样的两段式时序复刻。无须显式延迟参数。

## 8. sim ↔ RTL 映射

| Python ([controller_ws_ref.py](../simulator/cycle/sim_model/controller_ws_ref.py)) | RTL ([controller_ws.sv](../rtl/controller_ws.sv)) |
|---|---|
| `__init__(AR, AC, LATENCY, WTILE_NUM_MAX, ACT_ADDR_W, ACC_ADDR_W)` | `parameter` / `localparam` |
| `update(start, weight_loaded, activ_available, wtile_num, act_staddr, acc_staddr, feed_num)` | module IO 端口 |
| `_*_next` 字段 + `_next_ws_state` | `always_comb`（`ws_state_next` 等）|
| `commit()` 落 `ws_state` 等 | `always_ff @posedge clk`（每组寄存器一个 always_ff）|
| 7 态 FSM + cnt 阈值 + REWAIT 双分支 + per-column deskew SR | 一一对应 |

差异（按 RTL 自然结构，不机械翻译 Python）：
- Python 用 `_*_next` 影子字段；RTL 直接 `state_next` wire + 单一 FF
- Python 用大 if/else 链生成各信号；RTL 拆成多个 `always_ff`
- `o_act_ren` / `o_acc_wen` / `o_acc_waddr` 等 SR 在 RTL 当 SR 用（lane_fire 通过本体右推），Python 单独维护 SR

## 9. 关联 doc

- 系统总览 → [architecture.md](architecture.md)
- sa 内 b_sw / 权重双缓冲机制 → [systolic_array.md](systolic_array.md)
- 决策日志（F 删除、shadow 反压、per-column deskew 上移等）→ [decisions.md](decisions.md)
