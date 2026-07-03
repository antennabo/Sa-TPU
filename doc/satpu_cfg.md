# satpu_cfg — 顶层配置寄存器模块

`rtl/satpu_cfg.sv`（由 [rtl/cfg/script/gen_cfg.py](../rtl/cfg/script/gen_cfg.py) 从
[rtl/cfg/satpu_cfg.yaml](../rtl/cfg/satpu_cfg.yaml) 生成）。

> 状态：**spec 草案 (2026-06-28)**。yaml / sv / `satpu_top` 改造未提交，验收节点见 §6 实施清单。

---

## 1. 角色

在 `satpu_top` 内例化的 SAB (Simple Access Bus) 从端，把原来散在顶层的所有 raw 端口（指令字段 / abuf 写口 /
wfifo 写口 / accum 读口 / 状态）收敛到**单一 SAB 总线** + `clk` + `rst_n`。结果：
`satpu_top` 对外只剩 7 根有效信号，CPU 通过 SAB 单字访问完成 "灌数据 → 配指令 → 启动 →
轮询 → 读回结果" 的全流程，下一步可直接套 APB 桥（roadmap 步 1 终态）。

替代方案是顶层散端口 + 外部专用胶水：拒绝，每加一条 ISA 字段都要改 satpu_top 端口表 + 顶层例化。

---

## 2. 接口

### 2.1 `satpu_top` 改造后对外端口

| 信号 | 方向 | 宽度 | 说明 |
|---|---|---|---|
| `clk` | in | 1 | 系统时钟 |
| `rst_n` | in | 1 | 异步低有效复位 |
| `sab_valid` | in | 1 | 主端请求有效 |
| `sab_wen` | in | 1 | 1=写, 0=读 |
| `sab_addr` | in | `ADDR_W` | word-地址（每槽 1×`DATA_W` 位）|
| `sab_wdata` | in | `DATA_W` | 写数据 |
| `sab_ready` | out | 1 | 应答（1 拍, 紧随 `sab_valid` 上升沿）|
| `sab_rdata` | out | `DATA_W` | 读数据 |

参数：`ADDR_W = 15`（32K 槽; 见 §3 段选位推导）, `DATA_W = 32`。

### 2.2 `satpu_cfg` 对内端口（送给 satpu_top 胶水层）

| 端口组 | 方向 | 说明 |
|---|---|---|
| `start` | out 1b | RW，**电平**输出；顶层胶水做上升沿检测得 1 拍脉冲 (§5.5) |
| `activ_avail` | out 1b | RW，电平直送 controller `i_activ_available` |
| `wtile_num` / `act_staddr` / `acc_staddr` / `feed_num` | out | RW，4 个指令字段 |
| `ws_state` / `wfifo_full` | in | RO 状态回看 |
| `abuf_{addr,wdata,wen}` | out | RAM_WO 段, alen=13 (4 位预留 + 3 行 + 8 字节; 当前 row/byte 占低 11 位)|
| `wfifo_{addr,wdata,wen}` | out | RAM_WO 段, alen=3 (列 idx) |
| `accum_{addr,ren,rdata}` | out / out / in | RAM_RO 段, alen=13 (3 列 + 10 行) |

sab 总线时序：握手沿 = `sab_valid` 上升边；`sab_ready` 在下一拍拉高 1 拍。
读写在同一拍下发；RAM_RO 读延迟见 §6.2。

---

## 3. 寄存器映射

地址 word-寻址（每地址 1 个 32-bit 槽）。所有 reset 值 = 0。低 N 位以外字段读回 0。

**地址空间 (`ADDR_W = 16`, 64K)**：ABUF (16K, alen=14) + ACCUM (8K, alen=13)
加上控制区，段选靠 base 高位前缀匹配 (`sab_addr[15:alen] == base[15:alen]`)：

| 范围 | 段 | 前缀匹配 |
|---|---|---|
| 0x0000–0x000F | 控制寄存器 | exact addr |
| 0x0010–0x0017 | `WFIFO` (RAM_WO, alen=3) | `sab_addr[15:3] == 13'h2` |
| 0x2000–0x3FFF | `ACCUM` (RAM_RO, alen=13) | `sab_addr[15:13] == 3'b001` |
| 0x4000–0x7FFF | `ABUF` (RAM_WO, alen=14) | `sab_addr[15:14] == 2'b01` |
| 0x8000–0xFFFF | 保留 (32K) | — |

### 3.1 控制区 0x0000–0x000F

| 地址 | 名 | 类型 | 字段 | 语义 |
|---|---|---|---|---|
| 0x0000 | `START` | RW | `[0]` start | 写 1 → 顶层取上升沿出 1 拍 i_start 脉冲 (§5.5)；写 0 复位准备下次启动 |
| 0x0001 | `ACTIV_AVAIL` | RW | `[0]` activ_avail | 1 = abuf 已就绪可读；CPU 在灌完 abuf 后置 1 |
| 0x0002 | `WTILE_NUM` | RW | `[7:0]` wtile_num | 单指令跑几个 weight tile（≥1，≤`WTILE_NUM_MAX=128`）|
| 0x0003 | `ACT_STADDR` | RW | `[10:0]` act_staddr | abuf 读起点（所有 wtile 共用）|
| 0x0004 | `ACC_STADDR` | RW | `[9:0]` acc_staddr | accumulator 写起点（首 wtile）|
| 0x0005 | `FEED_NUM` | RW | `[11:0]` feed_num | 每 wtile feed 行数（≥`W = AR+LATENCY = 10`）|
| 0x0006 | `STATUS` | RO | `[2:0]` ws_state, `[3]` wfifo_full | 轮询 |

字段宽度对应当前顶层 localparam（`ABUF_DEPTH=2048` → `ACT_ADDR_W=11`;
`ACCUM_DEPTH=1024` → `ACC_ADDR_W=10`; `FEED_NUM_W=ACT_ADDR_W+1=12`;
`WTILE_NUM_MAX=128` → `WTILE_NUM_W=8`）。改顶层参数时 yaml 字段宽度须同步。

### 3.2 数据区 — RAM 段（多地址范围）

| 段 | 基址 | alen | dlen | 范围 | 段地址语义 |
|---|---|---|---|---|---|
| `WFIFO` | 0x0010 | 3 | 8 | 0x0010–0x0017 | `addr[2:0]` = 列 idx (0..7); 写 1 字节进对应列 wfifo |
| `ACCUM` | 0x2000 | 13 | 32 | 0x2000–0x3FFF | `addr[ACC_ADDR_W +: LANE_W]` = 列 idx (0..7), `addr[ACC_ADDR_W-1:0]` = accumulator 行地址 |
| `ABUF` | 0x4000 | 14 | 8 | 0x4000–0x7FFF | `addr[ACT_ADDR_W +: LANE_W]` = lane idx (0..7), `addr[ACT_ADDR_W-1:0]` = lane 内字节 |

段地址由 `sab_addr` 高 `ADDR_W - alen` 位前缀匹配选中 (gen_cfg.py 现有占位 bug，见 §4)。
段内地址 = `sab_addr[alen-1:0]` 直送内部；预留位由顶层胶水忽略 (软件纪律: 当前周期不写预留区)。

### 3.3 数据 layout — 从 A/B/Y 数学轴到 RAM 地址的映射

**这是软件灌数据时最容易踩坑的点**。硬件的 axis 分配跟标准 numpy 直觉相反 —
下表是权威定义。tb / driver / 编译器都必须按此 layout 灌数据。

矩阵约定：`Y[M][N] = A[M][K] × B[K][N]`（数学层面标准 matmul）。
硬件里 WS SA 的实际存储：

| 段 | 段内 axis | 对应数学 axis | 备注 |
|---|---|---|---|
| `ABUF` | `addr[ACT_ADDR_W +: LANE_W]` (高 3 bit = `[13:11]` 当前) = row | **K** (reduction) | 每 K 一行 |
| `ABUF` | `addr[ACT_ADDR_W-1:0]` (低 11 bit = `[10:0]` 当前) = byte | **M** (activation batch) | AR lane 一字节 |
| `WFIFO` | `addr[2:0]` = col | **N** (output col) | 每 col 一个 FIFO |
| `WFIFO` | push 顺序 | K 反序 | **K=K-1 先 push, K=0 最后 push** |
| `ACCUM` | `addr[ACC_ADDR_W +: LANE_W]` (高 3 bit = `[12:10]` 当前) = col | **N** | 读回同 col |
| `ACCUM` | `addr[ACC_ADDR_W-1:0]` (低 10 bit = `[9:0]` 当前) = row | **M** | 读回同 M |

**换个说法**：ABUF 里存的其实是 A 的**转置** `A^T[K][M]`；WFIFO push
的顺序对 K 是**倒着来**的。这是 WS SA 的 preload 语义决定的（weight 从
FIFO shift 进 SA 时，最先 shift 的应该停在最靠近 psum 起点的 PE 行）。

对拍公式（灌完按此手算，`o_ram_data` 就应该匹配）：

```
Y[m][n] = Σ_{k=0..K-1} A[k][m] * B[k][n]
```

小例（N=2, K=2, M=2, 灌字节 1..4 到 ABUF, 5..8 到 WFIFO）：

```
灌 ABUF:                          灌 WFIFO (per col, K 反序):
  [row=0,byte=0]=1                  col=0 push: 5 (→B[K=1][0]), 7 (→B[K=0][0])
  [row=0,byte=1]=2                  col=1 push: 6 (→B[K=1][1]), 8 (→B[K=0][1])
  [row=1,byte=0]=3
  [row=1,byte=1]=4

→ A[K=0][M=0]=1, A[K=0][M=1]=2, A[K=1][M=0]=3, A[K=1][M=1]=4
→ B[K=0][N=0]=7, B[K=0][N=1]=8, B[K=1][N=0]=5, B[K=1][N=1]=6

Y[0][0] = 1*7 + 3*5 = 22    Y[0][1] = 1*8 + 3*6 = 26
Y[1][0] = 2*7 + 4*5 = 34    Y[1][1] = 2*8 + 4*6 = 40
```

### 3.4 不暴露的内部信号

- controller `i_weight_loaded`：由 satpu_top 内 `!|wfifo_rdy` 自推（与现实现一致），CPU 不可见。
- `o_weight_sw / o_acc_*[AC] / o_act_*[AR]`：纯内部数据通路，cfg 不开口。

---

## 4. gen_cfg.py 占位 bug 修补 + `alen=0` 单槽支持

### 4.1 占位 bug

[gen_cfg.py:225-228](../rtl/cfg/script/gen_cfg.py#L225-L228) RAM sel 分支当前**两路字符串相同**:

```python
if alen < addr_w:
    sel_cond = f"sab_addr == {base}"   # ← 跟 else 完全一样, 即全位精确比较
else:
    sel_cond = f"sab_addr == {base}"
```

`alen < addr_w` 分支应该是前缀比较，被留成占位。本模块 13-bit alen 段必须修。

### 4.2 三档 alen 语义（修补后）

| alen | 用途 | sel 表达式 | addr 输出端口 |
|---|---|---|---|
| `0` | 单槽 RAM（FIFO 类，地址只用于触发 push/pop）| `sab_addr == BASE` 全位 | **不生成** `{name}_addr` |
| `0 < alen < ADDR_W` | 多槽 RAM 段（abuf / accum 用）| `sab_addr[ADDR_W-1:alen] == BASE[ADDR_W-1:alen]` 前缀 | `sab_addr[alen-1:0]` |
| `alen == ADDR_W` | RAM 占满整段（理论上）| 全位比较（恒真但保留对称）| `sab_addr` 全位 |

### 4.3 改动清单

- `gen_cfg.py` RAM 段 `sel_cond` 三档分支补齐（§4.2）
- `gen_cfg.py` 端口输出/读 mux 处对 `alen==0` 跳过 `{name}_addr` 生成
- [rtl/cfg/uart_cfg.yaml](../rtl/cfg/uart_cfg.yaml) `TX_DATA` / `RX_DATA` 从 `alen: 1` 改 `alen: 0`
  (uart 这两个本就是 FIFO 触发地址, 没有真正的内部地址用法)
- 验收：uart 重生成 → uart_cfg_tb 全 pass

WO_PULSE / 边沿检测**不进 generator**, 全部移交顶层胶水 (§5.5)。

---

## 5. 顶层胶水（satpu_top 改造）

`satpu_top` 内净留：cfg 例化 + 数据面子模块例化 + RAM 段 demux/mux。

### 5.1 abuf 写口 demux（cfg → AR 路）

cfg 段 alen=13, 当前只用低 11 位 (`[10:8]=row, [7:0]=byte`), `[12:11]` 预留:

```sv
for (int r = 0; r < AR; r++) begin : g_abuf_wr
    abuf_wr_en[r]   = cfg_abuf_wen && (cfg_abuf_addr[10:8] == r[2:0]);
    abuf_wr_addr[r] = cfg_abuf_addr[7:0];
    abuf_wr_data[r] = cfg_abuf_wdata;
end
```

### 5.2 wfifo 写口 demux（cfg → AC 路, 替代旧广播）

```sv
for (int c = 0; c < AC; c++) begin : g_wfifo_wr
    wfifo_wr_en[c] = cfg_wfifo_wen && (cfg_wfifo_addr == c[2:0]) && !wfifo_full[c];
    wfifo_wdata[c] = cfg_wfifo_wdata;
end
```

每次 sab 写一字节进一列。CPU 串行扫 8 列推完一行权重再下一行。
`wfifo_full` 走 STATUS（CPU 轮询防丢字），不阻塞 sab_ready。

### 5.3 accum 读口 mux

```sv
for (int c = 0; c < AC; c++) begin : g_accum_rd
    accum_rd_en[c]   = cfg_accum_ren && (cfg_accum_addr[12:10] == c[2:0]);
    accum_rd_addr[c] = cfg_accum_addr[9:0];
end
always_comb begin
    cfg_accum_rdata = '0;
    for (int c = 0; c < AC; c++)
        if (cfg_accum_addr[12:10] == c[2:0]) cfg_accum_rdata = accum_rd_data[c];
end
```

### 5.4 STATUS / activ_avail 直连

`ws_state` 来自 controller `o_ws_state`；`wfifo_full = |wfifo_full_per_col`。
`activ_avail` / 4 指令字段从 cfg 直接连 controller `i_*` 端口。

### 5.5 START 上升沿 → 1 拍脉冲（顶层胶水, gen_cfg 不参与）

```sv
logic cfg_start_d;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) cfg_start_d <= 1'b0;
    else        cfg_start_d <= cfg_start;
end
assign ctrl_i_start = cfg_start & ~cfg_start_d;
```

软件契约: 写 `0x0000 ← 1` 触发一次启动; 同一次任务结束后必须写 `0x0000 ← 0` 再写 1 才能下一次启动
(否则 `cfg_start_d` 已为 1, 不再有上升沿)。

---

## 6. 时序 / 不变式

### 6.1 CPU 使用流程

```
1. for each (row, byte_addr):                  write 0x2000 | (row<<8) | addr ← byte
2. for each (weight_row, col):                 wait STATUS[3]=0; write 0x0010 | col ← byte
3. write 0x0002~0x0005                         配 wtile_num / act_staddr / acc_staddr / feed_num
4. write 0x0001 ← 1                            ACTIV_AVAIL := 1
5. write 0x0000 ← 0; write 0x0000 ← 1          复位 START → 拉高 (产生上升沿脉冲)
6. poll 0x0006 until STATUS[2:0]==IDLE         轮询 ws_state == 3'b000
7. for each (col, addr): read 0x4000 | (col<<10) | addr  收结果
```

### 6.2 RAM_RO 读延迟（已知 timing gap）

`accumulator.o_rd_data` 注释明确 **`1stg (sdpram rdata)`** = 1 拍后才有效
（[rtl/accumulator.sv:45](../rtl/accumulator.sv#L45)）。当前 gen_cfg 生成的读 FF 在
`sab_valid` 上升沿后 1 拍即采 rdata，对 accumulator 会采到旧值。

**实施期二选一**（先不改 generator，留给 §7 验收）：

- (a) 把 `accumulator` 内 sdpram `REG_OUT` 改 0（组合读），与 `activation_buf` 一致；零额外延迟
- (b) gen_cfg 给 RAM_RO 段加 1 拍 `sab_ready` 延迟：写 always_ff 时分支判断 `{name}_sel` 多打 1 拍

倾向 (a)：accumulator 单口、4 KB 量级，组合读时序不紧。

### 6.3 不变式

| # | 不变式 | 出处 |
|---|---|---|
| I1 | sab 主端在看到 `sab_ready=1` 那拍 sample `sab_rdata`；其它拍读数据未定义 | gen_cfg 现行约定 |
| I2 | RAM_WO 段每次只写 dlen 位，alen 位定位；满段需 N 次 sab 写 | §3.2 |
| I3 | wfifo 推满后 `wfifo_full=1`，CPU 必须先轮询 STATUS[3] 再推 | §5.2 |
| I4 | `wtile_num ≥ 1`、`feed_num ≥ W`、4 字段在 START 之后 / 下次 IDLE 之前**不得改写** | controller_ws spec |

### 6.4 边界 / 不支持

- DMA / burst 写：本模块不做，单字节单字节 PIO（见 [scope.md](scope.md) §1 MVP）
- 中断 / IRQ：当前 STATUS 轮询；后续要 INT 类型再扩 yaml
- 并发：sab 主端一拍一事务，cfg 内无 outstanding 队列

---

## 7. 实施清单

| 步 | 改动 | 验收 |
|---|---|---|
| 1 | `gen_cfg.py` 修补 §4 一行 RAM sel 前缀比较 + 必要时为 uart 的 alen=1 留特例 | uart 重生成 + uart_cfg_tb 全 pass |
| 2 | 决定 §6.2 (a)/(b)，执行 accumulator REG_OUT 改 0 或 gen_cfg 二段 ready | 单元 tb 读延迟对齐 |
| 3 | 落 `rtl/cfg/satpu_cfg.yaml`（§3 表）+ 生成 sv | gen_cfg 输出 lint clean |
| 4 | `rtl/satpu_top.sv` 端口收敛 (clk/rst+sab)、内部加 §5 demux/mux + START 边沿 | tpu_top_tb 经 SAB 改写后 18/18 pass |
| 5 | 加 `tb/satpu_cfg_tb/`：register-walk 自检 + sab→子模块端口对照 | 单 tb pass |
| 6 | 新增 D9 进 [decisions.md](decisions.md)：顶层接口收敛 + RAM 段语义 + START 边沿在顶层 | doc 自洽 |

---

## 8. 关联

- 系统总览 / 维度符号 → [architecture.md](architecture.md)
- 控制器字段语义 → [controller_ws.md](controller_ws.md)
- MVP 范围 / APB 路线 → [scope.md](scope.md) §1
- gen_cfg 通用语法 → [rtl/cfg/script/README.md](../rtl/cfg/script/README.md)
- 决策日志（D9 待落） → [decisions.md](decisions.md)
